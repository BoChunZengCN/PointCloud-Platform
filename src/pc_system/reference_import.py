"""扫描源冻结、配额预留、独立解码与不可变版本提交。"""

import hashlib
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from .model_import import _freeze_import_request
from .model_library import _fsync_directory, load_model_asset, model_version_dir
from .model_matching_audit import start_operation, ensure_operation_event, complete_operation, fail_operation, read_verified_operation_snapshot
from .model_matching_errors import ModelMatchingError
from .model_matching_identity import require_any_role
from .model_resource_lock import model_resource_lock
from .reference_reader import decode_reference_file
from .reference_store import (MAX_IMPORT_BYTES, MAX_RETAINED_BYTES, RESOURCE_LIMITS, build_manifest, build_commit,
                              business_events, digest, frozen_payload, normalize_request, plain, publish_json,
                              read_json, load_bundle, load_reference_version, validate_owner, validate_frozen_source)


def _tree_bytes(path):
    if not path.exists():
        return 0
    plain(path, directory=True)
    total = 0
    for directory, folders, files in os.walk(path, followlinks=False):
        for name in folders:
            plain(Path(directory) / name, directory=True)
        for name in files:
            total += plain(Path(directory) / name).st_size
    return total


def _retained_usage(root):
    total = _tree_bytes(root / "imports/models")
    models = root / "models"
    if not models.exists():
        return total
    plain(models, directory=True)
    for asset_dir in models.iterdir():
        if asset_dir.name.startswith(".") or not asset_dir.is_dir():
            continue
        plain(asset_dir, directory=True)
        if not (asset_dir / "model_asset.json").is_file():
            continue
        asset = load_model_asset(root, asset_dir.name)
        if asset.get("source_family") != "scanned_reference":
            continue
        versions = asset_dir / "versions"
        if not versions.exists():
            continue
        plain(versions, directory=True)
        for directory in versions.iterdir():
            # 即使没有冻结源，已有所有者与中断副本仍占用预留。
            # 必须先观察终态：扫描过程中完成提交时，本轮仍保留完整预留。
            pending = (directory / "owner.json").exists() and not any((directory / name).exists() for name in ("commit.json", "rejection.json"))
            actual = _tree_bytes(directory)
            total += max(actual, MAX_IMPORT_BYTES) if pending else actual
    return total


def _capture_source(source, directory, *, available_bytes):
    source = Path(source).absolute()
    for parent in reversed(source.parents):
        plain(parent, directory=True)
    info = plain(source)
    if not 0 < info.st_size <= min(RESOURCE_LIMITS["source_bytes"], available_bytes):
        raise ModelMatchingError("reference_input_limit", "扫描源超过 limit 限制。")
    destination = directory / f"capture-{uuid.uuid4().hex}{source.suffix.lower()}"
    checksum, count = hashlib.sha256(), 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        opened = os.fstat(reader.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ModelMatchingError("reference_source_changed", "复制前扫描源身份改变。")
        while chunk := reader.read(1024 * 1024):
            count += len(chunk)
            if count > min(RESOURCE_LIMITS["source_bytes"], available_bytes):
                raise ModelMatchingError("reference_input_limit", "扫描源超过 limit 限制。")
            writer.write(chunk)
            checksum.update(chunk)
        after = os.fstat(reader.fileno())
        if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns) or count != info.st_size:
            raise ModelMatchingError("reference_source_changed", "扫描源在复制期间改变。")
        writer.flush()
        os.fsync(writer.fileno())
    _fsync_directory(directory)
    return {"artifact": "source/" + destination.name, "source_fingerprint": checksum.hexdigest(), "source_bytes": count}


@contextmanager
def _decode_slot(root):
    selected = None
    for index in range(2):
        candidate = model_resource_lock(root, "reference-decode", str(index), timeout_seconds=0)
        try:
            candidate.__enter__()
        except ModelMatchingError as exc:
            if exc.code != "operation_busy":
                raise
        else:
            selected = candidate
            break
    if selected is None:
        raise ModelMatchingError("operation_busy", "两个扫描解析槽位均在使用，请用原操作重试。")
    try:
        yield
    finally:
        selected.__exit__(None, None, None)


def _publish_with_budget(directory, name, value):
    from .model_sampling import _canonical_json_bytes
    if not (directory / name).exists() and _tree_bytes(directory) + len(_canonical_json_bytes(value)) > MAX_IMPORT_BYTES:
        raise ModelMatchingError("reference_storage_limit", "单次扫描导入工件超过配额。")
    publish_json(directory / name, value)


def _resume(root, directory, owner):
    opid = owner["operation_id"]
    request = owner["request"]
    snapshot = validate_owner(root, directory, owner)
    if snapshot["operation"]["status"] == "completed":
        return load_reference_version(root, request["model_id"], request["version_id"])
    if (directory / "rejection.json").exists():
        rejection = read_json(directory / "rejection.json")
        if rejection.get("owner_fingerprint") != digest(owner):
            raise ModelMatchingError("reference_integrity_error", "扫描拒绝记录不属于当前所有者。")
        fail_operation(root, opid, rejection["code"], rejection["message"])
        raise ModelMatchingError(rejection["code"], rejection["message"])
    ensure_operation_event(root, opid, "reference.import_owned", {"owner_fingerprint": digest(owner)})
    if not (directory / "source_frozen.json").exists():
        source_dir = directory / "source"
        source_dir.mkdir(exist_ok=True)
        plain(source_dir, directory=True)
        captured = _capture_source(request["source_path"], source_dir, available_bytes=MAX_IMPORT_BYTES - _tree_bytes(directory) - 65536)
        marker = {"schema_version": "1.0", "owner_fingerprint": digest(owner), **captured}
        _publish_with_budget(directory, "source_frozen.json", marker)
    marker = read_json(directory / "source_frozen.json")
    source = validate_frozen_source(directory, owner, marker)
    ensure_operation_event(root, opid, "reference.source_frozen", {"source_frozen_fingerprint": digest(marker), "source_fingerprint": marker["source_fingerprint"]})
    if not (directory / "model_manifest.json").exists():
        with _decode_slot(root):
            decoded = decode_reference_file(source, declared_unit=request["declared_unit"], maximum_points=RESOURCE_LIMITS["point_count"], timeout_seconds=RESOURCE_LIMITS["timeout_seconds"])
        if decoded["source_fingerprint"] != marker["source_fingerprint"] or decoded["source_bytes"] != marker["source_bytes"]:
            raise ModelMatchingError("reference_integrity_error", "解码内容与冻结源不一致。")
        _publish_with_budget(directory, "reference_points.json", decoded["normalized"])
        _publish_with_budget(directory, "quality_report.json", decoded["quality"])
        manifest = build_manifest(owner, marker, decoded["normalized"], decoded["quality"], decoded["reader"])
        _publish_with_budget(directory, "model_manifest.json", manifest)
    prepared = load_bundle(root, request["model_id"], request["version_id"], require_completed=False)
    manifest, commit = prepared["manifest"], prepared["commit"]
    for event_type, details in business_events(owner, marker, manifest):
        ensure_operation_event(root, opid, event_type, details)
    _publish_with_budget(directory, "commit.json", commit)
    complete_operation(root, opid, {"commit_fingerprint": digest(commit)})
    return load_reference_version(root, request["model_id"], request["version_id"])


_DETERMINISTIC_REJECTIONS = {"reference_format_invalid", "reference_format_unsupported", "reference_laz_variant_unsupported",
                           "reference_input_limit", "reference_points_invalid", "reference_unit_conflict", "reference_crs_unsupported",
                           "reference_source_changed", "reference_storage_limit"}


def import_reference_version(root, *, model_id, version_id, source_path, declared_unit, license_name, provenance,
                             principal, operation_id, request_id, idempotency_key, supersedes_version_id=None):
    root = Path(root)
    frozen = _freeze_import_request(model_id=model_id, version_id=version_id, source_path=source_path, declared_unit=declared_unit,
                                   license_name=license_name, provenance=provenance, supersedes_version_id=supersedes_version_id)
    operation, replayed = start_operation(root, operation_id=operation_id, operation_type="reference_version.import", principal=principal,
                                   request_id=request_id, idempotency_key=idempotency_key, request_payload=frozen_payload(frozen))
    opid = operation["operation_id"]
    if (opid, operation["request_id"], operation["actor_id"]) != (operation_id, request_id, principal.actor_id):
        raise ModelMatchingError("reference_operation_identity_conflict", "扫描恢复必须沿用原操作、请求和操作人身份。")
    if operation["status"] == "failed":
        raise ModelMatchingError(operation["error"]["code"], operation["error"]["message"])
    try:
        require_any_role(principal, {"expert"})
        request = normalize_request(frozen)
    except ModelMatchingError as exc:
        # 未能定位版本所有者时，不对已有 running 操作写失败终态。
        if operation["status"] == "running" and not replayed:
            fail_operation(root, opid, exc.code, str(exc))
        raise
    directory = model_version_dir(root, request["model_id"], request["version_id"])
    with model_resource_lock(root, "reference-version", request["model_id"], request["version_id"]):
        owner = None
        try:
            for path in (root / "models", directory.parent.parent):
                plain(path, directory=True)
            if directory.parent.exists():
                plain(directory.parent, directory=True)
            if load_model_asset(root, request["model_id"]).get("source_family", "cad_mesh") != "scanned_reference":
                raise ModelMatchingError("model_source_family_conflict", "CAD 资产不能导入扫描参考版本。")
            if directory.exists():
                plain(directory, directory=True)
                if (directory / "owner.json").exists():
                    found = read_json(directory / "owner.json")
                    validate_owner(root, directory, found)
                    if found["operation_id"] != opid:
                        raise ModelMatchingError("reference_version_owned", "此扫描版本已由其他操作持有。")
                    owner = found
                elif any(directory.iterdir()):
                    raise ModelMatchingError("reference_integrity_error", "扫描目录含无所有者工件。")
            if owner is None:
                if operation["status"] != "running":
                    raise ModelMatchingError("reference_integrity_error", "已完成扫描操作缺少原始所有者。")
                if request["supersedes_version_id"] is not None:
                    load_reference_version(root, request["model_id"], request["supersedes_version_id"])
                with model_resource_lock(root, "reference-quota"):
                    if _retained_usage(root) + MAX_IMPORT_BYTES > MAX_RETAINED_BYTES:
                        raise ModelMatchingError("reference_storage_limit", "扫描暂存及留存工件已达到配额。")
                    directory.parent.mkdir(exist_ok=True)
                    plain(directory.parent, directory=True)
                    directory.mkdir(exist_ok=True)
                    plain(directory, directory=True)
                    snapshot = read_verified_operation_snapshot(root, opid)
                    proposed = {"schema_version": "1.0", **{key: operation[key] for key in ("operation_id", "request_id", "request_fingerprint", "idempotency_key_hash", "actor_id")},
                                "created_at": snapshot["events"][0]["timestamp"], "request": request, "resource_limits": dict(RESOURCE_LIMITS)}
                    publish_json(directory / "owner.json", proposed)
                    owner = proposed
            return _resume(root, directory, owner)
        except Exception as exc:
            # 发布函数可能在文件已经可见后抛错；先重新检查所有者，禁止误记失败。
            if owner is None and (directory / "owner.json").exists():
                try:
                    candidate = read_json(directory / "owner.json")
                    validate_owner(root, directory, candidate)
                    if candidate.get("operation_id") == opid:
                        owner = candidate
                except Exception:
                    raise ModelMatchingError("publication_recovery_required", "扫描所有者状态不确定，请使用原操作重试。") from exc
            if owner is not None:
                if isinstance(exc, ModelMatchingError) and exc.code in _DETERMINISTIC_REJECTIONS and not (directory / "model_manifest.json").exists():
                    rejection = {"owner_fingerprint": digest(owner), "code": exc.code, "message": str(exc)}
                    publish_json(directory / "rejection.json", rejection)
                    fail_operation(root, opid, exc.code, str(exc))
                    raise
                if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
                    raise
                raise ModelMatchingError("publication_recovery_required", "扫描导入状态需恢复，请保留原操作身份重试。") from exc
            error = exc if isinstance(exc, ModelMatchingError) else ModelMatchingError("reference_import_failed", "扫描导入未能开始。")
            if operation["status"] == "running":
                fail_operation(root, opid, error.code, str(error))
            raise error from exc
