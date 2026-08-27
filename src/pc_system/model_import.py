import hashlib
import json
import math
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.model_library import (
    load_model_asset,
    model_version_dir,
)
from pc_system.model_matching_audit import (
    append_operation_event,
    complete_operation,
    ensure_operation_event,
    fail_operation,
    load_operation,
    read_verified_operation_events,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_mesh import (
    MeshReader,
    SUPPORTED_MESH_FORMATS,
    UNIT_SCALE_TO_METERS,
    inspect_mesh,
    trimesh_mesh_reader,
)

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "model_id",
        "version_id",
        "operation_id",
        "request_fingerprint",
        "source_format",
        "source_path",
        "source_fingerprint",
        "declared_unit",
        "coordinate_unit",
        "unit_scale_to_m",
        "license",
        "provenance",
        "imported_by",
        "imported_at",
        "status",
        "supersedes_version_id",
        "index_status",
        "artifacts",
        "artifact_fingerprints",
    }
)
_GEOMETRY_FIELDS = frozenset(
    {
        "schema_version",
        "source_format",
        "declared_unit",
        "coordinate_unit",
        "unit_scale_to_m",
        "vertex_count",
        "face_count",
        "bounds_m",
        "is_watertight",
    }
)
_SHA256_LENGTH = 64
_CONCRETE_PATH_TYPE = type(Path())
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
MAX_MODEL_SOURCE_BYTES = 512 * 1024 * 1024
MAX_RETAINED_STAGING_DIRS = 32
MAX_RETAINED_STAGING_BYTES = 2 * 1024 * 1024 * 1024
_STAGING_QUOTA_LOCK_NAME = ".model-staging-quota.lock"


@dataclass(frozen=True)
class _FrozenImportRequest:
    model_id: str | None
    version_id: str | None
    source_path: str | None
    declared_unit: str | None
    license_name: str | None
    provenance_json: str | None
    supersedes_version_id: str | None
    supersedes_is_none: bool
    errors: tuple[str, ...]
    audit_payload_json: str

    def audit_payload(self) -> dict:
        return json.loads(self.audit_payload_json)

    def provenance(self) -> dict:
        if self.provenance_json is None:
            raise ModelMatchingError(
                "invalid_model_version", "provenance must be a JSON object."
            )
        value = json.loads(self.provenance_json)
        if type(value) is not dict:
            raise ModelMatchingError(
                "invalid_model_version", "provenance must be a JSON object."
            )
        return value


@dataclass
class _Reservation:
    path: Path
    descriptor: int
    identity: tuple[int, int]
    operation_id: str
    owner_token: str
    locked: bool = True


@dataclass(frozen=True)
class _OwnedStaging:
    path: Path
    versions_root: Path
    operation_id: str
    owner_token: str
    identity: tuple[int, int]


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _capture_text(value: object, field: str) -> tuple[str | None, dict, str | None]:
    if type(value) is not str:
        return None, {"name": field, "status": "invalid_type"}, field
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return None, {"name": field, "status": "invalid_encoding"}, field
    return value, {"name": field, "status": "ok", "utf8_hex": encoded.hex()}, None


def _capture_path(value: object) -> tuple[str | None, dict, str | None]:
    if type(value) is str:
        raw = value
    elif type(value) is _CONCRETE_PATH_TYPE:
        raw = os.fspath(value)
    else:
        return None, {"name": "source_path", "status": "invalid_type"}, "source_path"
    if type(raw) is not str:
        return None, {"name": "source_path", "status": "invalid_type"}, "source_path"
    try:
        encoded = raw.encode("utf-8")
    except UnicodeError:
        return None, {"name": "source_path", "status": "invalid_encoding"}, "source_path"
    return raw, {
        "name": "source_path",
        "status": "ok",
        "utf8_hex": encoded.hex(),
    }, None


def _copy_json_value(value: object, *, depth: int, budget: list[int]) -> object:
    budget[0] += 1
    if budget[0] > 4096 or depth > 32:
        raise ValueError("provenance exceeds safe capture limits")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("provenance contains a non-finite number")
        return value
    if type(value) is str:
        value.encode("utf-8")
        return value
    if type(value) is list:
        if len(value) > 4096:
            raise ValueError("provenance exceeds safe capture limits")
        return [
            _copy_json_value(item, depth=depth + 1, budget=budget)
            for item in value
        ]
    if type(value) is dict:
        if len(value) > 4096:
            raise ValueError("provenance exceeds safe capture limits")
        copied: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("provenance keys must be exact strings")
            key.encode("utf-8")
            copied[key] = _copy_json_value(
                item, depth=depth + 1, budget=budget
            )
        return copied
    raise ValueError("provenance contains a non-JSON value")


def _capture_provenance(value: object) -> tuple[str | None, dict, str | None]:
    if type(value) is not dict:
        return None, {"name": "provenance", "status": "invalid_type"}, "provenance"
    try:
        copied = _copy_json_value(value, depth=0, budget=[0])
        serialized = json.dumps(
            copied,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded = serialized.encode("utf-8")
    except Exception:
        return None, {"name": "provenance", "status": "invalid_value"}, "provenance"
    return serialized, {
        "name": "provenance",
        "status": "ok",
        "json_utf8_hex": encoded.hex(),
    }, None


def _freeze_import_request(
    *,
    model_id: object,
    version_id: object,
    source_path: object,
    declared_unit: object,
    license_name: object,
    provenance: object,
    supersedes_version_id: object,
) -> _FrozenImportRequest:
    errors: list[str] = []
    fields: list[dict] = []
    frozen_text: dict[str, str | None] = {}
    for name, value in (
        ("model_id", model_id),
        ("version_id", version_id),
        ("declared_unit", declared_unit),
        ("license_name", license_name),
    ):
        captured, audit, error = _capture_text(value, name)
        frozen_text[name] = captured
        fields.append(audit)
        if error is not None:
            errors.append(error)
    captured_path, path_audit, path_error = _capture_path(source_path)
    fields.append(path_audit)
    if path_error is not None:
        errors.append(path_error)
    provenance_json, provenance_audit, provenance_error = _capture_provenance(
        provenance
    )
    fields.append(provenance_audit)
    if provenance_error is not None:
        errors.append(provenance_error)
    supersedes_is_none = supersedes_version_id is None
    if supersedes_is_none:
        frozen_supersedes = None
        fields.append(
            {"name": "supersedes_version_id", "status": "ok", "value": None}
        )
    else:
        frozen_supersedes, supersedes_audit, supersedes_error = _capture_text(
            supersedes_version_id, "supersedes_version_id"
        )
        fields.append(supersedes_audit)
        if supersedes_error is not None:
            errors.append(supersedes_error)
    audit_payload = {
        "schema_id": "model_version.import",
        "schema_version": "1.0",
        "fields": fields,
    }
    return _FrozenImportRequest(
        model_id=frozen_text["model_id"],
        version_id=frozen_text["version_id"],
        source_path=captured_path,
        declared_unit=frozen_text["declared_unit"],
        license_name=frozen_text["license_name"],
        provenance_json=provenance_json,
        supersedes_version_id=frozen_supersedes,
        supersedes_is_none=supersedes_is_none,
        errors=tuple(errors),
        audit_payload_json=json.dumps(
            audit_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _project_root(value: object) -> Path:
    return _exact_path(value, "invalid_project_root")


def _normalize_request(frozen: _FrozenImportRequest) -> dict:
    if frozen.errors:
        raise ModelMatchingError(
            "invalid_model_version",
            f"Invalid import request field: {frozen.errors[0]}.",
        )
    try:
        if frozen.model_id is None or frozen.version_id is None:
            raise ValueError("Model and version identifiers are required.")
        model_id = validate_identifier(frozen.model_id, "model_id")
        version_id = validate_identifier(frozen.version_id, "version_id")
        supersedes = frozen.supersedes_version_id
        if not frozen.supersedes_is_none:
            if supersedes is None:
                raise ValueError("supersedes_version_id is invalid.")
            supersedes = validate_identifier(
                supersedes, "supersedes_version_id"
            )
            if supersedes == version_id:
                raise ValueError("A model version cannot supersede itself.")
        if frozen.source_path is None:
            raise ValueError("source_path is required.")
        if frozen.declared_unit is None or frozen.license_name is None:
            raise ValueError("Model unit and license are required.")
        declared_unit = frozen.declared_unit.strip().lower()
        if declared_unit not in UNIT_SCALE_TO_METERS:
            raise ValueError("Unsupported model unit.")
        license_name = frozen.license_name.strip()
        if not license_name:
            raise ValueError("license_name must not be empty.")
        license_name.encode("utf-8")
        source = Path(frozen.source_path)
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_MESH_FORMATS:
            raise ModelMatchingError(
                "invalid_model_format", f"Unsupported model format: {suffix}"
            )
    except ModelMatchingError:
        raise
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ModelMatchingError("invalid_model_version", str(exc)) from exc
    return {
        "model_id": model_id,
        "version_id": version_id,
        "source_path": source,
        "source_format": suffix[1:],
        "declared_unit": declared_unit,
        "license": license_name,
        "provenance": frozen.provenance(),
        "supersedes_version_id": supersedes,
    }


def _exact_path(value: object, error_code: str) -> Path:
    if type(value) is str:
        raw = value
    elif type(value) is _CONCRETE_PATH_TYPE:
        raw = os.fspath(value)
    else:
        raise ModelMatchingError(
            error_code, "Path value must be an exact string or Path."
        )
    try:
        if type(raw) is not str or "\0" in raw:
            raise ValueError("Path contains an invalid value.")
        raw.encode("utf-8")
        return Path(raw)
    except Exception as exc:
        raise ModelMatchingError(
            error_code, "Path value must be an exact UTF-8 string or Path."
        ) from exc


def _regular_file_descriptor(path: Path) -> tuple[int, tuple[int, int]]:
    before = path.lstat()
    if (
        stat.S_ISLNK(before.st_mode)
        or getattr(before, "st_file_attributes", 0) & _REPARSE_POINT
        or not stat.S_ISREG(before.st_mode)
    ):
        raise OSError("Path is not a regular non-reparse file.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    identity = (opened.st_dev, opened.st_ino)
    if identity != (before.st_dev, before.st_ino) or not stat.S_ISREG(
        opened.st_mode
    ):
        os.close(descriptor)
        raise OSError("Path changed before it was opened.")
    return descriptor, identity


def _require_same_regular_path(path: Path, identity: tuple[int, int]) -> None:
    after = path.lstat()
    if (
        (after.st_dev, after.st_ino) != identity
        or stat.S_ISLNK(after.st_mode)
        or getattr(after, "st_file_attributes", 0) & _REPARSE_POINT
        or not stat.S_ISREG(after.st_mode)
    ):
        raise OSError("Path changed while it was being read.")


def _fingerprint_path(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor, identity = _regular_file_descriptor(path)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    finally:
        _require_same_regular_path(path, identity)
    return digest.hexdigest()


def fingerprint_file(path: Path) -> str:
    try:
        exact_path = _exact_path(path, "invalid_model_path")
        return _fingerprint_path(exact_path)
    except ModelMatchingError:
        raise
    except Exception as exc:
        raise ModelMatchingError(
            "model_file_error", "Model file could not be fingerprinted safely."
        ) from exc


def _canonical_start_event(project_root: Path, operation_id: str) -> tuple[dict, list[dict]]:
    events = read_verified_operation_events(project_root, operation_id)
    started = [
        event for event in events if event["event_type"] == "operation.started"
    ]
    if len(started) != 1 or not events or events[0] != started[0]:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Canonical import operation has no unique first start event.",
        )
    return started[0], events


def _load_json_artifact(path: Path, message: str) -> object:
    try:
        descriptor, identity = _regular_file_descriptor(path)
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        finally:
            _require_same_regular_path(path, identity)
        return value
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelMatchingError(
            "model_version_integrity_error", message
        ) from exc


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("JSON metadata is too deeply nested.")
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise ValueError("JSON metadata contains a non-finite number.")
    if type(value) is str:
        value.encode("utf-8")
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON metadata key is not a string.")
            key.encode("utf-8")
            _validate_json_value(item, depth=depth + 1)
        return
    raise ValueError("JSON metadata contains an unsupported value.")


def _validate_geometry(geometry: object, manifest: dict) -> None:
    if type(geometry) is not dict or set(geometry) != _GEOMETRY_FIELDS:
        raise ValueError("Geometry summary structure is invalid.")
    expected = {
        "schema_version": "1.0",
        "source_format": manifest["source_format"],
        "declared_unit": manifest["declared_unit"],
        "coordinate_unit": "m",
        "unit_scale_to_m": manifest["unit_scale_to_m"],
    }
    if any(geometry.get(key) != value for key, value in expected.items()):
        raise ValueError("Geometry summary does not match its manifest.")
    for field in ("vertex_count", "face_count"):
        value = geometry[field]
        if type(value) is not int or value <= 0:
            raise ValueError("Geometry summary count is invalid.")
    bounds = geometry["bounds_m"]
    if type(bounds) is not dict or set(bounds) != {"min", "max"}:
        raise ValueError("Geometry summary bounds are invalid.")
    for field in ("min", "max"):
        values = bounds[field]
        if type(values) is not list or len(values) != 3 or any(
            type(item) not in {int, float} or not math.isfinite(item)
            for item in values
        ):
            raise ValueError("Geometry summary bounds are invalid.")
    if geometry["is_watertight"] is not None and type(
        geometry["is_watertight"]
    ) is not bool:
        raise ValueError("Geometry watertightness is invalid.")


def _event_details(manifest: dict, manifest_fingerprint: str) -> dict:
    return {
        "model_id": manifest["model_id"],
        "version_id": manifest["version_id"],
        "source_fingerprint": manifest["source_fingerprint"],
        "manifest_fingerprint": manifest_fingerprint,
        "artifact_fingerprints": dict(manifest["artifact_fingerprints"]),
    }


def _operation_result(project_root: Path, manifest: dict) -> dict:
    path = model_version_dir(
        project_root, manifest["model_id"], manifest["version_id"]
    ) / "model_manifest.json"
    return {
        "model_id": manifest["model_id"],
        "version_id": manifest["version_id"],
        "artifact_path": path.relative_to(project_root).as_posix(),
        "source_fingerprint": manifest["source_fingerprint"],
        "manifest_fingerprint": fingerprint_file(path),
    }


def _validate_manifest(
    project_root: Path,
    expected_model_id: str,
    expected_version_id: str,
    manifest: object,
    *,
    artifact_root: Path | None = None,
) -> dict:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_FIELDS:
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model version manifest has an invalid structure.",
        )
    try:
        if (
            manifest["schema_version"] != "1.0"
            or manifest["model_id"] != expected_model_id
            or manifest["version_id"] != expected_version_id
            or manifest["status"] != "imported"
            or manifest["coordinate_unit"] != "m"
            or manifest["index_status"] != "not_indexed"
        ):
            raise ValueError("Manifest identity or state is invalid.")
        for field in (
            "operation_id",
            "request_fingerprint",
            "source_format",
            "source_path",
            "source_fingerprint",
            "declared_unit",
            "license",
            "imported_by",
            "imported_at",
        ):
            if type(manifest[field]) is not str:
                raise ValueError("Manifest text metadata is invalid.")
        validate_identifier(manifest["model_id"], "model_id")
        validate_identifier(manifest["version_id"], "version_id")
        validate_identifier(manifest["operation_id"], "operation_id")
        validate_identifier(manifest["imported_by"], "imported_by")
        if not _is_sha256(manifest["request_fingerprint"]):
            raise ValueError("Manifest request fingerprint is invalid.")
        if not _is_sha256(manifest["source_fingerprint"]):
            raise ValueError("Manifest source fingerprint is invalid.")
        if manifest["source_format"] not in {
            suffix[1:] for suffix in SUPPORTED_MESH_FORMATS
        }:
            raise ValueError("Manifest source format is invalid.")
        if manifest["declared_unit"] not in UNIT_SCALE_TO_METERS:
            raise ValueError("Manifest unit is invalid.")
        if manifest["unit_scale_to_m"] != UNIT_SCALE_TO_METERS[
            manifest["declared_unit"]
        ]:
            raise ValueError("Manifest unit scale is invalid.")
        if not manifest["license"].strip():
            raise ValueError("Manifest license is empty.")
        imported_at = datetime.fromisoformat(manifest["imported_at"])
        if imported_at.tzinfo is None or imported_at.utcoffset() != timedelta(0):
            raise ValueError("Manifest timestamp is not UTC.")
        supersedes = manifest["supersedes_version_id"]
        if supersedes is not None:
            validate_identifier(supersedes, "supersedes_version_id")
            if supersedes == expected_version_id:
                raise ValueError("Manifest supersedes itself.")
        _validate_json_value(manifest["provenance"])
        if type(manifest["provenance"]) is not dict:
            raise ValueError("Manifest provenance is invalid.")
        expected_source = f"source/model.{manifest['source_format']}"
        if manifest["source_path"] != expected_source:
            raise ValueError("Manifest source path is invalid.")
        expected_artifacts = {
            "source": expected_source,
            "source_geometry": "source_geometry.json",
        }
        if manifest["artifacts"] != expected_artifacts:
            raise ValueError("Manifest artifact paths are invalid.")
        fingerprints = manifest["artifact_fingerprints"]
        if type(fingerprints) is not dict or set(fingerprints) != set(
            expected_artifacts
        ) or any(not _is_sha256(value) for value in fingerprints.values()):
            raise ValueError("Manifest artifact fingerprints are invalid.")
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model version manifest contains invalid metadata.",
        ) from exc

    root = artifact_root or model_version_dir(
        project_root, expected_model_id, expected_version_id
    )
    source_path = root / manifest["artifacts"]["source"]
    geometry_path = root / manifest["artifacts"]["source_geometry"]
    manifest_path = root / "model_manifest.json"
    try:
        _require_plain_directory(
            root,
            "Model version root contains a link or reparse point.",
        )
        _require_plain_directory(
            source_path.parent,
            "Model source directory contains a link or reparse point.",
        )
        actual_source = fingerprint_file(source_path)
        actual_geometry = fingerprint_file(geometry_path)
        actual_manifest = fingerprint_file(manifest_path)
    except (OSError, ModelMatchingError) as exc:
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model version artifact could not be read.",
        ) from exc
    if (
        actual_source != manifest["source_fingerprint"]
        or actual_source != manifest["artifact_fingerprints"]["source"]
        or actual_geometry
        != manifest["artifact_fingerprints"]["source_geometry"]
    ):
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model version artifact fingerprint is invalid.",
        )
    geometry = _load_json_artifact(
        geometry_path, "Model geometry summary could not be read."
    )
    try:
        _validate_geometry(geometry, manifest)
        operation = load_operation(project_root, manifest["operation_id"])
        started, events = _canonical_start_event(
            project_root, manifest["operation_id"]
        )
        if (
            operation["operation_type"] != "model_version.import"
            or started["operation_id"] != manifest["operation_id"]
            or started["actor_id"] != manifest["imported_by"]
            or started["timestamp"] != manifest["imported_at"]
            or started["details"]["request_fingerprint"]
            != manifest["request_fingerprint"]
        ):
            raise ValueError("Manifest does not match its canonical operation.")
        source_details = {
            "model_id": expected_model_id,
            "version_id": expected_version_id,
            "source_fingerprint": manifest["source_fingerprint"],
        }
        fingerprinted = [
            event
            for event in events
            if event["event_type"] == "model_source.fingerprinted"
        ]
        prepared = [
            event for event in events if event["event_type"] == "model_version.prepared"
        ]
        expected_details = _event_details(manifest, actual_manifest)
        if (
            len(fingerprinted) != 1
            or fingerprinted[0]["details"] != source_details
            or len(prepared) != 1
            or prepared[0]["details"] != expected_details
        ):
            raise ValueError("Manifest has no matching prepared audit evidence.")
        published = [
            event for event in events if event["event_type"] == "model_version.published"
        ]
        if len(published) > 1 or (
            published and published[0]["details"] != expected_details
        ):
            raise ValueError("Manifest publication audit evidence is invalid.")
        if operation["status"] == "completed":
            expected_result = {
                "model_id": expected_model_id,
                "version_id": expected_version_id,
                "artifact_path": manifest_path.relative_to(project_root).as_posix(),
                "source_fingerprint": manifest["source_fingerprint"],
                "manifest_fingerprint": actual_manifest,
            }
            if len(published) != 1 or operation.get("result") != expected_result:
                raise ValueError("Completed import result is not bound to its manifest.")
        elif operation["status"] != "running":
            raise ValueError("Published manifest belongs to a failed operation.")
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model version ownership audit is invalid.",
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model version ownership audit is invalid.",
        ) from exc
    return json.loads(
        json.dumps(manifest, ensure_ascii=False, allow_nan=False)
    )


def load_model_version(
    project_root: Path, model_id: str, version_id: str
) -> dict:
    project_root = _project_root(project_root)
    try:
        normalized_model_id = validate_identifier(model_id, "model_id")
        normalized_version_id = validate_identifier(version_id, "version_id")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("invalid_model_version", str(exc)) from exc
    root = model_version_dir(
        project_root, normalized_model_id, normalized_version_id
    )
    path = root / "model_manifest.json"
    try:
        for directory in (
            project_root / "models",
            project_root / "models" / normalized_model_id,
            root.parent,
            root,
        ):
            _require_plain_directory(
                directory,
                "Model version path contains a link or reparse point.",
            )
        manifest_info = path.lstat()
        if (
            not stat.S_ISREG(manifest_info.st_mode)
            or stat.S_ISLNK(manifest_info.st_mode)
            or getattr(manifest_info, "st_file_attributes", 0)
            & _REPARSE_POINT
        ):
            raise ModelMatchingError(
                "model_version_integrity_error",
                "Model version manifest is not a regular file.",
            )
    except FileNotFoundError:
        raise ModelMatchingError(
            "model_version_not_found",
            f"Model version not found: {normalized_model_id}/{normalized_version_id}",
        )
    manifest = _load_json_artifact(
        path, "Model version manifest could not be read."
    )
    return _validate_manifest(
        project_root, normalized_model_id, normalized_version_id, manifest
    )


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _require_plain_directory(path: Path, message: str) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    ):
        raise ModelMatchingError(
            "model_version_integrity_error", message
        )


def list_model_versions(project_root: Path, model_id: str) -> list[dict]:
    project_root = _project_root(project_root)
    try:
        normalized_model_id = validate_identifier(model_id, "model_id")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("invalid_model_version", str(exc)) from exc
    model_root = project_root / "models" / normalized_model_id
    models_root = project_root / "models"
    versions_root = model_root / "versions"
    versions: list[dict] = []
    try:
        try:
            models_root.lstat()
        except FileNotFoundError:
            return []
        _require_plain_directory(
            models_root,
            "Model catalog root contains a link or reparse point.",
        )
        try:
            model_root.lstat()
        except FileNotFoundError:
            return []
        _require_plain_directory(
            model_root,
            "Model version catalog contains a link or reparse point.",
        )
        try:
            versions_root.lstat()
        except FileNotFoundError:
            return []
        _require_plain_directory(
            versions_root,
            "Model version catalog contains a link or reparse point.",
        )
        for candidate in sorted(versions_root.iterdir(), key=lambda item: item.name):
            if candidate.name.startswith("."):
                continue
            if _is_link_or_reparse(candidate):
                raise ModelMatchingError(
                    "model_version_integrity_error",
                    "Model version directory must not be a link.",
                )
            if not candidate.is_dir() or not (
                candidate / "model_manifest.json"
            ).is_file():
                continue
            try:
                validate_identifier(candidate.name, "version_id")
            except ValueError as exc:
                raise ModelMatchingError(
                    "model_version_integrity_error",
                    "Model version directory name is invalid.",
                ) from exc
            versions.append(
                load_model_version(
                    project_root, normalized_model_id, candidate.name
                )
            )
    except ModelMatchingError:
        raise
    except OSError as exc:
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model version catalog could not be read.",
        ) from exc
    return sorted(versions, key=lambda item: item["version_id"])


def _capture_source(
    source: Path,
    destination: Path,
    *,
    staging_bytes_available: int = MAX_RETAINED_STAGING_BYTES,
) -> str:
    digest = hashlib.sha256()
    captured_bytes = 0
    try:
        descriptor, identity = _regular_file_descriptor(source)
        try:
            with os.fdopen(descriptor, "rb") as incoming:
                destination.parent.mkdir(parents=True, exist_ok=False)
                with destination.open("xb") as outgoing:
                    for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                        captured_bytes += len(chunk)
                        if captured_bytes > MAX_MODEL_SOURCE_BYTES:
                            raise ModelMatchingError(
                                "model_source_too_large",
                                "Model source exceeds the configured byte limit.",
                            )
                        if captured_bytes > staging_bytes_available:
                            raise ModelMatchingError(
                                "model_staging_quota_exceeded",
                                "Retained model staging byte quota would be exceeded.",
                            )
                        digest.update(chunk)
                        outgoing.write(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
        finally:
            _require_same_regular_path(source, identity)
    except FileNotFoundError as exc:
        raise ModelMatchingError(
            "model_source_not_found", "Model source file does not exist."
        ) from exc
    except ModelMatchingError:
        raise
    except OSError as exc:
        raise ModelMatchingError(
            "model_source_read_error", "Model source file could not be captured."
        ) from exc
    return digest.hexdigest()


def _staging_quota_error(message: str) -> ModelMatchingError:
    return ModelMatchingError("model_staging_quota_exceeded", message)


def _plain_directory_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    ):
        raise OSError("Staging quota path is not a plain directory.")
    return info.st_dev, info.st_ino


def _staging_tree_bytes(root: Path) -> int:
    root_identity = _plain_directory_identity(root)
    total = 0
    for child in root.iterdir():
        info = child.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise OSError("Staging quota tree contains a link or reparse point.")
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
        elif stat.S_ISDIR(info.st_mode):
            total += _staging_tree_bytes(child)
        else:
            raise OSError("Staging quota tree contains an unsupported entry.")
    if _plain_directory_identity(root) != root_identity:
        raise OSError("Staging quota tree changed while being scanned.")
    return total


def _retained_staging_usage(models_root: Path) -> tuple[int, int]:
    try:
        models_identity = _plain_directory_identity(models_root)
        count = 0
        total = 0
        for model_root in models_root.iterdir():
            if model_root.name == _STAGING_QUOTA_LOCK_NAME:
                info = model_root.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
                ):
                    raise OSError("Staging quota lock path is not a plain file.")
                continue
            model_identity = _plain_directory_identity(model_root)
            versions_root = model_root / "versions"
            try:
                versions_root.lstat()
            except FileNotFoundError:
                continue
            versions_identity = _plain_directory_identity(versions_root)
            for candidate in versions_root.iterdir():
                if not candidate.name.startswith(".p15-model-"):
                    continue
                count += 1
                total += _staging_tree_bytes(candidate)
                if (
                    count > MAX_RETAINED_STAGING_DIRS
                    or total > MAX_RETAINED_STAGING_BYTES
                ):
                    raise _staging_quota_error(
                        "Retained model staging quota is already exceeded."
                    )
            if _plain_directory_identity(versions_root) != versions_identity:
                raise OSError("Versions root changed during staging quota scan.")
            if _plain_directory_identity(model_root) != model_identity:
                raise OSError("Model root changed during staging quota scan.")
        if _plain_directory_identity(models_root) != models_identity:
            raise OSError("Models root changed while staging quota was scanned.")
        return count, total
    except ModelMatchingError:
        raise
    except OSError as exc:
        raise _staging_quota_error(
            "Retained model staging could not be scanned safely."
        ) from exc


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("Reservation metadata write made no progress.")
        offset += written


def _lock_descriptor(descriptor: int, *, blocking: bool = False) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(descriptor, mode, 1)
    else:
        mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(descriptor, mode)


def _unlock_descriptor(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _reservation_path_matches_handle(reservation: _Reservation) -> bool:
    try:
        info = reservation.path.lstat()
        opened = os.fstat(reservation.descriptor)
        return (
            (info.st_dev, info.st_ino) == reservation.identity
            and (opened.st_dev, opened.st_ino) == reservation.identity
            and stat.S_ISREG(info.st_mode)
            and stat.S_ISREG(opened.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not (
                getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
            )
        )
    except OSError:
        return False


def _reserve_lock(
    lock_root: Path,
    lock_name: str,
    operation_id: str,
    *,
    blocking: bool,
    contention_code: str,
    contention_message: str,
) -> _Reservation:
    lock_root.mkdir(parents=True, exist_ok=True)
    path = lock_root / lock_name
    owner_token = uuid.uuid4().hex
    try:
        descriptor = os.open(
            path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        )
        info = os.fstat(descriptor)
        reservation = _Reservation(
            path,
            descriptor,
            (info.st_dev, info.st_ino),
            operation_id,
            owner_token,
            locked=False,
        )
        if not _reservation_path_matches_handle(reservation):
            raise ModelMatchingError(
                "model_version_reservation_integrity_error",
                "Model version reservation path is not a plain file.",
        )
        if info.st_size == 0:
            _write_all(descriptor, b"\0")
            os.fsync(descriptor)
        _lock_descriptor(descriptor, blocking=blocking)
        reservation.locked = True
    except (BlockingIOError, PermissionError) as exc:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise ModelMatchingError(
            contention_code, contention_message
        ) from exc
    except ModelMatchingError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise
    except OSError as exc:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise ModelMatchingError(
            "model_version_reservation_error",
            "Model version reservation could not be acquired.",
        ) from exc
    try:
        if not _reservation_path_matches_handle(reservation):
            raise ModelMatchingError(
                "model_version_reservation_integrity_error",
                "Model version reservation path changed while locked.",
            )
        payload = json.dumps(
            {
                "schema_version": "1.0",
                "operation_id": operation_id,
                "owner_token": owner_token,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        return reservation
    except ModelMatchingError:
        _release_reservation(reservation)
        raise
    except OSError as exc:
        _release_reservation(reservation)
        raise ModelMatchingError(
            "model_version_reservation_error",
            "Model reservation metadata could not be persisted.",
        ) from exc


def _reserve_version(model_root: Path, version_id: str, operation_id: str) -> _Reservation:
    return _reserve_lock(
        model_root,
        f".version-{version_id}.lock",
        operation_id,
        blocking=False,
        contention_code="operation_busy",
        contention_message="Model version identity is currently reserved.",
    )


def _reserve_staging_quota(project_root: Path, operation_id: str) -> _Reservation:
    return _reserve_lock(
        project_root / "models",
        _STAGING_QUOTA_LOCK_NAME,
        operation_id,
        blocking=True,
        contention_code="model_staging_quota_exceeded",
        contention_message="Model staging quota is currently being updated.",
    )


def _write_staged_json_with_quota(
    project_root: Path,
    operation_id: str,
    data: dict,
    path: Path,
) -> None:
    payload_size = len(
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    )
    reservation = _reserve_staging_quota(project_root, operation_id)
    try:
        _, retained_bytes = _retained_staging_usage(project_root / "models")
        if retained_bytes + payload_size > MAX_RETAINED_STAGING_BYTES:
            raise _staging_quota_error(
                "Retained model staging byte quota would be exceeded."
            )
        write_json(data, path)
    finally:
        _release_reservation(reservation)


def _release_reservation(reservation: _Reservation | None) -> None:
    if reservation is None:
        return
    integrity_error = not _reservation_path_matches_handle(reservation)
    release_error: OSError | None = None
    try:
        if reservation.locked:
            try:
                _unlock_descriptor(reservation.descriptor)
                reservation.locked = False
            except OSError as exc:
                release_error = exc
    finally:
        try:
            os.close(reservation.descriptor)
        except OSError as exc:
            release_error = release_error or exc
    if integrity_error:
        raise ModelMatchingError(
            "model_version_reservation_integrity_error",
            "Model version reservation path changed while locked.",
        )
    if release_error is not None:
        raise ModelMatchingError(
            "model_version_reservation_error",
            "Model version reservation could not be released safely.",
        ) from release_error


def _staging_matches_owner(owned: _OwnedStaging) -> bool:
    staging = owned.path
    if (
        staging.parent != owned.versions_root
        or staging.name != f".p15-model-{owned.operation_id}"
    ):
        return False
    try:
        if _is_link_or_reparse(staging):
            return False
        info = staging.lstat()
        if (info.st_dev, info.st_ino) != owned.identity:
            return False
        marker = staging / ".operation-owner.json"
        owner = _load_json_artifact(marker, "Staging ownership marker is invalid.")
        if owner != {
            "schema_version": "1.0",
            "operation_id": owned.operation_id,
            "owner_token": owned.owner_token,
        }:
            return False
        for root, directories, files in os.walk(staging, followlinks=False):
            for name in [*directories, *files]:
                if _is_link_or_reparse(Path(root) / name):
                    return False
        info = staging.lstat()
        return (info.st_dev, info.st_ino) == owned.identity
    except (ModelMatchingError, OSError):
        return False


def _import_error(exc: Exception) -> ModelMatchingError:
    if isinstance(exc, ModelMatchingError):
        return exc
    if isinstance(exc, ValueError):
        return ModelMatchingError("invalid_model_version", str(exc))
    return ModelMatchingError(
        "model_version_import_failed", "Model version import failed."
    )


def _audit_error() -> ModelMatchingError:
    return ModelMatchingError(
        "audit_persistence_error",
        "Model version audit operation could not be persisted.",
    )


def _both_operation_busy(first: Exception, second: Exception) -> bool:
    return all(
        isinstance(error, ModelMatchingError)
        and error.code == "operation_busy"
        for error in (first, second)
    )


def _record_failure(project_root: Path, operation_id: str, error: ModelMatchingError) -> None:
    try:
        fail_operation(project_root, operation_id, error.code, str(error))
    except Exception as exc:
        try:
            current = load_operation(project_root, operation_id)
        except Exception as load_exc:
            if _both_operation_busy(exc, load_exc):
                raise exc
            raise _audit_error() from load_exc
        if current.get("status") == "failed" and current.get("error") == {
            "code": error.code,
            "message": str(error),
        }:
            return
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise exc
        raise _audit_error() from exc


def _record_replay_failure(
    project_root: Path,
    *,
    principal: Principal,
    request_id: str,
    original_operation_id: str,
    requested_operation_id: str,
    error: ModelMatchingError,
) -> None:
    audit_id = f"audit-import-{uuid.uuid4()}"
    details = {
        "attempt_id": audit_id,
        "attempted_mutation": "model_version.import",
        "failure_code": error.code,
        "original_operation_id": original_operation_id,
        "requested_operation_id": requested_operation_id,
    }
    started = False
    try:
        _, replayed = start_operation(
            project_root,
            operation_id=audit_id,
            operation_type="model_version.replay_failure",
            principal=principal,
            request_id=request_id,
            idempotency_key=audit_id,
            request_payload=details,
        )
        if replayed:
            raise ModelMatchingError(
                "audit_integrity_error",
                "Replay failure audit was unexpectedly replayed.",
            )
        started = True
        append_operation_event(
            project_root, audit_id, "model_version.replay_failed", details
        )
        fail_operation(project_root, audit_id, error.code, str(error))
    except Exception as exc:
        if started:
            try:
                fail_operation(
                    project_root,
                    audit_id,
                    "audit_persistence_error",
                    "Replay failure audit could not be made durable.",
                )
            except Exception:
                pass
        raise _audit_error() from exc


def _source_event_fingerprint(events: list[dict]) -> str | None:
    fingerprinted = [
        event for event in events if event["event_type"] == "model_source.fingerprinted"
    ]
    if not fingerprinted:
        return None
    if len(fingerprinted) != 1:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Import operation has no unique source fingerprint event.",
        )
    value = fingerprinted[0]["details"].get("source_fingerprint")
    if not _is_sha256(value):
        raise ModelMatchingError(
            "audit_integrity_error", "Import source fingerprint event is invalid."
        )
    return value


def _failed_operation_error(operation: dict) -> ModelMatchingError:
    error = operation.get("error") or {}
    return ModelMatchingError(
        str(error.get("code") or "model_version_import_failed"),
        str(error.get("message") or "Model version import failed."),
    )


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _load_prepared_staging(
    project_root: Path,
    normalized: dict,
    operation_id: str,
    staging: Path,
) -> dict:
    final = model_version_dir(
        project_root, normalized["model_id"], normalized["version_id"]
    )
    if staging.parent != final.parent or staging.name != f".p15-model-{operation_id}":
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Prepared staging path is not owned by the import operation.",
        )
    for directory in (
        project_root / "models",
        final.parent.parent,
        final.parent,
        staging,
    ):
        _require_plain_directory(
            directory,
            "Prepared staging path contains a link or reparse point.",
        )
    owner = _load_json_artifact(
        staging / ".operation-owner.json",
        "Prepared staging ownership marker is invalid.",
    )
    if (
        type(owner) is not dict
        or set(owner) != {"schema_version", "operation_id", "owner_token"}
        or owner.get("schema_version") != "1.0"
        or owner.get("operation_id") != operation_id
        or type(owner.get("owner_token")) is not str
        or len(owner["owner_token"]) != 32
        or any(character not in "0123456789abcdef" for character in owner["owner_token"])
    ):
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Prepared staging ownership marker is invalid.",
        )
    manifest = _load_json_artifact(
        staging / "model_manifest.json",
        "Prepared staging manifest could not be read.",
    )
    validated = _validate_manifest(
        project_root,
        normalized["model_id"],
        normalized["version_id"],
        manifest,
        artifact_root=staging,
    )
    if validated["operation_id"] != operation_id:
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Prepared staging belongs to another import operation.",
        )
    return validated


def _terminalize_recovery_required(
    project_root: Path,
    operation_id: str,
    normalized: dict,
    reason: str,
) -> None:
    error = ModelMatchingError(
        "publication_recovery_required",
        "Prepared model publication could not be reconciled safely.",
    )
    try:
        ensure_operation_event(
            project_root,
            operation_id,
            "model_version.recovery_required",
            {
                "model_id": normalized["model_id"],
                "version_id": normalized["version_id"],
                "reason": reason,
            },
        )
    except Exception as exc:
        raise _audit_error() from exc
    _record_failure(project_root, operation_id, error)
    raise error


def _complete_recovery(
    project_root: Path,
    operation_id: str,
    manifest: dict,
) -> None:
    manifest_path = model_version_dir(
        project_root, manifest["model_id"], manifest["version_id"]
    ) / "model_manifest.json"
    details = _event_details(manifest, fingerprint_file(manifest_path))
    ensure_operation_event(
        project_root, operation_id, "model_version.published", details
    )
    ensure_operation_event(
        project_root,
        operation_id,
        "model_version.recovered",
        {
            "model_id": manifest["model_id"],
            "version_id": manifest["version_id"],
            "manifest_fingerprint": details["manifest_fingerprint"],
        },
    )
    result = _operation_result(project_root, manifest)
    try:
        complete_operation(project_root, operation_id, result)
    except Exception as exc:
        try:
            current = load_operation(project_root, operation_id)
        except Exception as load_exc:
            if _both_operation_busy(exc, load_exc):
                raise exc
            raise _audit_error() from load_exc
        if current.get("status") == "completed" and current.get("result") == result:
            return
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise _audit_error() from exc


def _reconcile_running_import(
    project_root: Path,
    operation_id: str,
    normalized: dict,
) -> dict:
    final = model_version_dir(
        project_root, normalized["model_id"], normalized["version_id"]
    )
    staging = final.parent / f".p15-model-{operation_id}"
    if _path_entry_exists(final):
        try:
            manifest = load_model_version(
                project_root, normalized["model_id"], normalized["version_id"]
            )
            if manifest["operation_id"] != operation_id:
                raise ModelMatchingError(
                    "model_version_integrity_error",
                    "Published model belongs to another import operation.",
                )
        except ModelMatchingError as exc:
            if exc.code == "operation_busy":
                raise
            _terminalize_recovery_required(
                project_root, operation_id, normalized, "final_unprovable"
            )
        _complete_recovery(project_root, operation_id, manifest)
        return manifest
    if not _path_entry_exists(staging):
        _terminalize_recovery_required(
            project_root, operation_id, normalized, "artifacts_missing"
        )
    try:
        manifest = _load_prepared_staging(
            project_root, normalized, operation_id, staging
        )
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        _terminalize_recovery_required(
            project_root, operation_id, normalized, "staging_unprovable"
        )
    try:
        staging.rename(final)
    except OSError:
        if _path_entry_exists(final):
            try:
                manifest = load_model_version(
                    project_root,
                    normalized["model_id"],
                    normalized["version_id"],
                )
                if manifest["operation_id"] != operation_id:
                    raise ModelMatchingError(
                        "model_version_integrity_error",
                        "Published model belongs to another import operation.",
                    )
            except ModelMatchingError as exc:
                if exc.code == "operation_busy":
                    raise
                _terminalize_recovery_required(
                    project_root, operation_id, normalized, "rename_unprovable"
                )
        else:
            _terminalize_recovery_required(
                project_root, operation_id, normalized, "rename_unconfirmed"
            )
    _complete_recovery(project_root, operation_id, manifest)
    return manifest


def _replay_import(
    project_root: Path,
    operation: dict,
    normalized: dict,
) -> dict:
    _, events = _canonical_start_event(project_root, operation["operation_id"])
    expected_source_fingerprint = _source_event_fingerprint(events)
    if expected_source_fingerprint is None:
        if operation["status"] == "failed":
            raise _failed_operation_error(operation)
        if operation["status"] == "running":
            raise ModelMatchingError(
                "operation_busy", "The original model import is still running."
            )
        raise ModelMatchingError(
            "audit_integrity_error",
            "Completed import has no source fingerprint evidence.",
        )
    if operation["status"] == "running":
        manifest = _reconcile_running_import(
            project_root, operation["operation_id"], normalized
        )
        try:
            current_source_fingerprint = fingerprint_file(
                normalized["source_path"]
            )
        except ModelMatchingError as exc:
            raise ModelMatchingError(
                "idempotency_conflict",
                "Idempotent model import source is no longer available.",
            ) from exc
        if current_source_fingerprint != expected_source_fingerprint:
            raise ModelMatchingError(
                "idempotency_conflict",
                "Idempotent model import source bytes have changed.",
            )
        return load_model_version(
            project_root, manifest["model_id"], manifest["version_id"]
        )
    current_source_fingerprint = fingerprint_file(normalized["source_path"])
    if current_source_fingerprint != expected_source_fingerprint:
        raise ModelMatchingError(
            "idempotency_conflict",
            "Idempotent model import source bytes have changed.",
        )
    if operation["status"] == "failed":
        raise _failed_operation_error(operation)
    final = model_version_dir(
        project_root, normalized["model_id"], normalized["version_id"]
    )
    if not (final / "model_manifest.json").is_file():
        if operation["status"] == "running":
            raise ModelMatchingError(
                "operation_busy", "The original model import is still running."
            )
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Completed model import has no immutable manifest.",
        )
    manifest = load_model_version(
        project_root, normalized["model_id"], normalized["version_id"]
    )
    if manifest["operation_id"] != operation["operation_id"]:
        raise ModelMatchingError(
            "model_version_exists",
            "Model version identity already belongs to another operation.",
        )
    if manifest["source_fingerprint"] != current_source_fingerprint:
        raise ModelMatchingError(
            "idempotency_conflict",
            "Idempotent model import source bytes have changed.",
        )
    return manifest


def import_model_version(
    project_root: Path,
    *,
    model_id: str,
    version_id: str,
    source_path: Path,
    declared_unit: str,
    license_name: str,
    provenance: dict,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
    supersedes_version_id: str | None = None,
    mesh_reader: MeshReader = trimesh_mesh_reader,
) -> dict:
    project_root = _project_root(project_root)
    frozen = _freeze_import_request(
        model_id=model_id,
        version_id=version_id,
        source_path=source_path,
        declared_unit=declared_unit,
        license_name=license_name,
        provenance=provenance,
        supersedes_version_id=supersedes_version_id,
    )
    operation, replayed = start_operation(
        project_root,
        operation_id=operation_id,
        operation_type="model_version.import",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=frozen.audit_payload(),
    )
    if replayed:
        try:
            require_any_role(principal, {"expert"})
            normalized = _normalize_request(frozen)
            replay_model_root = model_version_dir(
                project_root,
                normalized["model_id"],
                normalized["version_id"],
            ).parent.parent
            replay_reservation = _reserve_version(
                replay_model_root,
                normalized["version_id"],
                operation["operation_id"],
            )
            try:
                return _replay_import(
                    project_root,
                    operation,
                    normalized,
                )
            finally:
                _release_reservation(replay_reservation)
        except Exception as exc:
            error = _import_error(exc)
            _record_replay_failure(
                project_root,
                principal=principal,
                request_id=request_id,
                original_operation_id=operation["operation_id"],
                requested_operation_id=operation_id,
                error=error,
            )
            if error is exc:
                raise
            raise error from exc

    audited_operation_id = operation["operation_id"]
    reservation: _Reservation | None = None
    quota_reservation: _Reservation | None = None
    staging: Path | None = None
    owned_staging: _OwnedStaging | None = None
    rename_outcome = "not_attempted"
    versions_root: Path | None = None
    try:
        require_any_role(principal, {"expert"})
        normalized = _normalize_request(frozen)
        if normalized["supersedes_version_id"] is not None:
            load_model_version(
                project_root,
                normalized["model_id"],
                normalized["supersedes_version_id"],
            )
        load_model_asset(project_root, normalized["model_id"])
        final = model_version_dir(
            project_root, normalized["model_id"], normalized["version_id"]
        )
        versions_root = final.parent
        model_root = versions_root.parent
        if final.exists():
            raise ModelMatchingError(
                "model_version_exists", "Model version identity already exists."
            )
        reservation = _reserve_version(
            model_root, normalized["version_id"], audited_operation_id
        )
        if final.exists():
            raise ModelMatchingError(
                "model_version_exists", "Model version identity already exists."
            )
        quota_reservation = _reserve_staging_quota(
            project_root, audited_operation_id
        )
        try:
            retained_count, retained_bytes = _retained_staging_usage(
                project_root / "models"
            )
            if retained_count >= MAX_RETAINED_STAGING_DIRS:
                raise _staging_quota_error(
                    "Retained model staging directory quota would be exceeded."
                )
            versions_root.mkdir(parents=True, exist_ok=True)
            staging = versions_root / f".p15-model-{audited_operation_id}"
            staging_owner_token = uuid.uuid4().hex
            owner_marker = {
                "schema_version": "1.0",
                "operation_id": audited_operation_id,
                "owner_token": staging_owner_token,
            }
            marker_bytes = len(
                json.dumps(
                    owner_marker, ensure_ascii=False, indent=2
                ).encode("utf-8")
            )
            if retained_bytes + marker_bytes > MAX_RETAINED_STAGING_BYTES:
                raise _staging_quota_error(
                    "Retained model staging byte quota would be exceeded."
                )
            staging.mkdir(exist_ok=False)
            staging_info = staging.lstat()
            owned_staging = _OwnedStaging(
                staging,
                versions_root,
                audited_operation_id,
                staging_owner_token,
                (staging_info.st_dev, staging_info.st_ino),
            )
            write_json(owner_marker, staging / ".operation-owner.json")
            relative_source = f"source/model.{normalized['source_format']}"
            staged_source = staging / relative_source
            source_fingerprint = _capture_source(
                normalized["source_path"],
                staged_source,
                staging_bytes_available=(
                    MAX_RETAINED_STAGING_BYTES
                    - retained_bytes
                    - marker_bytes
                ),
            )
        finally:
            held_quota = quota_reservation
            quota_reservation = None
            _release_reservation(held_quota)
        source_details = {
            "model_id": normalized["model_id"],
            "version_id": normalized["version_id"],
            "source_fingerprint": source_fingerprint,
        }
        try:
            append_operation_event(
                project_root,
                audited_operation_id,
                "model_source.fingerprinted",
                source_details,
            )
        except Exception as exc:
            raise _audit_error() from exc
        geometry = inspect_mesh(
            staged_source,
            normalized["declared_unit"],
            reader=mesh_reader,
        )
        geometry_path = staging / "source_geometry.json"
        _write_staged_json_with_quota(
            project_root, audited_operation_id, geometry, geometry_path
        )
        artifact_fingerprints = {
            "source": source_fingerprint,
            "source_geometry": fingerprint_file(geometry_path),
        }
        started, _ = _canonical_start_event(
            project_root, audited_operation_id
        )
        manifest = {
            "schema_version": "1.0",
            "model_id": normalized["model_id"],
            "version_id": normalized["version_id"],
            "operation_id": audited_operation_id,
            "request_fingerprint": started["details"]["request_fingerprint"],
            "source_format": geometry["source_format"],
            "source_path": relative_source,
            "source_fingerprint": source_fingerprint,
            "declared_unit": geometry["declared_unit"],
            "coordinate_unit": "m",
            "unit_scale_to_m": geometry["unit_scale_to_m"],
            "license": normalized["license"],
            "provenance": normalized["provenance"],
            "imported_by": started["actor_id"],
            "imported_at": started["timestamp"],
            "status": "imported",
            "supersedes_version_id": normalized["supersedes_version_id"],
            "index_status": "not_indexed",
            "artifacts": {
                "source": relative_source,
                "source_geometry": "source_geometry.json",
            },
            "artifact_fingerprints": artifact_fingerprints,
        }
        manifest_path = staging / "model_manifest.json"
        _write_staged_json_with_quota(
            project_root, audited_operation_id, manifest, manifest_path
        )
        manifest_fingerprint = fingerprint_file(manifest_path)
        details = _event_details(manifest, manifest_fingerprint)
        try:
            append_operation_event(
                project_root,
                audited_operation_id,
                "model_version.prepared",
                details,
            )
        except Exception as exc:
            raise _audit_error() from exc
        rename_outcome = "unknown"
        staging.rename(final)
        rename_outcome = "published"
        staging = None
        (final / ".operation-owner.json").unlink()
        try:
            append_operation_event(
                project_root,
                audited_operation_id,
                "model_version.published",
                details,
            )
            complete_operation(
                project_root,
                audited_operation_id,
                _operation_result(project_root, manifest),
            )
        except Exception as exc:
            raise ModelMatchingError(
                "publication_recovery_required",
                "Model version is visible but its audit publication must be recovered.",
            ) from exc
        return load_model_version(
            project_root, normalized["model_id"], normalized["version_id"]
        )
    except Exception as exc:
        error = _import_error(exc)
        if rename_outcome in {"unknown", "published"}:
            if error.code != "publication_recovery_required":
                error = ModelMatchingError(
                    "publication_recovery_required",
                    "Model version is visible but its audit publication must be recovered.",
                )
            if error is exc:
                raise
            raise error from exc
        if owned_staging is not None:
            ownership_confirmed = _staging_matches_owner(owned_staging)
            try:
                append_operation_event(
                    project_root,
                    audited_operation_id,
                    "model_version.cleanup_deferred",
                    {
                        "model_id": normalized["model_id"],
                        "version_id": normalized["version_id"],
                        "reason": (
                            "automatic_cleanup_disabled"
                            if ownership_confirmed
                            else "ownership_unconfirmed"
                        ),
                    },
                )
            except Exception as audit_exc:
                raise _audit_error() from audit_exc
            if not ownership_confirmed:
                error = ModelMatchingError(
                    "model_version_cleanup_required",
                    "Model staging ownership could not be confirmed safely.",
                )
        _record_failure(project_root, audited_operation_id, error)
        if error is exc:
            raise
        raise error from exc
    finally:
        _release_reservation(quota_reservation)
        _release_reservation(reservation)
