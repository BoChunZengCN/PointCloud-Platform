import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.model_import import (
    fingerprint_file,
    list_model_versions,
    load_model_version,
)
from pc_system.model_library import load_model_asset, model_version_dir
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    fail_operation,
    load_operation,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_release_state import (
    ReleaseChain,
    ReleaseState,
    build_release_chain,
    classify_release_state,
)


RELEASE_ACTIONS = frozenset({"activate", "rollback"})
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class _CandidateVisibility(Enum):
    ABSENT = "absent"
    OWNED = "owned"
    VERIFIED_FOREIGN = "verified_foreign"
    UNCERTAIN = "uncertain"
_RELEASE_FIELDS = frozenset(
    {
        "schema_version",
        "model_id",
        "release_id",
        "version_id",
        "action",
        "previous_release_id",
        "rollback_of_release_id",
        "reason",
        "operation_id",
        "actor_id",
        "created_at",
        "version_manifest_fingerprint",
    }
)
_PROJECTION_FIELDS = frozenset(
    {
        "schema_version",
        "model_id",
        "current_release_id",
        "current_version_id",
        "release_fingerprint",
        "updated_at",
    }
)
_OWNER_FIELDS = frozenset(
    {
        "schema_version",
        "model_id",
        "release_id",
        "operation_id",
        "request_id",
        "request_fingerprint",
    }
)


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_hash(value: dict) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _release_root(project_root: Path, model_id: str, release_id: str) -> Path:
    return project_root / "models" / model_id / "releases" / release_id


def _current_path(project_root: Path, model_id: str) -> Path:
    return project_root / "models" / model_id / "current_release.json"


def _require_plain(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    ):
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release storage contains a non-plain filesystem object.",
        )


def _load_json(path: Path) -> object:
    try:
        _require_plain(path, directory=False)
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except ModelMatchingError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release data could not be read safely.",
        ) from exc


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_new_json(path: Path, value: dict) -> bool:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    temporary_path: Path | None = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            return False
        published = True
        _fsync_directory(path.parent)
        return True
    except OSError as exc:
        if published:
            raise ModelMatchingError(
                "publication_recovery_required",
                "Model release data is visible but durability must be recovered.",
            ) from exc
        raise
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _validate_identifier(value: object, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be an exact string.")
    return validate_identifier(value, label)


def _validate_optional_identifier(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _validate_identifier(value, label)


def _normalize_request(
    *,
    model_id: object,
    version_id: object,
    release_id: object,
    action: object,
    expected_current_release_id: object,
    rollback_of_release_id: object,
    reason: object,
) -> dict:
    try:
        normalized = {
            "model_id": _validate_identifier(model_id, "model_id"),
            "version_id": _validate_identifier(version_id, "version_id"),
            "release_id": _validate_identifier(release_id, "release_id"),
            "expected_current_release_id": _validate_optional_identifier(
                expected_current_release_id, "expected_current_release_id"
            ),
            "rollback_of_release_id": _validate_optional_identifier(
                rollback_of_release_id, "rollback_of_release_id"
            ),
        }
        if type(action) is not str or action not in RELEASE_ACTIONS:
            raise ValueError("action must be activate or rollback.")
        if type(reason) is not str:
            raise ValueError("reason must be an exact string.")
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 500:
            raise ValueError("reason must contain 1-500 Unicode characters.")
        normalized["action"] = action
        normalized["reason"] = normalized_reason
        if action == "activate" and normalized["rollback_of_release_id"] is not None:
            raise ValueError("activate must not provide rollback_of_release_id.")
        if action == "rollback" and normalized["rollback_of_release_id"] is None:
            raise ValueError("rollback requires rollback_of_release_id.")
        return normalized
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("invalid_model_release", str(exc)) from exc


def _validate_timestamp(value: object) -> str:
    if type(value) is not str:
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return value


def _validate_release(
    project_root: Path,
    model_id: str,
    release_id: str,
    value: object,
) -> dict:
    try:
        if type(value) is not dict or set(value) != _RELEASE_FIELDS:
            raise ValueError("invalid release structure")
        if (
            value["schema_version"] != "1.0"
            or value["model_id"] != model_id
            or value["release_id"] != release_id
            or value["action"] not in RELEASE_ACTIONS
        ):
            raise ValueError("invalid release identity")
        _validate_identifier(value["version_id"], "version_id")
        _validate_identifier(value["operation_id"], "operation_id")
        _validate_identifier(value["actor_id"], "actor_id")
        _validate_optional_identifier(
            value["previous_release_id"], "previous_release_id"
        )
        rollback_id = _validate_optional_identifier(
            value["rollback_of_release_id"], "rollback_of_release_id"
        )
        if (value["action"] == "rollback") != (rollback_id is not None):
            raise ValueError("invalid rollback identity")
        if type(value["reason"]) is not str or not value["reason"].strip():
            raise ValueError("invalid release reason")
        if value["reason"] != value["reason"].strip() or len(value["reason"]) > 500:
            raise ValueError("non-canonical release reason")
        _validate_timestamp(value["created_at"])
        fingerprint = value["version_manifest_fingerprint"]
        if (
            type(fingerprint) is not str
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ValueError("invalid version manifest fingerprint")
        load_model_version(project_root, model_id, value["version_id"])
        manifest_path = (
            model_version_dir(project_root, model_id, value["version_id"])
            / "model_manifest.json"
        )
        if fingerprint_file(manifest_path) != fingerprint:
            raise ValueError("version manifest fingerprint changed")
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release evidence is invalid.",
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release evidence is invalid.",
        ) from exc
    return json.loads(json.dumps(value, ensure_ascii=False))


def _load_release(
    project_root: Path,
    model_id: str,
    release_id: str,
    *,
    require_completed_audit: bool = True,
) -> dict:
    root = _release_root(project_root, model_id, release_id)
    try:
        _require_plain(root, directory=True)
        path = root / "release.json"
        value = _load_json(path)
    except FileNotFoundError as exc:
        raise ModelMatchingError(
            "model_release_not_found", "Model release does not exist."
        ) from exc
    release = _validate_release(project_root, model_id, release_id, value)
    if require_completed_audit:
        _validate_release_audit(project_root, release)
    return release


def _read_release_history(
    root: Path,
    model_id: str,
    *,
    require_completed_audit: bool,
) -> tuple[list[dict], ReleaseChain]:
    releases_root = root / "models" / model_id / "releases"
    try:
        releases_root.lstat()
    except FileNotFoundError:
        return [], build_release_chain([])
    _require_plain(releases_root, directory=True)
    releases: list[dict] = []
    try:
        candidates = sorted(releases_root.iterdir(), key=lambda item: item.name)
        for candidate in candidates:
            if candidate.name.startswith("."):
                continue
            _require_plain(candidate, directory=True)
            release_path = candidate / "release.json"
            try:
                release_path.lstat()
            except FileNotFoundError:
                continue
            release_id = _validate_identifier(candidate.name, "release_id")
            releases.append(
                _load_release(
                    root,
                    model_id,
                    release_id,
                    require_completed_audit=require_completed_audit,
                )
            )
    except ModelMatchingError:
        raise
    except (OSError, ValueError) as exc:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release history could not be read safely.",
        ) from exc
    return releases, build_release_chain(releases)


def list_model_releases(project_root: Path, model_id: str) -> list[dict]:
    root = Path(project_root)
    try:
        normalized_model_id = _validate_identifier(model_id, "model_id")
    except ValueError as exc:
        raise ModelMatchingError("invalid_model_release", str(exc)) from exc
    load_model_asset(root, normalized_model_id)
    releases, _chain = _read_release_history(
        root, normalized_model_id, require_completed_audit=True
    )
    return sorted(
        releases, key=lambda item: (item["created_at"], item["release_id"])
    )


def _load_projected_release(
    root: Path,
    model_id: str,
    *,
    require_completed_audit: bool,
) -> dict | None:
    path = _current_path(root, model_id)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    value = _load_json(path)
    try:
        if type(value) is not dict or set(value) != _PROJECTION_FIELDS:
            raise ValueError("invalid projection structure")
        if value["schema_version"] != "1.0" or value["model_id"] != model_id:
            raise ValueError("invalid projection identity")
        release_id = _validate_identifier(
            value["current_release_id"], "current_release_id"
        )
        version_id = _validate_identifier(
            value["current_version_id"], "current_version_id"
        )
        _validate_timestamp(value["updated_at"])
        release = _load_release(
            root,
            model_id,
            release_id,
            require_completed_audit=require_completed_audit,
        )
        if (
            release["version_id"] != version_id
            or value["release_fingerprint"] != _canonical_hash(release)
            or value["updated_at"] != release["created_at"]
        ):
            raise ValueError("projection does not match release")
        return release
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Current model release projection is invalid.",
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Current model release projection is invalid.",
        ) from exc


def load_current_model_release(
    project_root: Path, model_id: str
) -> dict | None:
    root = Path(project_root)
    try:
        normalized_model_id = _validate_identifier(model_id, "model_id")
    except ValueError as exc:
        raise ModelMatchingError("invalid_model_release", str(exc)) from exc
    load_model_asset(root, normalized_model_id)
    release = _load_projected_release(
        root, normalized_model_id, require_completed_audit=True
    )
    history, chain = _read_release_history(
        root, normalized_model_id, require_completed_audit=True
    )
    if release is None:
        if history:
            raise ModelMatchingError(
                "model_release_integrity_error",
                "Current model release projection is missing.",
            )
        return None
    if chain.head_release_id != release["release_id"]:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Current model release projection is not the history head.",
        )
    return release


def list_version_release_status(
    project_root: Path, model_id: str
) -> list[dict]:
    root = Path(project_root)
    current = load_current_model_release(root, model_id)
    history, chain = _read_release_history(
        root, model_id, require_completed_audit=True
    )
    by_id = {release["release_id"]: release for release in history}
    graph_history = [by_id[release_id] for release_id in chain.ordered_release_ids]
    releases_by_version: dict[str, list[dict]] = {}
    for release in graph_history:
        releases_by_version.setdefault(release["version_id"], []).append(release)
    return [
        {
            "version_id": version["version_id"],
            "supersedes_version_id": version["supersedes_version_id"],
            "imported_at": version["imported_at"],
            "manifest_fingerprint": fingerprint_file(
                model_version_dir(root, model_id, version["version_id"])
                / "model_manifest.json"
            ),
            "is_current": current is not None
            and current["version_id"] == version["version_id"],
            "release_count": len(
                releases_by_version.get(version["version_id"], [])
            ),
            "latest_release_id": (
                releases_by_version[version["version_id"]][-1]["release_id"]
                if releases_by_version.get(version["version_id"])
                else None
            ),
            "latest_release_action": (
                releases_by_version[version["version_id"]][-1]["action"]
                if releases_by_version.get(version["version_id"])
                else None
            ),
        }
        for version in list_model_versions(root, model_id)
    ]


def _owner_value(operation: dict, normalized: dict) -> dict:
    return {
        "schema_version": "1.0",
        "model_id": normalized["model_id"],
        "release_id": normalized["release_id"],
        "operation_id": operation["operation_id"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
    }


def _publish_owner(root: Path, owner: dict) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        _require_plain(root, directory=True)
        published = _publish_new_json(root / "operation_owner.json", owner)
    except ModelMatchingError:
        raise
    except OSError as exc:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release owner could not be published safely.",
        ) from exc
    if not published and _load_json(root / "operation_owner.json") != owner:
        raise ModelMatchingError(
            "model_release_exists",
            "Model release identity belongs to another request.",
        )


def _load_optional_owner(root: Path) -> dict | None:
    path = root / "operation_owner.json"
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    value = _load_json(path)
    if type(value) is not dict or set(value) != _OWNER_FIELDS:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release owner evidence is invalid.",
        )
    return value


def _write_or_verify_release(path: Path, release: dict) -> None:
    try:
        published = _publish_new_json(path, release)
    except ModelMatchingError:
        raise
    except OSError as exc:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release could not be published safely.",
        ) from exc
    if not published:
        existing = _load_json(path)
        if existing != release:
            raise ModelMatchingError(
                "model_release_exists",
                "Model release identity already exists with different content.",
            )


def _expected_release(
    project_root: Path,
    normalized: dict,
    operation: dict,
    start: dict,
) -> dict:
    if (
        start.get("event_type") != "operation.started"
        or start.get("actor_id") != operation.get("actor_id")
        or start.get("details", {}).get("request_id")
        != operation.get("request_id")
        or start.get("details", {}).get("request_fingerprint")
        != operation.get("request_fingerprint")
        or operation.get("request_fingerprint") != _canonical_hash(normalized)
    ):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Model release start evidence differs from its frozen request.",
        )
    load_model_version(
        project_root, normalized["model_id"], normalized["version_id"]
    )
    manifest_path = (
        model_version_dir(
            project_root, normalized["model_id"], normalized["version_id"]
        )
        / "model_manifest.json"
    )
    return {
        "schema_version": "1.0",
        "model_id": normalized["model_id"],
        "release_id": normalized["release_id"],
        "version_id": normalized["version_id"],
        "action": normalized["action"],
        "previous_release_id": normalized["expected_current_release_id"],
        "rollback_of_release_id": normalized["rollback_of_release_id"],
        "reason": normalized["reason"],
        "operation_id": operation["operation_id"],
        "actor_id": start["actor_id"],
        "created_at": start["timestamp"],
        "version_manifest_fingerprint": fingerprint_file(manifest_path),
    }


def _result(release: dict) -> dict:
    return {
        "model_id": release["model_id"],
        "release_id": release["release_id"],
        "version_id": release["version_id"],
        "action": release["action"],
    }


def _event_details(release: dict) -> dict:
    return {
        **_result(release),
        "previous_release_id": release["previous_release_id"],
        "rollback_of_release_id": release["rollback_of_release_id"],
        "release_fingerprint": _canonical_hash(release),
        "version_manifest_fingerprint": release[
            "version_manifest_fingerprint"
        ],
    }


def _validate_release_audit(project_root: Path, release: dict) -> None:
    try:
        snapshot = read_verified_operation_snapshot(
            project_root, release["operation_id"]
        )
        operation = snapshot["operation"]
        events = snapshot["events"]
        request_payload = {
            "model_id": release["model_id"],
            "version_id": release["version_id"],
            "release_id": release["release_id"],
            "expected_current_release_id": release["previous_release_id"],
            "rollback_of_release_id": release["rollback_of_release_id"],
            "action": release["action"],
            "reason": release["reason"],
        }
        owner = _load_json(
            _release_root(
                project_root, release["model_id"], release["release_id"]
            )
            / "operation_owner.json"
        )
        expected_owner = _owner_value(operation, request_payload)
        if (
            operation["operation_type"] != "model_release.change"
            or operation["status"] != "completed"
            or operation.get("result") != _result(release)
            or operation.get("request_fingerprint")
            != _canonical_hash(request_payload)
            or type(owner) is not dict
            or set(owner) != _OWNER_FIELDS
            or owner != expected_owner
            or not events
            or events[0]["event_type"] != "operation.started"
            or events[0]["actor_id"] != release["actor_id"]
            or events[0]["timestamp"] != release["created_at"]
        ):
            raise ValueError("release operation binding differs")
        event_type = (
            "model_release.rolled_back"
            if release["action"] == "rollback"
            else "model_release.published"
        )
        matching = [event for event in events if event["event_type"] == event_type]
        if len(matching) != 1 or matching[0]["details"] != _event_details(release):
            raise ValueError("release business event differs")
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release audit evidence is invalid.",
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Model release audit evidence is invalid.",
        ) from exc


def _require_other_release_audits(
    project_root: Path, releases: list[dict], own_release_id: str
) -> None:
    for release in releases:
        if release["release_id"] == own_release_id:
            continue
        try:
            _validate_release_audit(project_root, release)
        except ModelMatchingError as exc:
            try:
                operation = load_operation(
                    project_root, release["operation_id"]
                )
            except ModelMatchingError:
                raise exc
            if operation.get("status") == "running":
                raise ModelMatchingError(
                    "publication_recovery_required",
                    "A prior visible model release must be recovered first.",
                ) from exc
            raise


def _require_no_other_owner_only_candidate(
    project_root: Path, model_id: str, own_release_id: str
) -> None:
    releases_root = project_root / "models" / model_id / "releases"
    try:
        releases_root.lstat()
    except FileNotFoundError:
        return
    _require_plain(releases_root, directory=True)
    for candidate in releases_root.iterdir():
        if candidate.name.startswith(".") or candidate.name == own_release_id:
            continue
        _require_plain(candidate, directory=True)
        if _release_path_is_visible(candidate / "release.json"):
            continue
        owner = _load_optional_owner(candidate)
        if owner is None:
            continue
        try:
            operation = load_operation(project_root, owner["operation_id"])
        except (KeyError, ModelMatchingError) as exc:
            raise ModelMatchingError(
                "model_release_integrity_error",
                "An owner-only release candidate is invalid.",
            ) from exc
        if operation.get("status") == "running":
            raise ModelMatchingError(
                "publication_recovery_required",
                "A prior model release owner must be recovered first.",
            )
        raise ModelMatchingError(
            "model_release_integrity_error",
            "A terminal release operation has no release record.",
        )


def _collect_release_evidence(
    project_root: Path,
    expected_owner: dict,
    expected_release: dict,
) -> dict:
    model_id = expected_release["model_id"]
    release_id = expected_release["release_id"]
    candidate = _release_root(project_root, model_id, release_id)
    try:
        candidate.lstat()
    except FileNotFoundError:
        actual_owner = None
        actual_release = None
    else:
        _require_plain(candidate, directory=True)
        actual_owner = _load_optional_owner(candidate)
        release_path = candidate / "release.json"
        if _release_path_is_visible(release_path):
            actual_release = _load_release(
                project_root,
                model_id,
                release_id,
                require_completed_audit=False,
            )
        else:
            actual_release = None

    releases, chain = _read_release_history(
        project_root, model_id, require_completed_audit=False
    )
    _require_no_other_owner_only_candidate(
        project_root, model_id, release_id
    )
    _require_other_release_audits(project_root, releases, release_id)
    projected = _load_projected_release(
        project_root, model_id, require_completed_audit=False
    )
    projected_release_id = (
        None if projected is None else projected["release_id"]
    )
    snapshot = read_verified_operation_snapshot(
        project_root, expected_release["operation_id"]
    )
    operation = snapshot["operation"]
    events = snapshot["events"]
    event_type = (
        "model_release.rolled_back"
        if expected_release["action"] == "rollback"
        else "model_release.published"
    )
    matching_business_events = [
        event
        for event in events
        if event["event_type"] == event_type
        and event["details"] == _event_details(expected_release)
    ]
    business_events = [
        event for event in events if event["event_type"] == event_type
    ]
    business_event_matches = (
        len(matching_business_events) == 1 and len(business_events) == 1
    )
    completed_result_matches = (
        operation.get("status") == "completed"
        and operation.get("result") == _result(expected_release)
    )
    return {
        "actual_owner": actual_owner,
        "actual_release": actual_release,
        "projected_release_id": projected_release_id,
        "operation_status": operation.get("status"),
        "business_event_matches": business_event_matches,
        "completed_result_matches": completed_result_matches,
        "chain": chain,
        "releases": releases,
    }


def _classify_evidence(
    expected_owner: dict, expected_release: dict, evidence: dict
) -> ReleaseState:
    if (
        evidence["actual_owner"] is None
        and evidence["actual_release"] is None
        and evidence["projected_release_id"]
        == evidence["chain"].head_release_id
        and evidence["projected_release_id"]
        != expected_release["previous_release_id"]
    ):
        raise ModelMatchingError(
            "stale_model_release",
            "Current model release differs from the expected release.",
        )
    return classify_release_state(
        expected_owner=expected_owner,
        actual_owner=evidence["actual_owner"],
        expected_release=expected_release,
        actual_release=evidence["actual_release"],
        projected_release_id=evidence["projected_release_id"],
        operation_status=evidence["operation_status"],
        business_event_matches=evidence["business_event_matches"],
        completed_result_matches=evidence["completed_result_matches"],
        chain=evidence["chain"],
    )


def _require_valid_rollback_request(
    expected_release: dict, releases: list[dict]
) -> None:
    if expected_release["action"] != "rollback":
        return
    by_id = {release["release_id"]: release for release in releases}
    target = by_id.get(expected_release["rollback_of_release_id"])
    if (
        target is None
        or target["release_id"] == expected_release["previous_release_id"]
        or target["version_id"] != expected_release["version_id"]
    ):
        raise ModelMatchingError(
            "invalid_model_release",
            "Rollback target must be a non-current historical release of the target version.",
        )


def _foreign_candidate_is_verified(
    project_root: Path,
    expected_owner: dict,
    actual_owner: dict,
    *,
    release_visible: bool,
) -> bool:
    if (
        not release_visible
        or actual_owner.get("operation_id") == expected_owner["operation_id"]
        or actual_owner.get("model_id") != expected_owner["model_id"]
        or actual_owner.get("release_id") != expected_owner["release_id"]
    ):
        return False
    try:
        release = _load_release(
            project_root,
            expected_owner["model_id"],
            expected_owner["release_id"],
            require_completed_audit=False,
        )
        snapshot = read_verified_operation_snapshot(
            project_root, actual_owner["operation_id"]
        )
        operation = snapshot["operation"]
        start = snapshot["events"][0]
        request_payload = {
            "model_id": release["model_id"],
            "version_id": release["version_id"],
            "release_id": release["release_id"],
            "expected_current_release_id": release["previous_release_id"],
            "rollback_of_release_id": release["rollback_of_release_id"],
            "action": release["action"],
            "reason": release["reason"],
        }
        valid = (
            operation.get("operation_type") == "model_release.change"
            and operation.get("status") in {"running", "completed"}
            and release["operation_id"] == operation.get("operation_id")
            and actual_owner == _owner_value(operation, request_payload)
            and operation.get("request_fingerprint")
            == _canonical_hash(request_payload)
            and start.get("event_type") == "operation.started"
            and start.get("actor_id") == release["actor_id"]
            and start.get("timestamp") == release["created_at"]
        )
        if not valid:
            return False
        if operation["status"] == "completed":
            _validate_release_audit(project_root, release)
        return True
    except (KeyError, ModelMatchingError):
        return False


def _candidate_visibility(
    project_root: Path, expected_owner: dict
) -> _CandidateVisibility:
    candidate = _release_root(
        project_root, expected_owner["model_id"], expected_owner["release_id"]
    )
    try:
        candidate.lstat()
        _require_plain(candidate, directory=True)
    except FileNotFoundError:
        return _CandidateVisibility.ABSENT
    except (OSError, ModelMatchingError):
        return _CandidateVisibility.UNCERTAIN

    owner_path = candidate / "operation_owner.json"
    release_path = candidate / "release.json"
    try:
        owner_path.lstat()
        owner_visible = True
    except FileNotFoundError:
        owner_visible = False
    except OSError:
        return _CandidateVisibility.UNCERTAIN
    try:
        release_path.lstat()
        release_visible = True
    except FileNotFoundError:
        release_visible = False
    except OSError:
        return _CandidateVisibility.UNCERTAIN

    if not owner_visible:
        return (
            _CandidateVisibility.UNCERTAIN
            if release_visible
            else _CandidateVisibility.ABSENT
        )
    try:
        owner = _load_optional_owner(candidate)
    except ModelMatchingError:
        return _CandidateVisibility.UNCERTAIN
    if owner == expected_owner:
        return _CandidateVisibility.OWNED
    if _foreign_candidate_is_verified(
        project_root,
        expected_owner,
        owner,
        release_visible=release_visible,
    ):
        return _CandidateVisibility.VERIFIED_FOREIGN
    return _CandidateVisibility.UNCERTAIN


def _record_failure(project_root: Path, operation_id: str, error: ModelMatchingError) -> None:
    try:
        operation = load_operation(project_root, operation_id)
        if operation["status"] == "failed":
            if operation.get("error") == {
                "code": error.code,
                "message": str(error),
            }:
                return
            raise ModelMatchingError(
                "audit_integrity_error",
                "Release failure audit differs from the business failure.",
            )
        if operation["status"] != "running":
            raise ModelMatchingError(
                "audit_integrity_error",
                "Release failure cannot mutate a terminal operation.",
            )
        fail_operation(project_root, operation_id, error.code, str(error))
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        try:
            current = load_operation(project_root, operation_id)
        except Exception as load_exc:
            raise ModelMatchingError(
                "audit_persistence_error",
                "Release failure could not be recorded durably.",
            ) from load_exc
        if current["status"] == "failed" and current.get("error") == {
            "code": error.code,
            "message": str(error),
        }:
            return
        if exc.code.startswith("audit_"):
            raise
        raise ModelMatchingError(
            "audit_persistence_error",
            "Release failure could not be recorded durably.",
        ) from exc


def _release_path_is_visible(path: Path) -> bool:
    try:
        _require_plain(path, directory=False)
        return True
    except FileNotFoundError:
        return False


def _projection_value(release: dict) -> dict:
    return {
        "schema_version": "1.0",
        "model_id": release["model_id"],
        "current_release_id": release["release_id"],
        "current_version_id": release["version_id"],
        "release_fingerprint": _canonical_hash(release),
        "updated_at": release["created_at"],
    }


def _publish_projection(project_root: Path, release: dict) -> None:
    write_json(
        _projection_value(release),
        _current_path(project_root, release["model_id"]),
    )
    projected = _load_projected_release(
        project_root,
        release["model_id"],
        require_completed_audit=False,
    )
    if projected != release:
        raise ModelMatchingError(
            "model_release_integrity_error",
            "Current release projection could not be verified.",
        )


def release_model_version(
    project_root: Path,
    *,
    model_id: str,
    version_id: str,
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
    normalized = _normalize_request(
        model_id=model_id,
        version_id=version_id,
        release_id=release_id,
        action=action,
        expected_current_release_id=expected_current_release_id,
        rollback_of_release_id=rollback_of_release_id,
        reason=reason,
    )
    try:
        normalized_operation_id = _validate_identifier(operation_id, "operation_id")
    except ValueError as exc:
        raise ModelMatchingError("invalid_model_release", str(exc)) from exc
    operation, replayed = start_operation(
        root,
        operation_id=normalized_operation_id,
        operation_type="model_release.change",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=dict(normalized),
    )
    audited_operation_id = operation["operation_id"]
    if replayed:
        if operation["status"] == "failed":
            error = operation.get("error") or {}
            raise ModelMatchingError(
                str(error.get("code") or "invalid_model_release"),
                str(error.get("message") or "Model release failed."),
            )
    expected_release: dict | None = None
    try:
        require_any_role(principal, {"expert"})
        load_model_asset(root, normalized["model_id"])
        snapshot = read_verified_operation_snapshot(root, audited_operation_id)
        operation = snapshot["operation"]
        expected_owner = _owner_value(operation, normalized)
        expected_release = _expected_release(
            root, normalized, operation, snapshot["events"][0]
        )
        with model_resource_lock(root, "release", normalized["model_id"]):
            candidate = _release_root(
                root, normalized["model_id"], normalized["release_id"]
            )
            for _transition in range(8):
                evidence = _collect_release_evidence(
                    root, expected_owner, expected_release
                )
                state = _classify_evidence(
                    expected_owner, expected_release, evidence
                )
                if state is ReleaseState.COMPLETED:
                    return expected_release
                if state is ReleaseState.NO_CANDIDATE:
                    _require_valid_rollback_request(
                        expected_release, evidence["releases"]
                    )
                    ensure_operation_event(
                        root,
                        audited_operation_id,
                        "model_release.prepared",
                        {
                            "model_id": expected_release["model_id"],
                            "release_id": expected_release["release_id"],
                            "version_id": expected_release["version_id"],
                            "action": expected_release["action"],
                        },
                    )
                    _publish_owner(candidate, expected_owner)
                elif state is ReleaseState.OWNED_CANDIDATE:
                    ensure_operation_event(
                        root,
                        audited_operation_id,
                        "model_release.prepared",
                        {
                            "model_id": expected_release["model_id"],
                            "release_id": expected_release["release_id"],
                            "version_id": expected_release["version_id"],
                            "action": expected_release["action"],
                        },
                    )
                    _write_or_verify_release(
                        candidate / "release.json", expected_release
                    )
                elif state is ReleaseState.RELEASE_VISIBLE_OLD_PROJECTION:
                    _publish_projection(root, expected_release)
                elif state in {
                    ReleaseState.RELEASE_PROJECTED,
                    ReleaseState.RELEASE_ANCESTOR,
                }:
                    if evidence["business_event_matches"]:
                        complete_operation(
                            root,
                            audited_operation_id,
                            _result(expected_release),
                        )
                    else:
                        event_type = (
                            "model_release.rolled_back"
                            if expected_release["action"] == "rollback"
                            else "model_release.published"
                        )
                        ensure_operation_event(
                            root,
                            audited_operation_id,
                            event_type,
                            _event_details(expected_release),
                        )
                else:
                    raise ModelMatchingError(
                        "model_release_integrity_error",
                        "Model release state has no permitted transition.",
                    )
            raise ModelMatchingError(
                "model_release_integrity_error",
                "Model release recovery exceeded its bounded transitions.",
            )
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, ModelMatchingError)
            else ModelMatchingError(
                "model_release_integrity_error",
                "Model release could not be persisted safely.",
            )
        )
        owner = _owner_value(operation, normalized)
        if replayed and error.code == "operation_busy":
            current = load_operation(root, audited_operation_id)
            if current.get("status") == "running":
                raise error
            if current.get("status") == "failed":
                recorded = current.get("error") or {}
                raise ModelMatchingError(
                    str(recorded.get("code") or "invalid_model_release"),
                    str(recorded.get("message") or "Model release failed."),
                )
            if current.get("status") == "completed":
                if expected_release is None:
                    snapshot = read_verified_operation_snapshot(
                        root, audited_operation_id
                    )
                    expected_release = _expected_release(
                        root, normalized, snapshot["operation"], snapshot["events"][0]
                    )
                release = _load_release(
                    root,
                    normalized["model_id"],
                    normalized["release_id"],
                )
                if release != expected_release:
                    raise ModelMatchingError(
                        "model_release_integrity_error",
                        "Completed replay differs from canonical release evidence.",
                    )
                load_current_model_release(root, normalized["model_id"])
                return release
            raise ModelMatchingError(
                "audit_integrity_error",
                "Busy release replay has an invalid operation status.",
            )
        visibility = _candidate_visibility(root, owner)
        if visibility not in {
            _CandidateVisibility.ABSENT,
            _CandidateVisibility.VERIFIED_FOREIGN,
        }:
            if isinstance(exc, ModelMatchingError) and error.code in {
                "audit_integrity_error",
                "model_release_integrity_error",
                "operation_busy",
                "publication_recovery_required",
            }:
                raise error
            raise ModelMatchingError(
                "publication_recovery_required",
                "Model release evidence is visible but recovery is required.",
            ) from exc
        _record_failure(root, audited_operation_id, error)
        if error is exc:
            raise
        raise error from exc
