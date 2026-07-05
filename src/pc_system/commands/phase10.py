import json
import sys
from pathlib import Path

from pc_system.config import ProjectConfig
from pc_system.las_sampling import sample_points_from_source
from pc_system.object_segmentation import build_object_segmentation_quality, segment_object_candidates, segment_with_open3d_adapter, write_object_segmentation_report
from pc_system.point_cloud_analysis import load_points_json


def run_segment_objects(
    project_root: Path,
    asset_id: str,
    points_json: Path,
    distance_threshold: float,
    min_points: int,
) -> int:
    """从轻量点记录 JSON 生成 Phase 10 物体候选分割报告。"""

    if not points_json.exists():
        print(f"Point sample JSON not found: {points_json}", file=sys.stderr)
        return 2
    paths = ProjectConfig(project_root=project_root).ensure_directories()
    try:
        points = load_points_json(points_json)
        report = segment_object_candidates(
            asset_id,
            points,
            distance_threshold=distance_threshold,
            min_points=min_points,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    write_object_segmentation_report(report, paths["reports"] / "object_segments" / asset_id)
    return 0


def _load_segmentation_config(config_path: Path | None) -> dict:
    """读取 Phase 10 Extension 配置；未传入时返回空配置。"""

    if not config_path:
        return {}
    if not config_path.exists():
        raise FileNotFoundError(config_path)
    return json.loads(config_path.read_text(encoding="utf-8"))


def _segment_report(asset_id: str, points: list[dict], engine: str, distance_threshold: float, min_points: int) -> dict:
    """根据 engine 选择内置几何聚类或 Open3D 适配边界。"""

    if engine == "open3d":
        return segment_with_open3d_adapter(asset_id, points, distance_threshold=distance_threshold, min_points=min_points)
    return segment_object_candidates(asset_id, points, distance_threshold=distance_threshold, min_points=min_points)


def run_segment_asset_objects(
    project_root: Path,
    asset_id: str,
    distance_threshold: float,
    min_points: int,
    max_points: int,
    engine: str,
    config_path: Path | None = None,
) -> int:
    """从 workspace 资产源点云直接生成 Phase 10 物体候选分割报告。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    metadata_path = paths["assets"] / asset_id / "asset.json"
    if not metadata_path.exists():
        print(f"Asset metadata not found: {metadata_path}", file=sys.stderr)
        return 2
    try:
        config = _load_segmentation_config(config_path)
        resolved_distance = float(config.get("distance_threshold", distance_threshold))
        resolved_min_points = int(config.get("min_points", min_points))
        resolved_max_points = int(config.get("max_points", max_points))
        resolved_engine = str(config.get("engine", engine))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        source_path = Path(metadata.get("file", {}).get("path", ""))
        if not source_path.exists():
            print(f"Asset source not found: {source_path}", file=sys.stderr)
            return 2
        points = sample_points_from_source(source_path, max_points=resolved_max_points)
        report = _segment_report(asset_id, points, resolved_engine, resolved_distance, resolved_min_points)
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report["source_mode"] = "asset_source"
    report["source_path"] = str(source_path)
    report["max_points"] = resolved_max_points
    report["engine"] = resolved_engine
    report["segmentation_quality"] = build_object_segmentation_quality(report)
    write_object_segmentation_report(report, paths["reports"] / "object_segments" / asset_id)
    return 0

