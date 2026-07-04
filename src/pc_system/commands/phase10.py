import json
import sys
from pathlib import Path

from pc_system.config import ProjectConfig
from pc_system.object_segmentation import segment_object_candidates, write_object_segmentation_report
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
