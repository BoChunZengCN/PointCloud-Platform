import hashlib
import errno
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


_CAPABILITY_CACHE: set[tuple[int, str]] = set()
_CAPABILITY_CACHE_LOCK = Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operation_dir(project_root: Path, operation_id: str) -> Path:
    operation_id = validate_identifier(operation_id, "operation_id")
    return Path(project_root) / "reports" / "model_matching_operations" / operation_id


def _idempotency_path(project_root: Path, idempotency_key: str) -> Path:
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return (
        Path(project_root)
        / "reports"
        / "model_matching_idempotency"
        / f"{key_hash}.json"
    )


def _operation_lock_path(project_root: Path, operation_id: str) -> Path:
    operation_id = validate_identifier(operation_id, "operation_id")
    return (
        Path(project_root)
        / "reports"
        / "model_matching_locks"
        / f"{operation_id}.lock"
    )


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_hash(event: dict) -> str:
    return _canonical_hash(
        {key: value for key, value in event.items() if key != "event_hash"}
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {
                errno.EBADF,
                errno.EINVAL,
                getattr(errno, "ENOTSUP", errno.EINVAL),
            }:
                raise
    finally:
        os.close(descriptor)


def _publish_no_replace(source: Path, destination: Path) -> None:
    os.link(source, destination)
    _fsync_directory(destination.parent)


def _claim_idempotency_index(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.parent / f".tmp-{uuid.uuid4().hex}"
    descriptor = os.open(
        temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        _publish_no_replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            # A published destination is already complete and authoritative;
            # an invisible temporary hard link is safe for later cleanup.
            pass


def _acquire_kernel_byte_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_kernel_byte_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("Audit coordination metadata write made no progress.")
        offset += written


@contextmanager
def _kernel_file_lock(
    lock_path: Path,
    *,
    owner_token: str | None = None,
    purpose: str,
) -> Iterator[dict]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    acquired = False
    yielded = False
    metadata = {
        "schema_version": "1.0",
        "owner_token": owner_token or uuid.uuid4().hex,
        "pid": os.getpid(),
        "purpose": purpose,
        "acquired_at": utc_now(),
    }
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        if os.fstat(descriptor).st_size == 0:
            _write_all(descriptor, b"\0")
            os.fsync(descriptor)
        try:
            _acquire_kernel_byte_lock(descriptor)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ModelMatchingError(
                    "operation_busy",
                    "Operation is currently being updated.",
                ) from exc
            raise
        acquired = True
        serialized = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        os.lseek(descriptor, 0, os.SEEK_SET)
        _write_all(descriptor, serialized)
        os.ftruncate(descriptor, len(serialized))
        os.fsync(descriptor)
        yielded = True
        yield metadata
    except ModelMatchingError:
        raise
    except OSError as exc:
        if yielded:
            raise
        raise ModelMatchingError(
            "audit_persistence_error",
            "Audit coordination storage is unavailable.",
        ) from exc
    finally:
        if descriptor is not None:
            if acquired:
                try:
                    _release_kernel_byte_lock(descriptor)
                except OSError:
                    pass
            os.close(descriptor)


def _probe_audit_storage_capabilities(project_root: Path) -> None:
    probe_root = (
        Path(project_root)
        / "reports"
        / ".model_matching_capability_probe"
    )
    probe_root.mkdir(parents=True, exist_ok=True)
    probe_id = uuid.uuid4().hex
    lock_path = probe_root / f"{probe_id}.lock"
    source_a = probe_root / f"{probe_id}.a"
    source_b = probe_root / f"{probe_id}.b"
    destination = probe_root / f"{probe_id}.destination"
    try:
        with _kernel_file_lock(lock_path, purpose="capability_probe"):
            with _busy_probe(lock_path):
                pass
        for source, payload in (
            (source_a, b"first"),
            (source_b, b"second"),
        ):
            with source.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        _publish_no_replace(source_a, destination)
        try:
            _publish_no_replace(source_b, destination)
        except FileExistsError:
            pass
        else:
            raise OSError("Hard-link publication overwrote an existing path.")
        if destination.read_bytes() != b"first":
            raise OSError("Hard-link no-replace verification failed.")
    finally:
        for path in (destination, source_b, source_a, lock_path):
            path.unlink(missing_ok=True)


@contextmanager
def _busy_probe(lock_path: Path) -> Iterator[None]:
    try:
        with _kernel_file_lock(lock_path, purpose="capability_probe_contender"):
            raise OSError("Kernel lock allowed a concurrent owner.")
    except ModelMatchingError as exc:
        if exc.code != "operation_busy":
            raise
    yield


def _require_audit_storage_capabilities(project_root: Path) -> None:
    cache_key = (os.getpid(), str(Path(project_root).resolve()))
    with _CAPABILITY_CACHE_LOCK:
        if cache_key in _CAPABILITY_CACHE:
            return
    try:
        _probe_audit_storage_capabilities(project_root)
    except Exception as exc:
        raise ModelMatchingError(
            "audit_persistence_error",
            "Audit storage lacks required kernel-lock or no-replace semantics.",
        ) from exc
    with _CAPABILITY_CACHE_LOCK:
        _CAPABILITY_CACHE.add(cache_key)


@contextmanager
def _operation_write_lock(
    project_root: Path,
    operation_id: str,
    *,
    owner_token: str | None = None,
    purpose: str = "mutation",
) -> Iterator[dict]:
    _require_audit_storage_capabilities(project_root)
    with _kernel_file_lock(
        _operation_lock_path(project_root, operation_id),
        owner_token=owner_token,
        purpose=purpose,
    ) as metadata:
        yield metadata


def load_operation(project_root: Path, operation_id: str) -> dict:
    path = _operation_dir(project_root, operation_id) / "operation.json"
    operation = json.loads(path.read_text(encoding="utf-8"))
    events = read_operation_events(project_root, operation_id)
    _require_valid_operation_chain(events)
    projected = _project_operation(operation, events)
    if projected != operation:
        write_json(projected, path)
    return projected


def read_operation_events(project_root: Path, operation_id: str) -> list[dict]:
    path = _operation_dir(project_root, operation_id) / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _require_valid_operation_chain(events: list[dict]) -> None:
    if not verify_operation_chain(events):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Operation audit chain integrity verification failed.",
        )


def _lifecycle_is_valid(events: list[dict]) -> bool:
    if not events:
        return True
    first_type = events[0].get("event_type")
    if first_type not in {"operation.started", "operation.start_failed"}:
        return False
    terminal = first_type == "operation.start_failed"
    for event in events[1:]:
        event_type = event.get("event_type")
        if event_type in {"operation.started", "operation.start_failed"}:
            return False
        if terminal:
            if event_type not in {
                "operation.replayed",
                "operation.idempotency_conflict",
            }:
                return False
            continue
        if event_type in {"operation.completed", "operation.failed"}:
            terminal = True
    return True


def _require_event_transition_allowed(
    events: list[dict], event_type: str
) -> None:
    event_types = [event["event_type"] for event in events]
    if not events:
        if event_type not in {"operation.started", "operation.start_failed"}:
            raise ModelMatchingError(
                "operation_immutable",
                "The first operation event must establish its start outcome.",
            )
        return
    if event_type in {"operation.started", "operation.start_failed"}:
        raise ModelMatchingError(
            "operation_immutable",
            "An operation start outcome can be recorded only once.",
        )
    if any(
        terminal in event_types
        for terminal in {
            "operation.start_failed",
            "operation.completed",
            "operation.failed",
        }
    ) and event_type not in {
        "operation.replayed",
        "operation.idempotency_conflict",
    }:
        raise ModelMatchingError(
            "operation_immutable",
            "Completed and failed operations are immutable.",
        )


def _project_operation(operation: dict, events: list[dict]) -> dict:
    terminal = next(
        (
            event
            for event in events
            if event["event_type"] in {"operation.completed", "operation.failed"}
        ),
        None,
    )
    if terminal is None:
        terminal = next(
            (
                event
                for event in events
                if event["event_type"] == "operation.start_failed"
            ),
            None,
        )
    if terminal is None:
        return operation
    if terminal["event_type"] == "operation.completed":
        return {
            **operation,
            "status": "completed",
            "completed_at": terminal["timestamp"],
            "result": dict(terminal["details"]["result"]),
            "error": None,
        }
    return {
        **operation,
        "status": "failed",
        "completed_at": terminal["timestamp"],
        "result": None,
        "error": {
            "code": terminal["details"]["code"],
            "message": terminal["details"]["message"],
        },
    }


def _read_operation_document(project_root: Path, operation_id: str) -> dict:
    path = _operation_dir(project_root, operation_id) / "operation.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _append_operation_event_locked(
    project_root: Path,
    operation_id: str,
    event_type: str,
    details: dict,
    principal: Principal | None = None,
) -> dict:
    events = read_operation_events(project_root, operation_id)
    _require_valid_operation_chain(events)
    _require_event_transition_allowed(events, event_type)
    operation = _read_operation_document(project_root, operation_id)
    actor_id = principal.actor_id if principal else operation["actor_id"]
    roles = sorted(principal.roles) if principal else operation["roles"]
    principal_source = (
        principal.source if principal else operation["principal_source"]
    )
    sequence = len(events) + 1
    event = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "operation_id": operation_id,
        "sequence": sequence,
        "event_type": event_type,
        "timestamp": utc_now(),
        "actor_id": actor_id,
        "roles": roles,
        "principal_source": principal_source,
        "previous_event_hash": events[-1]["event_hash"] if events else None,
        "details": dict(details),
    }
    event["event_hash"] = _event_hash(event)
    events_path = _operation_dir(project_root, operation_id) / "events.jsonl"
    serialized = json.dumps(
        event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with events_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def append_operation_event(
    project_root: Path,
    operation_id: str,
    event_type: str,
    details: dict,
) -> dict:
    operation_id = validate_identifier(operation_id, "operation_id")
    try:
        with _operation_write_lock(project_root, operation_id):
            return _append_operation_event_locked(
                project_root, operation_id, event_type, details
            )
    except ModelMatchingError as exc:
        if exc.code in {
            "operation_busy",
            "operation_immutable",
            "audit_integrity_error",
            "audit_persistence_error",
        }:
            _record_failed_mutation_safely(
                project_root,
                target_operation_id=operation_id,
                attempted_mutation=event_type,
                code=exc.code,
                message=str(exc),
            )
        raise


def _record_failed_mutation(
    project_root: Path,
    *,
    target_operation_id: str,
    attempted_mutation: str,
    code: str,
    message: str,
) -> None:
    audit_id = f"audit-{uuid.uuid4()}"
    principal = Principal("system-audit", frozenset(), "system")
    details = {
        "target_operation_id": target_operation_id,
        "attempted_mutation": attempted_mutation,
        "code": code,
        "message": message,
    }
    _start_operation(
        project_root,
        operation_id=audit_id,
        operation_type="audit.mutation_failure",
        principal=principal,
        request_id=audit_id,
        idempotency_key=audit_id,
        request_payload=details,
    )
    append_operation_event(
        project_root,
        audit_id,
        "operation.mutation_rejected",
        details,
    )
    fail_operation(
        project_root,
        audit_id,
        code,
        message,
        _audit_rejection=False,
    )


def _record_failed_mutation_safely(
    project_root: Path,
    *,
    target_operation_id: str,
    attempted_mutation: str,
    code: str,
    message: str,
) -> None:
    try:
        _record_failed_mutation(
            project_root,
            target_operation_id=target_operation_id,
            attempted_mutation=attempted_mutation,
            code=code,
            message=message,
        )
    except Exception:
        # Preserve the stable primary failure when audit storage itself is
        # unavailable. A successful capability probe is cached only after it
        # completes, so a transient failure can still be audited here.
        pass


def _append_event_with_lock(
    project_root: Path,
    operation_id: str,
    event_type: str,
    details: dict,
    *,
    principal: Principal | None = None,
) -> dict:
    with _operation_write_lock(project_root, operation_id):
        return _append_operation_event_locked(
            project_root,
            operation_id,
            event_type,
            details,
            principal,
        )


def _replay_or_reject(
    project_root: Path,
    *,
    idempotency_path: Path,
    operation_id: str,
    operation_type: str,
    principal: Principal,
    request_id: str,
    request_fingerprint: str,
) -> tuple[dict, bool]:
    deadline = time.monotonic() + 2
    index = _read_idempotency_index(idempotency_path)
    existing = _load_indexed_operation(project_root, index)
    events = read_operation_events(project_root, existing["operation_id"])
    if not events:
        with _operation_write_lock(
            project_root,
            existing["operation_id"],
            purpose="initializer_recovery",
        ):
            index = _read_idempotency_index(idempotency_path)
            existing = _load_indexed_operation(project_root, index)
            events = read_operation_events(
                project_root, existing["operation_id"]
            )
            _require_valid_operation_chain(events)
            if not events:
                _record_start_failure_locked(
                    project_root,
                    existing["operation_id"],
                    code="operation_start_interrupted",
                    message=(
                        "Operation start was interrupted before its first "
                        "audit event."
                    ),
                )
        existing = load_operation(project_root, existing["operation_id"])

    if (existing["operation_type"], existing["request_fingerprint"]) != (
        operation_type,
        request_fingerprint,
    ):
        _append_idempotency_event(
            project_root,
            existing["operation_id"],
            "operation.idempotency_conflict",
            {
                "requested_operation_id": operation_id,
                "request_id": request_id,
                "request_fingerprint": request_fingerprint,
                "actor_id": principal.actor_id,
                "roles": sorted(principal.roles),
                "principal_source": principal.source,
            },
            deadline,
            principal,
        )
        raise ModelMatchingError(
            "idempotency_conflict",
            "Idempotency key is already bound to a different request.",
        )
    _append_idempotency_event(
        project_root,
        existing["operation_id"],
        "operation.replayed",
        {
            "requested_operation_id": operation_id,
            "request_id": request_id,
            "actor_id": principal.actor_id,
            "roles": sorted(principal.roles),
            "principal_source": principal.source,
        },
        deadline,
        principal,
    )
    return existing, True


def _read_idempotency_index(path: Path) -> dict:
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelMatchingError(
            "audit_persistence_error",
            "Operation idempotency index could not be read.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Operation idempotency index is not valid JSON.",
        ) from exc
    if not isinstance(index, dict):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Operation idempotency index must be an object.",
        )
    for field in {
        "operation_id",
        "request_fingerprint",
        "initializer_owner_token",
    }:
        if not isinstance(index.get(field), str) or not index[field]:
            raise ModelMatchingError(
                "audit_integrity_error",
                "Operation idempotency index is incomplete.",
            )
    return index


def _load_indexed_operation(project_root: Path, index: dict) -> dict:
    try:
        indexed_operation_id = validate_identifier(
            index["operation_id"], "operation_id"
        )
        existing = load_operation(project_root, indexed_operation_id)
    except ModelMatchingError:
        raise
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Operation idempotency index references an invalid operation.",
        ) from exc
    _require_index_operation_binding(index, existing, indexed_operation_id)
    return existing


def _require_index_operation_binding(
    index: dict, operation: dict, indexed_operation_id: str
) -> None:
    if (
        operation.get("operation_id") != indexed_operation_id
        or operation.get("request_fingerprint")
        != index["request_fingerprint"]
        or operation.get("initializer_owner_token")
        != index["initializer_owner_token"]
    ):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Operation idempotency index does not match its operation.",
        )


def _append_idempotency_event(
    project_root: Path,
    operation_id: str,
    event_type: str,
    details: dict,
    deadline: float,
    principal: Principal,
) -> None:
    while True:
        try:
            _append_event_with_lock(
                project_root,
                operation_id,
                event_type,
                details,
                principal=principal,
            )
            return
        except ModelMatchingError as exc:
            if exc.code != "operation_busy" or time.monotonic() >= deadline:
                raise
        time.sleep(0.005)


def _discard_unstarted_operation(
    project_root: Path,
    operation_id: str,
    request_fingerprint: str,
) -> None:
    root = _operation_dir(project_root, operation_id)
    discarded = root.with_name(f".{root.name}.discarded-{uuid.uuid4().hex}")
    with _operation_write_lock(project_root, operation_id):
        operation = _read_operation_document(project_root, operation_id)
        events = read_operation_events(project_root, operation_id)
        if (
            operation["status"] != "running"
            or operation["request_fingerprint"] != request_fingerprint
            or events
        ):
            raise ModelMatchingError(
                "operation_immutable",
                "Only an unstarted losing operation may be discarded.",
            )
        os.replace(root, discarded)
    shutil.rmtree(discarded)


def _discard_empty_operation_root(
    project_root: Path, operation_id: str
) -> None:
    root = _operation_dir(project_root, operation_id)
    discarded = root.with_name(f".{root.name}.discarded-{uuid.uuid4().hex}")
    with _operation_write_lock(project_root, operation_id):
        remaining = {path.name for path in root.iterdir()}
        if remaining:
            raise ModelMatchingError(
                "operation_immutable",
                "A non-empty operation directory cannot be discarded.",
            )
        os.replace(root, discarded)
    shutil.rmtree(discarded)


def _recover_started_event_failure(
    project_root: Path,
    operation_id: str,
    error: BaseException,
) -> bool:
    return _record_start_failure(
        project_root,
        operation_id,
        code="operation_start_failed",
        message=f"{type(error).__name__}: {error}",
    )


def _record_start_failure(
    project_root: Path,
    operation_id: str,
    *,
    code: str,
    message: str,
) -> bool:
    with _operation_write_lock(project_root, operation_id):
        return _record_start_failure_locked(
            project_root,
            operation_id,
            code=code,
            message=message,
        )


def _record_start_failure_locked(
    project_root: Path,
    operation_id: str,
    *,
    code: str,
    message: str,
) -> bool:
    root = _operation_dir(project_root, operation_id)
    events = read_operation_events(project_root, operation_id)
    _require_valid_operation_chain(events)
    if any(event["event_type"] == "operation.started" for event in events):
        return True
    if any(event["event_type"] == "operation.start_failed" for event in events):
        return False
    failure = {"code": code, "message": message}
    event = _append_operation_event_locked(
        project_root,
        operation_id,
        "operation.start_failed",
        failure,
    )
    operation = _read_operation_document(project_root, operation_id)
    failed = {
        **operation,
        "status": "failed",
        "completed_at": event["timestamp"],
        "result": None,
        "error": failure,
    }
    write_json(failed, root / "operation.json")
    return False


def _start_operation(
    project_root: Path,
    *,
    operation_id: str,
    operation_type: str,
    principal: Principal,
    request_id: str,
    idempotency_key: str,
    request_payload: dict,
    _recover_start_failure: bool = True,
) -> tuple[dict, bool]:
    operation_id = validate_identifier(operation_id, "operation_id")
    request_id = validate_identifier(request_id, "request_id")
    idempotency_key = validate_identifier(idempotency_key, "idempotency_key")
    _require_audit_storage_capabilities(project_root)
    request_fingerprint = _canonical_hash(request_payload)
    idempotency_path = _idempotency_path(project_root, idempotency_key)
    replay_arguments = {
        "idempotency_path": idempotency_path,
        "operation_id": operation_id,
        "operation_type": operation_type,
        "principal": principal,
        "request_id": request_id,
        "request_fingerprint": request_fingerprint,
    }
    if idempotency_path.exists():
        return _replay_or_reject(project_root, **replay_arguments)

    root = _operation_dir(project_root, operation_id)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ModelMatchingError(
            "operation_exists", "Operation ID already exists."
        ) from exc
    initializer_owner_token = uuid.uuid4().hex
    operation = {
        "schema_version": "1.0",
        "operation_id": operation_id,
        "operation_type": operation_type,
        "status": "running",
        "actor_id": principal.actor_id,
        "roles": sorted(principal.roles),
        "principal_source": principal.source,
        "request_id": request_id,
        "idempotency_key_hash": hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest(),
        "request_fingerprint": request_fingerprint,
        "initializer_owner_token": initializer_owner_token,
        "started_at": utc_now(),
        "completed_at": None,
        "result": None,
        "error": None,
    }
    operation_path = root / "operation.json"
    try:
        write_json(operation, operation_path)
    except Exception as exc:
        if operation_path.exists():
            try:
                if json.loads(operation_path.read_text(encoding="utf-8")) == operation:
                    pass
                else:
                    raise ValueError("Persisted projection differs from request.")
            except (OSError, ValueError, json.JSONDecodeError) as read_exc:
                raise ModelMatchingError(
                    "operation_persistence_failed",
                    "Operation projection could not be persisted safely.",
                ) from read_exc
        else:
            _discard_empty_operation_root(project_root, operation_id)
            raise ModelMatchingError(
                "operation_persistence_failed",
                "Operation projection could not be persisted safely.",
            ) from exc
    claim_won = False
    try:
        with _operation_write_lock(
            project_root,
            operation_id,
            owner_token=initializer_owner_token,
            purpose="initializer",
        ):
            try:
                _claim_idempotency_index(
                    idempotency_path,
                    {
                        "operation_id": operation_id,
                        "request_fingerprint": request_fingerprint,
                        "initializer_owner_token": initializer_owner_token,
                    },
                )
                claim_won = True
            except FileExistsError:
                pass
            except OSError as exc:
                raise ModelMatchingError(
                    "audit_persistence_error",
                    "Operation idempotency index could not be published.",
                ) from exc
            if claim_won:
                _append_operation_event_locked(
                    project_root,
                    operation_id,
                    "operation.started",
                    {
                        "request_id": request_id,
                        "request_fingerprint": request_fingerprint,
                    },
                )
    except BaseException as exc:
        if not claim_won:
            try:
                _discard_unstarted_operation(
                    project_root, operation_id, request_fingerprint
                )
            except Exception:
                pass
            raise
        if _recover_start_failure and _recover_started_event_failure(
            project_root, operation_id, exc
        ):
            return operation, False
        raise
    if not claim_won:
        _discard_unstarted_operation(
            project_root, operation_id, request_fingerprint
        )
        return _replay_or_reject(project_root, **replay_arguments)
    return operation, False


def start_operation(
    project_root: Path,
    *,
    operation_id: str,
    operation_type: str,
    principal: Principal,
    request_id: str,
    idempotency_key: str,
    request_payload: dict,
) -> tuple[dict, bool]:
    try:
        return _start_operation(
            project_root,
            operation_id=operation_id,
            operation_type=operation_type,
            principal=principal,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    except ModelMatchingError as exc:
        if exc.code in {
            "operation_busy",
            "operation_immutable",
            "operation_exists",
            "operation_persistence_failed",
            "audit_integrity_error",
            "audit_persistence_error",
        }:
            _record_failed_mutation_safely(
                project_root,
                target_operation_id=operation_id,
                attempted_mutation="operation.started",
                code=exc.code,
                message=str(exc),
            )
        raise


def _require_running(operation: dict) -> None:
    if operation["status"] != "running":
        raise ModelMatchingError(
            "operation_immutable", "Completed and failed operations are immutable."
        )


def complete_operation(
    project_root: Path,
    operation_id: str,
    result: dict,
    *,
    _audit_rejection: bool = True,
) -> dict:
    operation_id = validate_identifier(operation_id, "operation_id")
    root = _operation_dir(project_root, operation_id)
    try:
        with _operation_write_lock(project_root, operation_id):
            operation = load_operation(project_root, operation_id)
            _require_running(operation)
            event = _append_operation_event_locked(
                project_root,
                operation_id,
                "operation.completed",
                {"result": dict(result)},
            )
            completed = {
                **operation,
                "status": "completed",
                "completed_at": event["timestamp"],
                "result": dict(result),
                "error": None,
            }
            write_json(completed, root / "operation.json")
    except ModelMatchingError as exc:
        if _audit_rejection and exc.code in {
            "operation_busy",
            "operation_immutable",
            "audit_integrity_error",
            "audit_persistence_error",
        }:
            _record_failed_mutation_safely(
                project_root,
                target_operation_id=operation_id,
                attempted_mutation="operation.completed",
                code=exc.code,
                message=str(exc),
            )
        raise
    return completed


def fail_operation(
    project_root: Path,
    operation_id: str,
    code: str,
    message: str,
    *,
    _audit_rejection: bool = True,
) -> dict:
    operation_id = validate_identifier(operation_id, "operation_id")
    root = _operation_dir(project_root, operation_id)
    try:
        with _operation_write_lock(project_root, operation_id):
            operation = load_operation(project_root, operation_id)
            _require_running(operation)
            error = {"code": code, "message": message}
            event = _append_operation_event_locked(
                project_root, operation_id, "operation.failed", error
            )
            failed = {
                **operation,
                "status": "failed",
                "completed_at": event["timestamp"],
                "result": None,
                "error": error,
            }
            write_json(failed, root / "operation.json")
    except ModelMatchingError as exc:
        if _audit_rejection and exc.code in {
            "operation_busy",
            "operation_immutable",
            "audit_integrity_error",
            "audit_persistence_error",
        }:
            _record_failed_mutation_safely(
                project_root,
                target_operation_id=operation_id,
                attempted_mutation="operation.failed",
                code=exc.code,
                message=str(exc),
            )
        raise
    return failed


def verify_operation_chain(events: list[dict]) -> bool:
    previous_hash = None
    for expected_sequence, event in enumerate(events, start=1):
        if (
            event.get("sequence") != expected_sequence
            or event.get("previous_event_hash") != previous_hash
            or event.get("event_hash") != _event_hash(event)
        ):
            return False
        previous_hash = event["event_hash"]
    return _lifecycle_is_valid(events)


def _denied_recovery_path(project_root: Path, operation_id: str) -> Path:
    return (
        Path(project_root)
        / "reports"
        / "model_matching_denied_recovery"
        / f"{validate_identifier(operation_id, 'operation_id')}.json"
    )


def _denied_marker_lock_path(project_root: Path, marker_name: str) -> Path:
    marker_hash = hashlib.sha256(marker_name.encode("utf-8")).hexdigest()
    return (
        Path(project_root)
        / "reports"
        / "model_matching_denied_recovery_locks"
        / f"{marker_hash}.lock"
    )


def _ensure_denied_initial_operation(
    project_root: Path,
    recovery: dict,
) -> None:
    operation_id = validate_identifier(
        recovery["operation_id"], "operation_id"
    )
    root = _operation_dir(project_root, operation_id)
    operation_path = root / "operation.json"
    principal = Principal("system-api", frozenset(), "system")
    request_payload = {
        "route": recovery["route"],
        "reason": recovery["reason"],
        "token_fingerprint": recovery["token_fingerprint"],
    }
    if not operation_path.exists():
        if root.exists():
            _discard_empty_operation_root(project_root, operation_id)
        try:
            _start_operation(
                project_root,
                operation_id=operation_id,
                operation_type="security.permission_denied",
                principal=principal,
                request_id=recovery["request_id"],
                idempotency_key=operation_id,
                request_payload=request_payload,
                _recover_start_failure=False,
            )
        except Exception:
            if not operation_path.exists():
                raise
    expected_fingerprint = _canonical_hash(request_payload)
    idempotency_path = _idempotency_path(project_root, operation_id)
    with _operation_write_lock(
        project_root, operation_id, purpose="denied_initializer_recovery"
    ):
        operation = _read_operation_document(project_root, operation_id)
        if (
            operation.get("operation_type") != "security.permission_denied"
            or operation.get("request_id") != recovery["request_id"]
            or operation.get("request_fingerprint") != expected_fingerprint
            or not isinstance(operation.get("initializer_owner_token"), str)
        ):
            raise ModelMatchingError(
                "audit_integrity_error",
                "Denied recovery operation does not match its marker.",
            )
        if idempotency_path.exists():
            index = _read_idempotency_index(idempotency_path)
            _require_index_operation_binding(index, operation, operation_id)
        else:
            try:
                _claim_idempotency_index(
                    idempotency_path,
                    {
                        "operation_id": operation_id,
                        "request_fingerprint": expected_fingerprint,
                        "initializer_owner_token": operation[
                            "initializer_owner_token"
                        ],
                    },
                )
            except FileExistsError:
                index = _read_idempotency_index(idempotency_path)
                _require_index_operation_binding(
                    index, operation, operation_id
                )
        events = read_operation_events(project_root, operation_id)
        _require_valid_operation_chain(events)
        if not events:
            _append_operation_event_locked(
                project_root,
                operation_id,
                "operation.started",
                {
                    "request_id": recovery["request_id"],
                    "request_fingerprint": expected_fingerprint,
                },
            )


def _ensure_denied_operation(
    project_root: Path,
    operation_id: str,
    denial_details: dict,
) -> None:
    root = _operation_dir(project_root, operation_id)
    principal = Principal("system-api", frozenset(), "system")
    with _operation_write_lock(project_root, operation_id):
        events = read_operation_events(project_root, operation_id)
        _require_valid_operation_chain(events)
        if not any(
            event["event_type"] == "security.permission_denied"
            for event in events
        ):
            _append_operation_event_locked(
                project_root,
                operation_id,
                "security.permission_denied",
                denial_details,
                principal,
            )
            events = read_operation_events(project_root, operation_id)
        if not any(
            event["event_type"] == "operation.failed" for event in events
        ):
            _append_operation_event_locked(
                project_root,
                operation_id,
                "operation.failed",
                {
                    "code": "permission_denied",
                    "message": "Request was denied.",
                },
                principal,
            )
            events = read_operation_events(project_root, operation_id)
        operation = _read_operation_document(project_root, operation_id)
        failed = _project_operation(operation, events)
        write_json(failed, root / "operation.json")


def _recover_denied_entry(
    project_root: Path,
    recovery: dict,
) -> None:
    last_error: Exception | None = None
    for _ in range(4):
        try:
            _ensure_denied_initial_operation(project_root, recovery)
            _ensure_denied_operation(
                project_root,
                recovery["operation_id"],
                {
                    "request_id": recovery["request_id"],
                    "route": recovery["route"],
                    "code": "permission_denied",
                    "reason": recovery["reason"],
                    "token_fingerprint": recovery["token_fingerprint"],
                },
            )
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.005)
    assert last_error is not None
    raise last_error


def recover_denied_operations(project_root: Path) -> list[str]:
    _require_audit_storage_capabilities(project_root)
    recovery_root = (
        Path(project_root) / "reports" / "model_matching_denied_recovery"
    )
    if not recovery_root.exists():
        return []
    recovered = []
    for recovery_path in sorted(recovery_root.glob("*.json")):
        try:
            with _kernel_file_lock(
                _denied_marker_lock_path(project_root, recovery_path.name),
                purpose="denied_recovery_claim",
            ):
                if not recovery_path.exists():
                    continue
                try:
                    recovery = _read_denied_recovery_marker(recovery_path)
                except ModelMatchingError as exc:
                    _record_denied_recovery_failure(
                        project_root, recovery_path, exc.code, str(exc)
                    )
                    continue
                try:
                    _recover_denied_entry(project_root, recovery)
                except Exception as exc:
                    _record_denied_recovery_failure(
                        project_root,
                        recovery_path,
                        (
                            exc.code
                            if isinstance(exc, ModelMatchingError)
                            else "audit_persistence_error"
                        ),
                        str(exc),
                    )
                    continue
                recovery_path.unlink(missing_ok=True)
                recovered.append(recovery["operation_id"])
        except ModelMatchingError as exc:
            if exc.code != "operation_busy":
                raise
    return recovered


def _read_denied_recovery_marker(recovery_path: Path) -> dict:
    try:
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Denied recovery marker is unreadable or invalid.",
        ) from exc
    if not isinstance(recovery, dict):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Denied recovery marker must be an object.",
        )
    try:
        operation_id = validate_identifier(
            recovery["operation_id"], "operation_id"
        )
        for field in {"request_id", "route", "reason", "token_fingerprint"}:
            if not isinstance(recovery[field], str):
                raise KeyError(field)
    except (KeyError, ModelMatchingError) as exc:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Denied recovery marker is incomplete.",
        ) from exc
    if recovery_path.stem != operation_id:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Denied recovery index does not match its operation ID.",
        )
    return recovery


def _record_denied_recovery_failure(
    project_root: Path,
    recovery_path: Path,
    code: str,
    message: str,
) -> None:
    try:
        raw = recovery_path.read_bytes()
    except OSError:
        raw = recovery_path.name.encode("utf-8")
    fingerprint = hashlib.sha256(raw).hexdigest()
    report_path = (
        recovery_path.parent
        / "errors"
        / f"{recovery_path.stem}-{fingerprint[:16]}.json"
    )
    report = {
        "schema_version": "1.0",
        "marker_name": recovery_path.name,
        "marker_fingerprint": fingerprint,
        "code": code,
        "message": message,
        "recorded_at": utc_now(),
    }
    try:
        _claim_idempotency_index(report_path, report)
    except FileExistsError:
        return
    except OSError:
        return
    _record_failed_mutation_safely(
        project_root,
        target_operation_id=recovery_path.stem,
        attempted_mutation="security.permission_denied.recovery",
        code=code,
        message=message,
    )


def record_denied_operation(
    project_root: Path,
    *,
    request_id: str,
    route: str,
    token: str | None,
    reason: str,
) -> str:
    _require_audit_storage_capabilities(project_root)
    suffix = str(uuid.uuid4())
    operation_id = f"denied-{suffix}"
    token_fingerprint = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    recovery = {
        "schema_version": "1.0",
        "operation_id": operation_id,
        "request_id": request_id,
        "route": route,
        "reason": reason,
        "token_fingerprint": token_fingerprint,
    }
    recovery_path = _denied_recovery_path(project_root, operation_id)
    with _kernel_file_lock(
        _denied_marker_lock_path(project_root, recovery_path.name),
        purpose="denied_recovery_claim",
    ):
        try:
            _claim_idempotency_index(recovery_path, recovery)
        except OSError as exc:
            raise ModelMatchingError(
                "audit_persistence_error",
                "Denied recovery marker could not be published.",
            ) from exc
        _recover_denied_entry(project_root, recovery)
        recovery_path.unlink(missing_ok=True)
    return operation_id
