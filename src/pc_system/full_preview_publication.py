"""完整 LAS 预览的本机版本化发布、回滚与审计。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pc_system.full_preview_conversion import convert_and_validate
from pc_system.full_preview_source import inspect_las_source
from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json

_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class PublishedPreview:
    asset_id: str
    run_id: str
    run_dir: Path
    metadata_url: str
    manifest: dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _asset_root(project_root: Path, asset_id: str, *, create: bool) -> Path:
    asset_id = validate_identifier(asset_id, "asset_id")
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("项目根目录无效。")
    path = root
    for part in ("workspace", "previews", asset_id):
        path = path / part
        if _is_reparse(path):
            raise ValueError("预览工作目录不可是符号链接或重解析点。")
        if create:
            path.mkdir(exist_ok=True)
        elif not path.is_dir():
            return path
    return path


def _audit(root: Path, event: str, run_id: str, **details: object) -> None:
    entry = {
        "event": event,
        "run_id": run_id,
        "at": _now(),
        "actor": "local_cli",
        **details,
    }
    with (root / "audit.jsonl").open("ab") as handle:
        handle.write((json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(root: Path, run_id: str) -> Path:
    validate_identifier(run_id, "run_id")
    return root / "runs" / run_id / "manifest.json"


def _load_published(root: Path, asset_id: str, run_id: str) -> PublishedPreview | None:
    path = _manifest_path(root, run_id)
    if _is_reparse(root / "runs") or _is_reparse(path.parent):
        return None
    if _is_reparse(path) or not path.is_file() or path.stat().st_size > 1024 * 1024:
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "ready"
        or manifest.get("asset_id") != asset_id
        or manifest.get("run_id") != run_id
        or manifest.get("metadata_url") != f"/previews/{asset_id}/runs/{run_id}/metadata.json"
        or not isinstance(manifest.get("created_at"), str)
    ):
        return None
    run_dir = path.parent
    if any(
        _is_reparse(run_dir / name) or not (run_dir / name).is_file()
        for name in ("metadata.json", "hierarchy.bin", "octree.bin")
    ):
        return None
    return PublishedPreview(asset_id, run_id, run_dir, manifest["metadata_url"], manifest)


def _write_active(root: Path, asset_id: str, run_id: str, action: str) -> None:
    write_json(
        {"schema": 1, "asset_id": asset_id, "run_id": run_id, "action": action, "at": _now()},
        root / "active.json",
    )


def prepare_full_preview(
    project_root: Path,
    source_path: Path,
    asset_id: str,
    converter_path: Path,
) -> PublishedPreview:
    """同资产独占准备；全部验证后原子切换活动指针。"""

    root = _asset_root(project_root, asset_id, create=True)
    lock = root / ".prepare-lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(f"资产 {asset_id} 正在准备完整预览；请等待或人工检查残留锁。") from exc
    try:
        source = inspect_las_source(source_path, asset_id)
        if not Path(converter_path).is_file():
            raise ValueError("转换器路径无效。")
        if shutil.disk_usage(root).free < source.size * 3:
            raise RuntimeError("预览工作盘空间不足：至少需要来源大小的三倍空闲容量。")
        converter_sha256 = _fingerprint(Path(converter_path))
        run_id = uuid.uuid4().hex
        runs = root / "runs"
        if _is_reparse(runs):
            raise ValueError("运行目录不可是符号链接或重解析点。")
        runs.mkdir(exist_ok=True)
        run_dir = runs / run_id
        _audit(root, "prepare_started", run_id, source_sha256=source.sha256, converter_sha256=converter_sha256)
        try:
            output = convert_and_validate(source, converter_path, run_dir)
            metadata_url = f"/previews/{asset_id}/runs/{run_id}/metadata.json"
            manifest = {
                "schema": 1,
                "status": "ready",
                "asset_id": asset_id,
                "run_id": run_id,
                "created_at": _now(),
                "source_name": source.path.name,
                "source_format": source.source_format,
                "representation_kind": source.representation_kind,
                "viewer_format": "potree2",
                "source_size": source.size,
                "source_sha256": source.sha256,
                "source_point_count": source.point_count,
                "source_unit": source.unit,
                "converter_sha256": converter_sha256,
                "metadata_url": metadata_url,
                "bounds": output.bounds,
                "rgb_available": output.rgb_available,
            }
            write_json(manifest, run_dir / "manifest.json")
            _audit(root, "publish_intent", run_id, source_sha256=source.sha256)
            _write_active(root, asset_id, run_id, "publish")
            return PublishedPreview(asset_id, run_id, run_dir, metadata_url, manifest)
        except Exception as exc:
            if run_dir.exists() and not (run_dir / "manifest.json").exists():
                write_json({"status": "failed", "run_id": run_id, "reason": str(exc)[:500]}, run_dir / "status.json")
            _audit(root, "prepare_failed", run_id, reason=str(exc)[:500])
            raise
    finally:
        lock.rmdir()


def get_active_preview(project_root: Path, asset_id: str) -> PublishedPreview | None:
    root = _asset_root(project_root, asset_id, create=False)
    pointer = root / "active.json"
    if _is_reparse(pointer) or not pointer.is_file() or pointer.stat().st_size > 1024 * 1024:
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("asset_id") != asset_id:
        return None
    run_id = data.get("run_id")
    if not isinstance(run_id, str):
        return None
    try:
        return _load_published(root, asset_id, run_id)
    except ValueError:
        return None


def list_preview_versions(project_root: Path, asset_id: str) -> list[PublishedPreview]:
    root = _asset_root(project_root, asset_id, create=False)
    runs = root / "runs"
    if _is_reparse(runs) or not runs.is_dir():
        return []
    versions = []
    for item in runs.iterdir():
        if not item.is_dir() or _is_reparse(item):
            continue
        try:
            published = _load_published(root, asset_id, item.name)
        except ValueError:
            continue
        if published is not None:
            versions.append(published)
    return sorted(versions, key=lambda item: item.manifest["created_at"])


def activate_preview_version(project_root: Path, asset_id: str, run_id: str) -> PublishedPreview:
    root = _asset_root(project_root, asset_id, create=False)
    validate_identifier(run_id, "run_id")
    if not root.is_dir():
        raise ValueError("预览资产不存在。")
    lock = root / ".prepare-lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(f"资产 {asset_id} 正在准备完整预览；无法回滚。") from exc
    try:
        published = _load_published(root, asset_id, run_id)
        if published is None:
            raise ValueError("只能激活既有已完成预览版本。")
        old = get_active_preview(project_root, asset_id)
        _audit(root, "activation_intent", run_id, previous_run_id=old.run_id if old else None)
        _write_active(root, asset_id, run_id, "rollback")
        return published
    finally:
        lock.rmdir()
