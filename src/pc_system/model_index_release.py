import hashlib
import json
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.model_feature_index import load_model_feature_index
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    load_operation,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import _record_failure, _require_plain
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_retrieval_config import load_retrieval_config
from pc_system.model_sampling import _publish_exact_json


_OWNER_FIELDS = {
    "schema_version",
    "release_id",
    "operation_id",
    "request_id",
    "request_fingerprint",
}
_RELEASE_FIELDS = {
    "schema_version",
    "release_id",
    "index_id",
    "index_fingerprint",
    "action",
    "previous_release_id",
    "rollback_of_release_id",
    "reason",
    "current_heads",
    "coverage",
    "operation_id",
    "created_by",
    "created_at",
    "status",
}
_PROJECTION_FIELDS = {
    "schema_version",
    "current_release_id",
    "current_index_id",
    "release_fingerprint",
    "updated_at",
}


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_index_integrity_error", "Index release is not canonical JSON."
        ) from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _identifier(value: object, label: str, code: str = "model_index_release_conflict") -> str:
    try:
        return validate_identifier(value, label)
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(code, "Index release identity is invalid.") from exc


def _release_root(root: Path, release_id: str) -> Path:
    return root / "models" / "feature_index_releases" / release_id


def _projection_path(root: Path) -> Path:
    return root / "models" / "current_feature_index.json"


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _read_json(path: Path, *, not_found: bool = False) -> dict:
    try:
        _require_plain(path, directory=False)
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("index release file too large")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except FileNotFoundError as exc:
        code = "model_index_release_not_found" if not_found else "model_index_integrity_error"
        raise ModelMatchingError(code, "Index release artifact does not exist.") from exc
    except (OSError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_index_integrity_error", "Index release artifact is invalid."
        ) from exc
    if type(value) is not dict:
        raise ModelMatchingError(
            "model_index_integrity_error", "Index release artifact must be an object."
        )
    return value


def _result(release: dict) -> dict:
    return {"release_id": release["release_id"], "index_id": release["index_id"]}


def _load_release(root: Path, release_id: str) -> dict:
    release_id = _identifier(
        release_id, "release_id", code="model_index_release_not_found"
    )
    directory = _release_root(root, release_id)
    owner = _read_json(directory / "operation_owner.json", not_found=True)
    release = _read_json(directory / "release.json", not_found=True)
    try:
        snapshot = read_verified_operation_snapshot(root, release["operation_id"])
        operation = snapshot["operation"]
        events = snapshot["events"]
        published = [
            event
            for event in events
            if event["event_type"] == "model_feature_index_release.published"
        ]
        if (
            set(owner) != _OWNER_FIELDS
            or set(release) != _RELEASE_FIELDS
            or release["schema_version"] != "1.0"
            or release["release_id"] != release_id
            or release["status"] != "published"
            or owner["release_id"] != release_id
            or owner["operation_id"] != release["operation_id"]
            or operation["operation_type"] != "model_feature_index_release.publish"
            or operation["status"] != "completed"
            or operation.get("result") != _result(release)
            or operation["request_id"] != owner["request_id"]
            or operation["request_fingerprint"] != owner["request_fingerprint"]
            or events[0]["actor_id"] != release["created_by"]
            or events[0]["timestamp"] != release["created_at"]
            or len(published) != 1
            or published[0]["details"] != _result(release)
        ):
            raise ValueError("index release evidence differs")
        index = load_model_feature_index(
            root, release["index_id"], require_current_heads=False
        )
        if (
            _fingerprint(index) != release["index_fingerprint"]
            or index["current_heads"] != release["current_heads"]
            or index["coverage"] != release["coverage"]
        ):
            raise ValueError("released index differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_index_integrity_error", "Index release evidence is invalid."
        ) from exc
    return json.loads(json.dumps(release, ensure_ascii=False))


def _list_model_feature_index_releases(
    project_root: Path, *, exclude_release_id: str | None = None
) -> list[dict]:
    root = Path(project_root)
    parent = root / "models" / "feature_index_releases"
    try:
        candidates = sorted(parent.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        return []
    releases = []
    for candidate in candidates:
        if candidate.name == exclude_release_id:
            continue
        if (candidate / "release.json").is_file():
            releases.append(_load_release(root, candidate.name))
    releases.sort(key=lambda item: (item["created_at"], item["release_id"]))
    previous = None
    for release in releases:
        if release["previous_release_id"] != previous:
            raise ModelMatchingError(
                "model_index_integrity_error", "Index release history is not linear."
            )
        previous = release["release_id"]
    return releases


def list_model_feature_index_releases(project_root: Path) -> list[dict]:
    return _list_model_feature_index_releases(project_root)


def _load_projection(root: Path) -> dict | None:
    path = _projection_path(root)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    projection = _read_json(path)
    try:
        if set(projection) != _PROJECTION_FIELDS or projection["schema_version"] != "1.0":
            raise ValueError("projection structure differs")
        release = _load_release(root, projection["current_release_id"])
        if (
            release["index_id"] != projection["current_index_id"]
            or _fingerprint(release) != projection["release_fingerprint"]
            or release["created_at"] != projection["updated_at"]
        ):
            raise ValueError("projection differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_index_integrity_error", "Current index projection is invalid."
        ) from exc
    return release


def load_current_model_feature_index_release(project_root: Path) -> dict | None:
    root = Path(project_root)
    release = _load_projection(root)
    history = list_model_feature_index_releases(root)
    if release is None:
        if history:
            raise ModelMatchingError(
                "model_index_integrity_error", "Current index projection is missing."
            )
        return None
    if not history or history[-1]["release_id"] != release["release_id"]:
        raise ModelMatchingError(
            "model_index_integrity_error", "Current index release is not the history head."
        )
    load_model_feature_index(root, release["index_id"], require_current_heads=True)
    return release


def _projection(release: dict) -> dict:
    return {
        "schema_version": "1.0",
        "current_release_id": release["release_id"],
        "current_index_id": release["index_id"],
        "release_fingerprint": _fingerprint(release),
        "updated_at": release["created_at"],
    }


def release_model_feature_index(
    project_root: Path,
    *,
    index_id: str,
    release_id: str,
    action: str,
    expected_current_release_id: str | None,
    rollback_of_release_id: str | None,
    reason: str,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    index_id = _identifier(index_id, "index_id")
    release_id = _identifier(release_id, "release_id")
    if action not in {"activate", "rollback"} or type(reason) is not str or not reason.strip():
        raise ModelMatchingError(
            "model_index_release_conflict", "Index release request is invalid."
        )
    if action == "activate" and rollback_of_release_id is not None:
        raise ModelMatchingError(
            "model_index_release_conflict", "Activation cannot name a rollback release."
        )
    if action == "rollback" and rollback_of_release_id is None:
        raise ModelMatchingError(
            "model_index_release_conflict", "Rollback target is required."
        )
    if expected_current_release_id is not None:
        expected_current_release_id = _identifier(
            expected_current_release_id, "expected_current_release_id"
        )
    if rollback_of_release_id is not None:
        rollback_of_release_id = _identifier(
            rollback_of_release_id, "rollback_of_release_id"
        )
    require_any_role(principal, {"expert"})
    request_payload = {
        "index_id": index_id,
        "release_id": release_id,
        "action": action,
        "expected_current_release_id": expected_current_release_id,
        "rollback_of_release_id": rollback_of_release_id,
        "reason": reason.strip(),
    }
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type="model_feature_index_release.publish",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    owner = {
        "schema_version": "1.0",
        "release_id": release_id,
        "operation_id": operation_id,
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
    }
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(
            error.get("code", "model_index_integrity_error"),
            error.get("message", "Index release failed."),
        )
    try:
        if replayed and operation["status"] == "completed":
            release = _load_release(root, release_id)
            current = _load_projection(root)
            history = list_model_feature_index_releases(root)
            current_id = current["release_id"] if current else None
            if (
                history
                and history[-1]["release_id"] == release_id
                and current_id == release["previous_release_id"]
            ):
                write_json(_projection(release), _projection_path(root))
            return release
        with model_resource_lock(root, "feature-index-release", "production"):
            current = _load_projection(root)
            actual_current_id = current["release_id"] if current else None
            if actual_current_id != expected_current_release_id:
                raise ModelMatchingError(
                    "model_index_release_conflict", "Current index release changed."
                )
            candidate_owner = None
            candidate_owner_path = (
                _release_root(root, release_id) / "operation_owner.json"
            )
            try:
                candidate_owner = _read_json(
                    candidate_owner_path, not_found=True
                )
            except ModelMatchingError as exc:
                if exc.code != "model_index_release_not_found":
                    raise
            history = _list_model_feature_index_releases(
                root,
                exclude_release_id=(
                    release_id if candidate_owner == owner else None
                ),
            )
            if action == "rollback":
                target = next(
                    (item for item in history if item["release_id"] == rollback_of_release_id),
                    None,
                )
                if target is None or target["index_id"] != index_id:
                    raise ModelMatchingError(
                        "model_index_release_not_found", "Rollback index release does not exist."
                    )
            index = load_model_feature_index(root, index_id, require_current_heads=True)
            if index["index_mode"] != "production":
                raise ModelMatchingError(
                    "model_index_release_conflict", "Challenger index cannot be activated."
                )
            config = load_retrieval_config(root, index["config_id"])
            minimum = config["scoring_config"]["production_minimum_coverage"]
            if index["coverage"]["coverage"] < minimum:
                raise ModelMatchingError(
                    "model_index_coverage_rejected", "Index coverage is below production minimum."
                )
            snapshot = read_verified_operation_snapshot(root, operation_id)
            started = snapshot["events"][0]
            release = {
                "schema_version": "1.0",
                "release_id": release_id,
                "index_id": index_id,
                "index_fingerprint": _fingerprint(index),
                "action": action,
                "previous_release_id": actual_current_id,
                "rollback_of_release_id": rollback_of_release_id,
                "reason": reason.strip(),
                "current_heads": index["current_heads"],
                "coverage": index["coverage"],
                "operation_id": operation_id,
                "created_by": started["actor_id"],
                "created_at": started["timestamp"],
                "status": "published",
            }
            directory = _release_root(root, release_id)
            directory.mkdir(parents=True, exist_ok=True)
            _publish_exact_json(
                directory / "operation_owner.json",
                owner,
                conflict_code="operation_busy",
                conflict_message="Index release owner conflicts.",
            )
            _publish_exact_json(
                directory / "release.json",
                release,
                conflict_code="model_index_release_conflict",
                conflict_message="Index release content conflicts.",
            )
            ensure_operation_event(
                root,
                operation_id,
                "model_feature_index_release.published",
                _result(release),
            )
            complete_operation(root, operation_id, _result(release))
            write_json(_projection(release), _projection_path(root))
            return _load_release(root, release_id)
    except Exception as exc:
        error = exc if isinstance(exc, ModelMatchingError) else ModelMatchingError(
            "model_index_integrity_error", "Index release failed."
        )
        current_operation = load_operation(root, operation_id)
        if current_operation["status"] == "running" and error.code not in {
            "operation_busy",
            "publication_recovery_required",
        }:
            _record_failure(root, operation_id, error)
        if error is exc:
            raise
        raise error from exc
