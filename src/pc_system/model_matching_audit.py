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
    try:
        descriptor = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
        )
    except FileExistsError as exc:
        raise ModelMatchingError(
            "operation_busy", "Operation is currently being updated."
        ) from exc
    os.close(descriptor)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def load_operation(project_root: Path, operation_id: str) -> dict:
    path = _operation_dir(project_root, operation_id) / "operation.json"
    return json.loads(path.read_text(encoding="utf-8"))


def read_operation_events(project_root: Path, operation_id: str) -> list[dict]:
    path = _operation_dir(project_root, operation_id) / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _append_operation_event_locked(
    project_root: Path,
    operation_id: str,
    event_type: str,
    details: dict,
) -> dict:
    events = read_operation_events(project_root, operation_id)
    operation = load_operation(project_root, operation_id)
    sequence = len(events) + 1
    event = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "operation_id": operation_id,
        "sequence": sequence,
        "event_type": event_type,
        "timestamp": utc_now(),
        "actor_id": operation["actor_id"],
        "roles": operation["roles"],
        "principal_source": operation["principal_source"],
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
    with _operation_write_lock(root):
        return _append_operation_event_locked(
            project_root, operation_id, event_type, details
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
    while True:
        try:
            index = json.loads(idempotency_path.read_text(encoding="utf-8"))
            existing = load_operation(project_root, index["operation_id"])
            events = read_operation_events(project_root, existing["operation_id"])
            if events and events[0]["event_type"] == "operation.started":
                break
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise ModelMatchingError(
                "operation_busy", "Operation is currently being initialized."
            )
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
            },
            deadline,
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
        },
        deadline,
    )
    return existing, True


def _append_idempotency_event(
    project_root: Path,
    operation_id: str,
    event_type: str,
    details: dict,
    deadline: float,
) -> None:
    while True:
        try:
            append_operation_event(
                project_root, operation_id, event_type, details
            )
            return
        except ModelMatchingError as exc:
            if exc.code != "operation_busy" or time.monotonic() >= deadline:
                raise
        time.sleep(0.005)


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
    root.mkdir(parents=True, exist_ok=False)
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
    write_json(operation, root / "operation.json")
    try:
        _claim_idempotency_index(
            idempotency_path,
            {
                "operation_id": operation_id,
                "request_fingerprint": request_fingerprint,
            },
        )
    except FileExistsError:
        shutil.rmtree(root)
        return _replay_or_reject(project_root, **replay_arguments)
    append_operation_event(
        project_root,
        operation_id,
        "operation.started",
        {
            "request_id": request_id,
            "request_fingerprint": request_fingerprint,
        },
    )
    return operation, False


def _require_running(operation: dict) -> None:
    if operation["status"] != "running":
        raise ModelMatchingError(
            "operation_immutable", "Completed and failed operations are immutable."
        )


def complete_operation(
    project_root: Path, operation_id: str, result: dict
) -> dict:
    operation_id = validate_identifier(operation_id, "operation_id")
    root = _operation_dir(project_root, operation_id)
    with _operation_write_lock(root):
        operation = load_operation(project_root, operation_id)
        _require_running(operation)
        _append_operation_event_locked(
            project_root,
            operation_id,
            "operation.completed",
            {"result": dict(result)},
        )
        completed = {
            **operation,
            "status": "completed",
            "completed_at": utc_now(),
            "result": dict(result),
            "error": None,
        }
        write_json(completed, root / "operation.json")
    return completed


def fail_operation(
    project_root: Path,
    operation_id: str,
    code: str,
    message: str,
) -> dict:
    operation_id = validate_identifier(operation_id, "operation_id")
    root = _operation_dir(project_root, operation_id)
    with _operation_write_lock(root):
        operation = load_operation(project_root, operation_id)
        _require_running(operation)
        error = {"code": code, "message": message}
        _append_operation_event_locked(
            project_root, operation_id, "operation.failed", error
        )
        failed = {
            **operation,
            "status": "failed",
            "completed_at": utc_now(),
            "result": None,
            "error": error,
        }
        write_json(failed, root / "operation.json")
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
    principal = Principal("system-api", frozenset(), "system")
    start_operation(
        project_root,
        operation_id=operation_id,
        operation_type="security.permission_denied",
        principal=principal,
        request_id=request_id,
        idempotency_key=operation_id,
        request_payload={
            "route": route,
            "reason": reason,
            "token_fingerprint": token_fingerprint,
        },
    )
    append_operation_event(
        project_root,
        operation_id,
        "security.permission_denied",
        {
            "request_id": request_id,
            "route": route,
            "code": "permission_denied",
            "reason": reason,
            "token_fingerprint": token_fingerprint,
        },
    )
    fail_operation(
        project_root,
        operation_id,
        "permission_denied",
        "Request was denied.",
    )
    return operation_id
