import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.segmentation_corrections import (
    CorrectionError,
    _assignment_fingerprint,
    _lock_expiry,
    _object_document,
    _session_dir,
    _iso_now,
    load_correction_baseline,
    load_correction_session,
)


SUPPORTED_OPERATIONS = {
    "confirm",
    "merge",
    "split",
    "relabel",
    "mark_noise",
    "restore_from_noise",
    "undo",
    "redo",
    "restore",
}


def read_correction_events(
    project_root: Path, asset_id: str, session_id: str
) -> list[dict]:
    path = _session_dir(project_root, asset_id, session_id) / "events.jsonl"
    if not path.is_file():
        raise CorrectionError("session_not_found", f"Session not found: {session_id}")
    events = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise CorrectionError(
                    "invalid_event_log",
                    f"Event on line {line_number} must be an object.",
                )
            events.append(item)
    except json.JSONDecodeError as exc:
        raise CorrectionError("invalid_event_log", str(exc)) from exc
    return events


def _exact_indices(value: object) -> list[int]:
    if not isinstance(value, list) or not value:
        raise CorrectionError(
            "invalid_point_selection",
            "source_point_indices must be a non-empty array.",
        )
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in value
    ):
        raise CorrectionError(
            "invalid_point_selection",
            "source_point_indices must contain non-negative integers.",
        )
    if len(set(value)) != len(value):
        raise CorrectionError(
            "invalid_point_selection",
            "source_point_indices must not contain duplicates.",
        )
    return sorted(value)


def _instance_ids(value: object, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise CorrectionError(
            "invalid_instance_selection",
            f"instance_ids must contain at least {minimum} values.",
        )
    try:
        result = [validate_identifier(item, "instance_id") for item in value]
    except (TypeError, ValueError) as exc:
        raise CorrectionError("invalid_instance_selection", str(exc)) from exc
    if len(set(result)) != len(result):
        raise CorrectionError(
            "invalid_instance_selection", "instance_ids must be distinct."
        )
    return result


def _active_instances(assignments: dict[int, dict]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for index, item in assignments.items():
        if not item["is_noise"]:
            groups.setdefault(item["instance_id"], []).append(index)
    return groups


def _apply_label_operation(
    assignments: dict[int, dict],
    baseline: dict[int, dict],
    operation: dict,
    confirmed: set[str],
) -> None:
    operation_type = operation["type"]
    groups = _active_instances(assignments)
    if operation_type == "confirm":
        instance_ids = _instance_ids(operation.get("instance_ids"))
        missing = [item for item in instance_ids if item not in groups]
        if missing:
            raise CorrectionError(
                "instance_not_found",
                f"Active instances not found: {', '.join(missing)}",
            )
        confirmed.update(instance_ids)
        return
    if operation_type == "merge":
        try:
            instance_ids = _instance_ids(
                operation.get("instance_ids"), minimum=2
            )
        except CorrectionError as exc:
            raise CorrectionError("invalid_merge", str(exc)) from exc
        target = operation.get("target_instance_id")
        try:
            target = validate_identifier(target, "target_instance_id")
        except (TypeError, ValueError) as exc:
            raise CorrectionError("invalid_merge", str(exc)) from exc
        if target not in instance_ids or any(item not in groups for item in instance_ids):
            raise CorrectionError(
                "invalid_merge",
                "Merge instances must be active and include the target.",
            )
        target_class = next(
            assignments[index]["class_id"] for index in groups[target]
        )
        for item in assignments.values():
            if item["instance_id"] in instance_ids and not item["is_noise"]:
                item["instance_id"] = target
                item["class_id"] = target_class
        confirmed.difference_update(instance_ids)
        return
    if operation_type == "split":
        try:
            instance_id = validate_identifier(
                operation.get("instance_id"), "instance_id"
            )
            new_instance_id = validate_identifier(
                operation.get("new_instance_id"), "new_instance_id"
            )
            indices = _exact_indices(operation.get("source_point_indices"))
        except (TypeError, ValueError, CorrectionError) as exc:
            raise CorrectionError("invalid_split", str(exc)) from exc
        members = set(groups.get(instance_id, []))
        selection = set(indices)
        if (
            not members
            or not selection < members
            or new_instance_id in groups
            or new_instance_id == instance_id
        ):
            raise CorrectionError(
                "invalid_split",
                "Split selection must be a proper subset of one active object.",
            )
        for index in indices:
            assignments[index]["instance_id"] = new_instance_id
        confirmed.discard(instance_id)
        confirmed.discard(new_instance_id)
        return
    if operation_type == "relabel":
        instance_ids = _instance_ids(operation.get("instance_ids"))
        try:
            class_id = validate_identifier(operation.get("class_id"), "class_id")
        except (TypeError, ValueError) as exc:
            raise CorrectionError("invalid_class_id", str(exc)) from exc
        if any(item not in groups for item in instance_ids):
            raise CorrectionError(
                "instance_not_found", "Relabel requires active instances."
            )
        for item in assignments.values():
            if item["instance_id"] in instance_ids and not item["is_noise"]:
                item["class_id"] = class_id
        confirmed.difference_update(instance_ids)
        return
    if operation_type == "mark_noise":
        indices = _exact_indices(operation.get("source_point_indices"))
        if any(index not in assignments for index in indices):
            raise CorrectionError(
                "invalid_point_selection", "Selected point is outside the session."
            )
        affected = {assignments[index]["instance_id"] for index in indices}
        for index in indices:
            assignments[index].update(
                {"instance_id": "noise", "class_id": "noise", "is_noise": True}
            )
        confirmed.difference_update(affected)
        return
    if operation_type == "restore_from_noise":
        indices = _exact_indices(operation.get("source_point_indices"))
        try:
            target = validate_identifier(
                operation.get("target_instance_id"), "target_instance_id"
            )
        except (TypeError, ValueError) as exc:
            raise CorrectionError("invalid_noise_restore", str(exc)) from exc
        if target not in groups:
            raise CorrectionError(
                "invalid_noise_restore", "Noise restore target must be active."
            )
        if any(
            index not in assignments or not assignments[index]["is_noise"]
            for index in indices
        ):
            raise CorrectionError(
                "invalid_noise_restore", "Only active noise points can be restored."
            )
        target_class = assignments[groups[target][0]]["class_id"]
        for index in indices:
            assignments[index].update(
                {
                    "instance_id": target,
                    "class_id": target_class,
                    "is_noise": False,
                }
            )
        confirmed.discard(target)
        return
    if operation_type == "restore":
        scope = operation.get("scope")
        if scope == "all":
            assignments.clear()
            assignments.update(
                {index: dict(item) for index, item in baseline.items()}
            )
            confirmed.clear()
            return
        if scope == "points":
            indices = _exact_indices(operation.get("source_point_indices"))
        elif scope == "instances":
            instance_ids = set(_instance_ids(operation.get("instance_ids")))
            indices = [
                index
                for index, item in assignments.items()
                if item["instance_id"] in instance_ids
                or baseline[index]["instance_id"] in instance_ids
            ]
            if not indices:
                raise CorrectionError(
                    "invalid_restore", "No points match the requested instances."
                )
        else:
            raise CorrectionError(
                "invalid_restore",
                "Restore scope must be all, points, or instances.",
            )
        if any(index not in assignments for index in indices):
            raise CorrectionError(
                "invalid_restore", "Restore selection is outside the session."
            )
        affected = {assignments[index]["instance_id"] for index in indices}
        for index in indices:
            assignments[index] = dict(baseline[index])
        confirmed.difference_update(affected)
        return
    raise CorrectionError(
        "unsupported_operation", f"Unsupported correction operation: {operation_type}"
    )


def materialize_correction(baseline: dict, events: list[dict]) -> dict:
    """Replay events from an immutable baseline into one deterministic draft."""

    baseline_items = baseline.get("assignments")
    if not isinstance(baseline_items, list):
        raise CorrectionError(
            "invalid_baseline", "Baseline assignments must be an array."
        )
    baseline_map = {
        int(item["source_point_index"]): dict(item) for item in baseline_items
    }
    active_events: list[dict] = []
    redo_events: list[dict] = []
    for event in events:
        operation = event.get("operation")
        if not isinstance(operation, dict):
            raise CorrectionError("invalid_event_log", "Event operation is invalid.")
        operation_type = operation.get("type")
        if operation_type == "undo":
            if active_events:
                redo_events.append(active_events.pop())
        elif operation_type == "redo":
            if redo_events:
                active_events.append(redo_events.pop())
        else:
            active_events.append(event)
            redo_events.clear()
    assignments = {index: dict(item) for index, item in baseline_map.items()}
    confirmed: set[str] = set()
    for event in active_events:
        _apply_label_operation(
            assignments, baseline_map, event["operation"], confirmed
        )
    ordered = [assignments[index] for index in sorted(assignments)]
    return {
        "schema_version": "1.0",
        "asset_id": baseline.get("asset_id"),
        "session_id": baseline.get("session_id"),
        "point_count": len(ordered),
        "source_fingerprint": baseline.get("source_fingerprint"),
        "baseline_fingerprint": _assignment_fingerprint(baseline_items),
        "draft_fingerprint": _assignment_fingerprint(ordered),
        "assignments": ordered,
        "confirmed_instance_ids": sorted(confirmed),
        "undo_available": bool(active_events),
        "redo_available": bool(redo_events),
    }


def _normalize_operation(operation: dict, next_revision: int) -> dict:
    if not isinstance(operation, dict):
        raise CorrectionError("invalid_operation", "operation must be an object.")
    operation_type = operation.get("type")
    if operation_type not in SUPPORTED_OPERATIONS:
        raise CorrectionError(
            "unsupported_operation",
            f"Unsupported correction operation: {operation_type}",
        )
    normalized = dict(operation)
    if operation_type == "split":
        normalized["new_instance_id"] = f"split-{next_revision:04d}"
    return normalized


def _lock_is_active(session: dict) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(session["lock_expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CorrectionError("invalid_session_lock", str(exc)) from exc
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def apply_correction_event(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    actor: str,
    expected_revision: int,
    client_request_id: str,
    operation: dict,
) -> dict:
    """Validate, append, and materialize one correction event."""

    asset_id = validate_identifier(asset_id, "asset_id")
    session_id = validate_identifier(session_id, "session_id")
    try:
        client_request_id = validate_identifier(
            client_request_id, "client_request_id"
        )
    except (TypeError, ValueError) as exc:
        raise CorrectionError("invalid_client_request_id", str(exc)) from exc
    if not isinstance(actor, str) or not actor.strip():
        raise CorrectionError("invalid_actor", "actor must be a non-empty string.")
    actor = actor.strip()
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise CorrectionError(
            "invalid_revision", "expected_revision must be a non-negative integer."
        )
    session_dir = _session_dir(project_root, asset_id, session_id)
    lock_path = session_dir / ".write.lock"
    try:
        lock_handle = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
        )
        os.close(lock_handle)
    except FileExistsError as exc:
        raise CorrectionError("session_busy", "Session write is already in progress.") from exc
    try:
        session = load_correction_session(project_root, asset_id, session_id)
        events = read_correction_events(project_root, asset_id, session_id)
        for event in events:
            if event.get("client_request_id") == client_request_id:
                if event.get("actor") != actor:
                    raise CorrectionError(
                        "request_id_conflict",
                        "client_request_id is already owned by another actor.",
                    )
                return dict(event["accepted_response"])
        if session.get("status") != "draft":
            raise CorrectionError(
                "session_immutable",
                f"Session cannot be edited in state: {session.get('status')}",
            )
        if session.get("active_editor") != actor and _lock_is_active(session):
            raise CorrectionError(
                "session_locked",
                f"Session is locked by {session.get('active_editor')}.",
            )
        if expected_revision != session.get("revision"):
            raise CorrectionError(
                "stale_revision",
                f"Expected revision {expected_revision}, current revision is {session.get('revision')}.",
            )
        next_revision = int(session["revision"]) + 1
        normalized_operation = _normalize_operation(operation, next_revision)
        public_event = {
            "schema_version": "1.0",
            "event_id": f"event-{next_revision:06d}",
            "session_id": session_id,
            "actor": actor,
            "timestamp": _iso_now(),
            "client_request_id": client_request_id,
            "resulting_revision": next_revision,
            "operation": normalized_operation,
        }
        baseline = load_correction_baseline(project_root, asset_id, session_id)
        materialized = materialize_correction(
            baseline, [*events, public_event]
        )
        objects = _object_document(materialized["assignments"])
        confirmed = set(materialized["confirmed_instance_ids"])
        for item in objects["objects"]:
            if item["instance_id"] in confirmed:
                item["review_state"] = "confirmed"
        updated = {
            **session,
            "revision": next_revision,
            "active_editor": actor,
            "lock_expires_at": _lock_expiry(
                int(session.get("lock_ttl_seconds", 900))
            ),
            "updated_at": public_event["timestamp"],
            "draft_fingerprint": materialized["draft_fingerprint"],
            "undo_available": materialized["undo_available"],
            "redo_available": materialized["redo_available"],
            "last_event": public_event,
        }
        stored_event = {**public_event, "accepted_response": updated}
        events_path = session_dir / "events.jsonl"
        with events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(stored_event, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        write_json(materialized, session_dir / "draft_labels.json")
        write_json(objects, session_dir / "draft_objects.json")
        write_json(updated, session_dir / "correction_session.json")
        return updated
    finally:
        lock_path.unlink(missing_ok=True)
