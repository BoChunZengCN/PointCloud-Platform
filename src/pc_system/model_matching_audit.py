import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


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


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_hash(event: dict) -> str:
    return _canonical_hash(
        {key: value for key, value in event.items() if key != "event_hash"}
    )


def _claim_idempotency_index(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
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
    except BaseException:
        path.unlink(missing_ok=True)
        raise


@contextmanager
def _operation_write_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".write.lock"
    owner_token = uuid.uuid4().hex
    try:
        descriptor = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
        )
    except FileExistsError as exc:
        raise ModelMatchingError(
            "operation_busy", "Operation is currently being updated."
        ) from exc
    try:
        os.write(descriptor, owner_token.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        yield
    finally:
        try:
            recorded_owner = lock_path.read_text(encoding="ascii")
        except FileNotFoundError:
            recorded_owner = None
        if recorded_owner == owner_token:
            lock_path.unlink(missing_ok=True)


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
    root = _operation_dir(project_root, operation_id)
    try:
        with _operation_write_lock(root):
            return _append_operation_event_locked(
                project_root, operation_id, event_type, details
            )
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            _record_failed_mutation(
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


def _append_event_with_lock(
    project_root: Path,
    operation_id: str,
    event_type: str,
    details: dict,
    *,
    principal: Principal | None = None,
) -> dict:
    root = _operation_dir(project_root, operation_id)
    with _operation_write_lock(root):
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
    indexed_operation_id: str | None = None
    while True:
        try:
            index = json.loads(idempotency_path.read_text(encoding="utf-8"))
            indexed_operation_id = index["operation_id"]
            existing = load_operation(project_root, indexed_operation_id)
            events = read_operation_events(project_root, existing["operation_id"])
            if events and events[0]["event_type"] in {
                "operation.started",
                "operation.start_failed",
            }:
                break
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        if time.monotonic() >= deadline:
            if indexed_operation_id is None:
                raise ModelMatchingError(
                    "operation_busy",
                    "Operation idempotency claim is incomplete.",
                )
            _record_start_failure(
                project_root,
                indexed_operation_id,
                code="operation_start_interrupted",
                message=(
                    "Operation start was interrupted before its first audit event."
                ),
            )
            continue
        time.sleep(0.005)

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
    with _operation_write_lock(root):
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


def _discard_empty_operation_root(root: Path) -> None:
    discarded = root.with_name(f".{root.name}.discarded-{uuid.uuid4().hex}")
    with _operation_write_lock(root):
        remaining = {
            path.name for path in root.iterdir() if path.name != ".write.lock"
        }
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
    root = _operation_dir(project_root, operation_id)
    with _operation_write_lock(root):
        events = read_operation_events(project_root, operation_id)
        _require_valid_operation_chain(events)
        if any(event["event_type"] == "operation.started" for event in events):
            return True
        if any(event["event_type"] == "operation.start_failed" for event in events):
            return False
        failure = {
            "code": code,
            "message": message,
        }
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
) -> tuple[dict, bool]:
    operation_id = validate_identifier(operation_id, "operation_id")
    request_id = validate_identifier(request_id, "request_id")
    idempotency_key = validate_identifier(idempotency_key, "idempotency_key")
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
            _discard_empty_operation_root(root)
            raise ModelMatchingError(
                "operation_persistence_failed",
                "Operation projection could not be persisted safely.",
            ) from exc
    try:
        _claim_idempotency_index(
            idempotency_path,
            {
                "operation_id": operation_id,
                "request_fingerprint": request_fingerprint,
            },
        )
    except FileExistsError:
        _discard_unstarted_operation(
            project_root, operation_id, request_fingerprint
        )
        return _replay_or_reject(project_root, **replay_arguments)
    try:
        _append_event_with_lock(
            project_root,
            operation_id,
            "operation.started",
            {
                "request_id": request_id,
                "request_fingerprint": request_fingerprint,
            },
        )
    except BaseException as exc:
        if _recover_started_event_failure(project_root, operation_id, exc):
            return operation, False
        raise
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
        }:
            _record_failed_mutation(
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
        with _operation_write_lock(root):
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
        }:
            _record_failed_mutation(
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
        with _operation_write_lock(root):
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
        }:
            _record_failed_mutation(
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
    return True


def _denied_recovery_path(project_root: Path, operation_id: str) -> Path:
    return (
        Path(project_root)
        / "reports"
        / "model_matching_denied_recovery"
        / f"{validate_identifier(operation_id, 'operation_id')}.json"
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
            _discard_empty_operation_root(root)
        try:
            _start_operation(
                project_root,
                operation_id=operation_id,
                operation_type="security.permission_denied",
                principal=principal,
                request_id=recovery["request_id"],
                idempotency_key=operation_id,
                request_payload=request_payload,
            )
        except Exception:
            if not operation_path.exists():
                raise

    idempotency_path = _idempotency_path(project_root, operation_id)
    if not idempotency_path.exists():
        operation = _read_operation_document(project_root, operation_id)
        try:
            _claim_idempotency_index(
                idempotency_path,
                {
                    "operation_id": operation_id,
                    "request_fingerprint": operation["request_fingerprint"],
                },
            )
        except FileExistsError:
            pass
    events = read_operation_events(project_root, operation_id)
    _require_valid_operation_chain(events)
    if not events:
        _record_start_failure(
            project_root,
            operation_id,
            code="operation_start_interrupted",
            message=(
                "Denied operation start was interrupted before its first "
                "audit event."
            ),
        )


def _ensure_denied_operation(
    project_root: Path,
    operation_id: str,
    denial_details: dict,
) -> None:
    root = _operation_dir(project_root, operation_id)
    principal = Principal("system-api", frozenset(), "system")
    with _operation_write_lock(root):
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
    recovery_root = (
        Path(project_root) / "reports" / "model_matching_denied_recovery"
    )
    if not recovery_root.exists():
        return []
    recovered = []
    for recovery_path in sorted(recovery_root.glob("*.json")):
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        operation_id = validate_identifier(
            recovery["operation_id"], "operation_id"
        )
        if recovery_path.stem != operation_id:
            raise ModelMatchingError(
                "audit_integrity_error",
                "Denied recovery index does not match its operation ID.",
            )
        _recover_denied_entry(project_root, recovery)
        recovery_path.unlink()
        recovered.append(operation_id)
    return recovered


def record_denied_operation(
    project_root: Path,
    *,
    request_id: str,
    route: str,
    token: str | None,
    reason: str,
) -> str:
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
    _claim_idempotency_index(recovery_path, recovery)
    _recover_denied_entry(project_root, recovery)
    recovery_path.unlink()
    return operation_id
