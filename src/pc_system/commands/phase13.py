import json
import sys
from pathlib import Path

from pc_system.config import ProjectConfig
from pc_system.las_sampling import sample_points_from_source
from pc_system.segmentation_service import run_segmentation


def run_phase13_segmentation(
    project_root: Path,
    asset_id: str,
    run_id: str,
    engine: str,
    allow_fallback: bool,
    distance_threshold: float,
    min_points: int,
    voxel_size: float | None,
    max_points: int,
) -> int:
    """从 workspace 资产执行一次版本化 Phase 13A 分割运行。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    metadata_path = paths["assets"] / asset_id / "asset.json"
    if not metadata_path.exists():
        print(f"Asset metadata not found: {metadata_path}", file=sys.stderr)
        return 2
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        source_path = Path(metadata.get("file", {}).get("path", ""))
        if not source_path.exists():
            print(f"Asset source not found: {source_path}", file=sys.stderr)
            return 2
        points = sample_points_from_source(source_path, max_points=max_points)
        config = {
            "engine": engine,
            "allow_fallback": allow_fallback,
            "distance_threshold": distance_threshold,
            "min_points": min_points,
            "max_points": max_points,
        }
        if voxel_size is not None:
            config["voxel_size"] = voxel_size
        run_segmentation(
            project_root,
            asset_id=asset_id,
            asset_version=str(metadata.get("asset_version", metadata.get("schema_version", "1.0"))),
            source_uri=str(source_path),
            points=points,
            config=config,
            run_id=run_id,
        )
    except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(project_root / "reports" / "segmentation_runs" / asset_id / run_id / "segmentation_run.json")
    return 0
