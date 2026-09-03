"""扫描版本的不可变工件与公开读取门禁。"""

import hashlib
import json
import os
import stat
from pathlib import Path

from .identifiers import validate_identifier
from .model_import import _freeze_import_request
from .model_library import load_model_asset, model_version_dir
from .model_matching_audit import read_verified_operation_snapshot
from .model_matching_errors import ModelMatchingError
from .model_sampling import _canonical_json_bytes, _publish_exact_json

MAX_IMPORT_BYTES = 512 * 1024 * 1024
MAX_RETAINED_BYTES = 2 * 1024 * 1024 * 1024
RESOURCE_LIMITS = {"source_bytes": 128 * 1024 * 1024, "point_count": 1_000_000,
                   "import_bytes": MAX_IMPORT_BYTES, "retained_bytes": MAX_RETAINED_BYTES,
                   "decode_workers": 2, "timeout_seconds": 120}


def integrity(message="扫描版本的工件、所有者或审计证据不一致。"):
    return ModelMatchingError("reference_integrity_error", message)


def digest(value):
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def audit_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def plain(path, *, directory=False):
    info = Path(path).lstat()
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400):
        raise integrity("扫描工件路径不是普通目录或文件。")
    return info


def read_json(path):
    info = plain(path)
    if info.st_size > MAX_IMPORT_BYTES:
        raise integrity("扫描工件超出读取上限。")
    try:
        result = json.loads(Path(path).read_bytes())
        if type(result) is not dict:
            raise ValueError("object required")
        return result
    except (UnicodeError, ValueError) as exc:
        raise integrity() from exc


def file_hash(path):
    plain(path)
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish_json(path, value):
    plain(Path(path).parent, directory=True)
    _publish_exact_json(Path(path), value, conflict_code="reference_integrity_error", conflict_message="不可变扫描工件已存在且内容不同。")


def frozen_payload(frozen):
    payload = frozen.audit_payload()
    payload["schema_id"] = "reference_version.import"
    return payload


def normalize_request(frozen):
    if frozen.errors:
        raise ModelMatchingError("reference_request_invalid", "扫描导入请求含无效字段。")
    try:
        request = {"model_id": validate_identifier(frozen.model_id, "model_id"),
                   "version_id": validate_identifier(frozen.version_id, "version_id"),
                   "source_path": frozen.source_path, "declared_unit": frozen.declared_unit,
                   "license_name": frozen.license_name, "provenance": frozen.provenance(),
                   "supersedes_version_id": frozen.supersedes_version_id}
        if request["supersedes_version_id"] is not None:
            validate_identifier(request["supersedes_version_id"], "supersedes_version_id")
            if request["supersedes_version_id"] == request["version_id"]:
                raise ValueError("不能替代自身")
        if request["declared_unit"] not in {"mm", "cm", "m"} or not request["license_name"].strip():
            raise ValueError("必须声明单位和许可")
        if len(_canonical_json_bytes(request)) > 65536 or "\0" in request["source_path"]:
            raise ValueError("元数据超限")
        if Path(request["source_path"]).suffix.lower() not in {".las", ".laz", ".ply"}:
            raise ModelMatchingError("reference_format_unsupported", "扫描入口仅接受 LAS、受支持的 LAZ 和点式 PLY。")
        return request
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("reference_request_invalid", "扫描导入身份、单位、许可或元数据无效。") from exc


def validate_owner(root, directory, owner):
    fields = {"schema_version", "operation_id", "request_id", "request_fingerprint", "idempotency_key_hash",
              "actor_id", "created_at", "request", "resource_limits"}
    try:
        if set(owner) != fields or owner["schema_version"] != "1.0":
            raise integrity()
        snapshot = read_verified_operation_snapshot(root, owner["operation_id"])
        op, events = snapshot["operation"], snapshot["events"]
        frozen = _freeze_import_request(**owner["request"])
        request = normalize_request(frozen)
        if (request != owner["request"] or audit_digest(frozen_payload(frozen)) != op["request_fingerprint"]
                or op["operation_type"] != "reference_version.import"
                or any(owner[key] != op[key] for key in ("operation_id", "request_id", "request_fingerprint", "idempotency_key_hash", "actor_id"))
                or owner["created_at"] != events[0]["timestamp"]
                or directory != model_version_dir(root, request["model_id"], request["version_id"])
                or owner["resource_limits"] != RESOURCE_LIMITS):
            raise integrity()
        return snapshot
    except (KeyError, TypeError, ValueError) as exc:
        raise integrity() from exc


def validate_frozen_source(directory, owner, marker):
    try:
        if set(marker) != {"schema_version", "owner_fingerprint", "artifact", "source_fingerprint", "source_bytes"} or marker["schema_version"] != "1.0" or marker["owner_fingerprint"] != digest(owner):
            raise integrity()
        relative = marker["artifact"]
        if type(relative) is not str or not relative.startswith("source/") or len(relative.split("/")) != 2:
            raise integrity()
        name = relative.split("/")[1]
        if name in {"", ".", ".."} or any(c in name for c in ("\\", ":", "\0")):
            raise integrity()
        path = directory / relative
        plain(path.parent, directory=True)
        if plain(path).st_size != marker["source_bytes"] or not 0 < marker["source_bytes"] <= RESOURCE_LIMITS["source_bytes"] or file_hash(path) != marker["source_fingerprint"]:
            raise integrity("已冻结扫描源的字节或指纹改变。")
        if path.suffix.lower() != Path(owner["request"]["source_path"]).suffix.lower():
            raise integrity()
        return path
    except (KeyError, TypeError, ValueError) as exc:
        raise integrity() from exc


def build_manifest(owner, marker, normalized, quality, reader):
    request = owner["request"]
    metadata = {key: value for key, value in normalized.items() if key != "points"}
    return {"schema_version": "2.0", "source_kind": "scanned_reference", "model_id": request["model_id"],
            "version_id": request["version_id"], "supersedes_version_id": request["supersedes_version_id"],
            "operation_id": owner["operation_id"], "request_fingerprint": owner["request_fingerprint"],
            "imported_by": owner["actor_id"], "imported_at": owner["created_at"], "status": "imported", "index_status": "not_indexed",
            "source_format": Path(marker["artifact"]).suffix[1:], "source_path": marker["artifact"],
            "source_fingerprint": marker["source_fingerprint"], "source_bytes": marker["source_bytes"],
            "license": request["license_name"].strip(), "provenance": request["provenance"], "reader": reader,
            **metadata, "quality_status": quality["status"], "quality_policy_version": quality["quality_policy_version"],
            "quality_policy_fingerprint": audit_digest({"quality_policy_version": quality["quality_policy_version"]}),
            "resource_limits": owner["resource_limits"],
            "artifacts": {"source": marker["artifact"], "normalized_points": "reference_points.json", "quality_report": "quality_report.json"},
            "artifact_fingerprints": {"source": marker["source_fingerprint"], "normalized_points": digest(normalized), "quality_report": digest(quality)}}


def build_commit(owner, marker, manifest):
    return {"schema_version": "1.0", "operation_id": owner["operation_id"], "owner_fingerprint": digest(owner),
            "source_frozen_fingerprint": digest(marker), "manifest_fingerprint": digest(manifest)}


def business_events(owner, marker, manifest):
    return [("reference.import_owned", {"owner_fingerprint": digest(owner)}),
            ("reference.source_frozen", {"source_frozen_fingerprint": digest(marker), "source_fingerprint": marker["source_fingerprint"]}),
            ("reference.quality_checked", {"quality_fingerprint": manifest["artifact_fingerprints"]["quality_report"], "status": manifest["quality_status"]}),
            ("reference.version_imported", {"manifest_fingerprint": digest(manifest), "model_id": manifest["model_id"], "version_id": manifest["version_id"]})]


def load_bundle(root, model_id, version_id, *, require_completed=True):
    root = Path(root)
    directory = model_version_dir(root, model_id, version_id)
    try:
        for path in (root / "models", directory.parent.parent, directory.parent, directory):
            plain(path, directory=True)
        if load_model_asset(root, model_id).get("source_family") != "scanned_reference":
            raise integrity()
        owner, marker = read_json(directory / "owner.json"), read_json(directory / "source_frozen.json")
        snapshot = validate_owner(root, directory, owner)
        validate_frozen_source(directory, owner, marker)
        normal, quality = read_json(directory / "reference_points.json"), read_json(directory / "quality_report.json")
        manifest = read_json(directory / "model_manifest.json")
        if manifest != build_manifest(owner, marker, normal, quality, manifest["reader"]):
            raise integrity()
        for key in ("normalized_points", "quality_report"):
            if file_hash(directory / manifest["artifacts"][key]) != manifest["artifact_fingerprints"][key]:
                raise integrity()
        if normal["declared_unit"] != owner["request"]["declared_unit"] or normal["coordinate_unit"] != "m" or normal["point_count"] != len(normal["points"]):
            raise integrity()
        commit = build_commit(owner, marker, manifest)
        if require_completed:
            if read_json(directory / "commit.json") != commit:
                raise integrity()
            for event_type, details in business_events(owner, marker, manifest):
                actual = [e for e in snapshot["events"] if e["event_type"] == event_type]
                if len(actual) != 1 or actual[0]["details"] != details:
                    raise integrity()
            op = snapshot["operation"]
            if op["status"] != "completed" or op["result"] != {"commit_fingerprint": digest(commit)}:
                raise ModelMatchingError("publication_recovery_required", "扫描版本尚未完成审计提交。")
        return {"owner": owner, "source_frozen": marker, "manifest": manifest, "commit": commit, "normalized": normal, "quality": quality}
    except FileNotFoundError as exc:
        raise ModelMatchingError("reference_version_not_ready", "扫描版本不存在或尚未完整提交。") from exc
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise integrity() from exc


def load_reference_version(root, model_id, version_id):
    return load_bundle(root, model_id, version_id)["manifest"]


def list_reference_versions(root, model_id):
    directory = model_version_dir(root, model_id, "placeholder").parent
    if not directory.exists():
        return []
    plain(directory, directory=True)
    result = []
    for child in sorted(directory.iterdir()):
        plain(child, directory=True)
        if (child / "commit.json").exists():
            result.append(load_reference_version(root, model_id, child.name))
    return result
