from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.segmentation_engines import SegmentationEngineUnavailable, execute_engine
from pc_system.segmentation_operational_quality import (
    build_operational_quality,
    write_operational_quality,
)
from pc_system.segmentation_preprocessing import preprocess_points
from pc_system.segmentation_run import (
    build_segmentation_run,
    publish_latest_success,
    utc_now,
    write_segmentation_run,
)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, SegmentationEngineUnavailable):
        return "engine_unavailable"
    if isinstance(exc, ValueError):
        return "invalid_input"
    return "segmentation_failed"


def _write_membership_artifacts(
    report: dict,
    points: list[dict],
    run_dir: Path,
) -> dict[str, str]:
    """写出每个对象的独立点成员工件，并移除内部索引。"""

    artifacts: dict[str, str] = {}
    for item in report.get("objects", []):
        if "_point_indices" not in item:
            raise ValueError("Segmentation engine output is missing object membership indices.")
        indices = item.pop("_point_indices")
        object_id = validate_identifier(item["object_id"], "object_id")
        relative_path = Path("artifacts") / f"{object_id}.points.json"
        write_json(
            {
                "schema_version": "1.0",
                "object_id": object_id,
                "point_count": len(indices),
                "points": [points[index] for index in indices],
            },
            run_dir / relative_path,
        )
        item["point_membership_artifact"] = relative_path.as_posix()
        artifacts[object_id] = relative_path.as_posix()
    return artifacts


def run_segmentation(
    project_root: Path,
    *,
    asset_id: str,
    asset_version: str,
    source_uri: str,
    points: list[dict],
    config: dict,
    run_id: str,
    runners: dict | None = None,
) -> dict:
    """执行一次可追溯分割运行并保留历史工件。"""

    asset_id = validate_identifier(asset_id, "asset_id")
    run_id = validate_identifier(run_id, "run_id")
    run_dir = project_root / "reports" / "segmentation_runs" / asset_id / run_id
    compatibility_dir = project_root / "reports" / "object_segments" / asset_id
    requested_engine = str(config.get("engine", "builtin_geometric"))
    run = build_segmentation_run(
        run_id=run_id,
        asset_id=asset_id,
        asset_version=asset_version,
        source_uri=source_uri,
        source_point_count=len(points),
        config=dict(config),
        requested_engine=requested_engine,
    )
    write_segmentation_run(run, run_dir)

    try:
        run["status"] = "running"
        run["started_at"] = utc_now()
        write_segmentation_run(run, run_dir)
        processed_points, preprocessing = preprocess_points(points, config)
        report, execution = execute_engine(
            asset_id,
            processed_points,
            config,
            runners=runners,
            include_membership=True,
        )
        memberships = _write_membership_artifacts(report, processed_points, run_dir)
        write_json(report, run_dir / "object_segments.json")
        quality = build_operational_quality(
            report=report,
            preprocessing=preprocessing,
            execution=execution,
            thresholds=dict(config.get("quality_thresholds", {})),
        )
        write_operational_quality(quality, run_dir)

        run["preprocessing"] = preprocessing
        run["requested_engine"] = execution["requested_engine"]
        run["executed_engine"] = execution["executed_engine"]
        run["fallback_reason"] = execution["fallback_reason"]
        run["artifacts"] = {
            "object_segments": "object_segments.json",
            "memberships": memberships,
            "segmentation_quality": "segmentation_quality.json",
            "segmentation_quality_markdown": "segmentation_quality.md",
        }
        run["quality"] = quality
        run["status"] = "completed"
        run["completed_at"] = utc_now()
        write_segmentation_run(run, run_dir)
        publish_latest_success(run, run_dir, compatibility_dir)
        return run
    except Exception as exc:
        run["status"] = "failed"
        run["completed_at"] = utc_now()
        run["error"] = {"code": _error_code(exc), "message": str(exc)}
        write_segmentation_run(run, run_dir)
        raise
