import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pc_system.json_io import write_json


def fingerprint_config(config: dict) -> str:
    """生成与字典键顺序无关的稳定配置指纹。"""

    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_segmentation_run(
    *,
    run_id: str,
    asset_id: str,
    asset_version: str,
    source_uri: str,
    source_point_count: int,
    config: dict,
    requested_engine: str,
) -> dict:
    """创建尚未执行的版本化分割运行记录。"""

    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "asset_id": asset_id,
        "asset_version": asset_version,
        "source_uri": source_uri,
        "source_point_count": int(source_point_count),
        "config": config,
        "config_fingerprint": fingerprint_config(config),
        "requested_engine": requested_engine,
        "executed_engine": None,
        "fallback_reason": None,
        "status": "planned",
        "started_at": None,
        "completed_at": None,
        "preprocessing": None,
        "artifacts": {},
        "quality": None,
        "error": None,
    }


def utc_now() -> str:
    """返回可写入 JSON 的 UTC 时间。"""

    return datetime.now(timezone.utc).isoformat()


def write_segmentation_run(run: dict, run_dir: Path) -> Path:
    """原子写出分割运行清单。"""

    return write_json(run, run_dir / "segmentation_run.json")


def publish_latest_success(run: dict, run_dir: Path, compatibility_dir: Path) -> Path:
    """仅把成功运行发布为 Phase 10 兼容的最新物体报告。"""

    if run.get("status") != "completed":
        raise ValueError("Only completed segmentation runs can be published.")
    artifact = run.get("artifacts", {}).get("object_segments")
    if not artifact:
        raise ValueError("Completed segmentation run is missing object_segments artifact.")
    source = run_dir / artifact
    if not source.is_file():
        raise FileNotFoundError(source)
    compatibility_dir.mkdir(parents=True, exist_ok=True)
    destination = compatibility_dir / "object_segments.json"
    shutil.copy2(source, destination)
    return destination
