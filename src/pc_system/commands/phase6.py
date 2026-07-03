import json
import sys
from pathlib import Path

from pc_system.config import ProjectConfig
from pc_system.las_sampling import sample_points_from_source
from pc_system.point_cloud_analysis import analyze_point_records, load_points_json, write_point_cloud_analysis


def run_analyze_point_cloud(project_root: Path, asset_id: str, points_json: Path, grid_cell_size: float) -> int:
    """从轻量点记录 JSON 生成 Phase 6 点云分析报告。"""

    if not points_json.exists():
        print(f"Point sample JSON not found: {points_json}", file=sys.stderr)
        return 2
    paths = ProjectConfig(project_root=project_root).ensure_directories()
    try:
        points = load_points_json(points_json)
    except json.JSONDecodeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    analysis = analyze_point_records(asset_id, points, grid_cell_size=grid_cell_size)
    write_point_cloud_analysis(analysis, paths["reports"] / "analysis" / asset_id)
    return 0


def run_analyze_asset(project_root: Path, asset_id: str, max_points: int, grid_cell_size: float) -> int:
    """从 workspace 资产元数据读取源点云并生成 Phase 7 分析报告。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    metadata_path = paths["assets"] / asset_id / "asset.json"
    if not metadata_path.exists():
        print(f"Asset metadata not found: {metadata_path}", file=sys.stderr)
        return 2
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_path = Path(metadata.get("file", {}).get("path", ""))
    if not source_path.exists():
        print(f"Asset source not found: {source_path}", file=sys.stderr)
        return 2
    try:
        points = sample_points_from_source(source_path, max_points=max_points)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    analysis = analyze_point_records(asset_id, points, grid_cell_size=grid_cell_size)
    write_point_cloud_analysis(analysis, paths["reports"] / "analysis" / asset_id)
    return 0
