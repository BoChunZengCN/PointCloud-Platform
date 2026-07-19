import json
from collections.abc import Callable
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.las_sampling import sample_points_from_source
from pc_system.segmentation_benchmarks import load_benchmark_sample
from pc_system.segmentation_correspondence import (
    CorrespondenceError,
    match_point_labels,
)
from pc_system.segmentation_metrics import (
    associate_instances,
    build_bbox_metrics,
    build_instance_metrics,
    build_point_metrics,
)
from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_run import fingerprint_config, utc_now


def _error_details(exc: Exception) -> dict[str, str]:
    if isinstance(exc, CorrespondenceError):
        return {"code": exc.code, "message": str(exc)}
    if isinstance(exc, FileNotFoundError):
        return {"code": "artifact_not_found", "message": str(exc)}
    if isinstance(exc, (ValueError, KeyError, json.JSONDecodeError)):
        return {"code": "invalid_evaluation_input", "message": str(exc)}
    return {"code": "evaluation_failed", "message": str(exc)}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _source_path(project_root: Path, source_uri: str) -> Path:
    path = Path(source_uri)
    if path.is_absolute() or path.exists():
        return path
    project_path = project_root / path
    return project_path if project_path.exists() else path


def _predicted_assignments(
    run_dir: Path,
    run: dict,
    source_point_count: int,
) -> tuple[dict[int, dict], list[dict]]:
    object_report = _read_json(run_dir / run["artifacts"]["object_segments"])
    predicted: dict[int, dict] = {}
    for item in object_report.get("objects", []):
        artifact = item.get("point_membership_artifact")
        if not artifact:
            raise ValueError(
                f"Object is missing point_membership_artifact: {item.get('object_id')}"
            )
        membership = _read_json(run_dir / artifact)
        indices = membership.get("source_point_indices")
        if not isinstance(indices, list):
            raise ValueError(
                f"Membership artifact is missing source_point_indices: {artifact}"
            )
        for raw_index in indices:
            index = int(raw_index)
            if index < 0 or index >= source_point_count:
                raise ValueError(f"Membership source index is out of range: {index}")
            if index in predicted:
                raise ValueError(
                    f"Source point belongs to multiple predicted objects: {index}"
                )
            predicted[index] = {
                "instance_id": str(item["object_id"]),
                "class_id": str(
                    item.get("class_id", item.get("label", "object_candidate"))
                ),
                "is_noise": False,
            }
    for index in range(source_point_count):
        predicted.setdefault(
            index,
            {"instance_id": "noise", "class_id": "noise", "is_noise": True},
        )
    return predicted, list(object_report.get("objects", []))


def _find_sample(manifest: dict, sample_id: str) -> dict:
    sample = next(
        (
            item
            for item in manifest.get("samples", [])
            if item.get("sample_id") == sample_id
        ),
        None,
    )
    if sample is None:
        raise KeyError(f"Benchmark sample not found: {sample_id}")
    return sample


def evaluate_segmentation_run(
    project_root: Path,
    *,
    asset_id: str,
    run_id: str,
    benchmark_id: str,
    sample_id: str,
    evaluation_id: str,
    config: dict,
    box_runner: Callable[[dict, dict], float] | None = None,
) -> dict:
    """对一个 Phase 13A 运行执行版本化黄金标签评估。"""

    asset_id = validate_identifier(asset_id, "asset_id")
    run_id = validate_identifier(run_id, "run_id")
    benchmark_id = validate_identifier(benchmark_id, "benchmark_id")
    sample_id = validate_identifier(sample_id, "sample_id")
    evaluation_id = validate_identifier(evaluation_id, "evaluation_id")
    evaluation_dir = (
        project_root
        / "reports"
        / "segmentation_evaluations"
        / asset_id
        / evaluation_id
    )
    if evaluation_dir.exists():
        raise FileExistsError(evaluation_dir)
    evaluation = {
        "schema_version": "1.0",
        "evaluation_kind": "golden_labels",
        "evaluation_id": evaluation_id,
        "asset_id": asset_id,
        "segmentation_run_id": run_id,
        "benchmark_id": benchmark_id,
        "sample_id": sample_id,
        "config": dict(config),
        "config_fingerprint": fingerprint_config(config),
        "status": "planned",
        "started_at": None,
        "completed_at": None,
        "artifacts": {},
        "summary": None,
        "error": None,
    }
    write_json(evaluation, evaluation_dir / "evaluation_run.json")
    try:
        evaluation["status"] = "running"
        evaluation["started_at"] = utc_now()
        write_json(evaluation, evaluation_dir / "evaluation_run.json")

        run_dir = (
            project_root / "reports" / "segmentation_runs" / asset_id / run_id
        )
        segmentation_run = _read_json(run_dir / "segmentation_run.json")
        if segmentation_run.get("status") != "completed":
            raise ValueError("Only completed segmentation runs can be evaluated.")
        if segmentation_run.get("asset_id") != asset_id:
            raise ValueError("Segmentation run asset_id does not match evaluation.")

        manifest, labels = load_benchmark_sample(
            project_root, benchmark_id, sample_id
        )
        sample = _find_sample(manifest, sample_id)
        if sample.get("asset_id") != asset_id:
            raise ValueError("Benchmark sample asset_id does not match evaluation.")
        source_path = _source_path(project_root, segmentation_run["source_uri"])
        max_points = int(
            segmentation_run.get("config", {}).get(
                "max_points", segmentation_run["source_point_count"]
            )
        )
        source_points = sample_points_from_source(
            source_path, max_points=max_points
        )
        actual_fingerprint = fingerprint_points(source_points)
        if segmentation_run.get("source_fingerprint") != actual_fingerprint:
            raise CorrespondenceError(
                "source_fingerprint_mismatch",
                "Segmentation run source fingerprint does not match its source points.",
            )

        correspondence_mode = str(
            config.get("correspondence_mode", "strict_index")
        )
        matched_labels, correspondence = match_point_labels(
            labels.get("point_labels", []),
            source_points,
            expected_fingerprint=str(sample["source_fingerprint"]),
            mode=correspondence_mode,
            tolerance=config.get("coordinate_tolerance"),
            min_coverage=float(config.get("min_match_coverage", 1.0)),
        )
        golden = {
            int(item["source_point_index"]): {
                "instance_id": str(item["instance_id"]),
                "class_id": str(item["class_id"]),
                "is_noise": bool(item.get("is_noise", False)),
            }
            for item in matched_labels
        }
        predicted, predicted_objects = _predicted_assignments(
            run_dir, segmentation_run, len(source_points)
        )
        predicted_labeled = {
            index: predicted[index] for index in golden
        }
        golden_instances = {
            index: item["instance_id"]
            for index, item in golden.items()
            if not item["is_noise"]
        }
        predicted_instances = {
            index: item["instance_id"]
            for index, item in predicted_labeled.items()
            if not item["is_noise"]
        }
        instance_threshold = float(config.get("instance_iou_threshold", 0.5))
        association = associate_instances(
            golden_instances, predicted_instances, instance_threshold
        )
        point_metrics = build_point_metrics(
            golden, predicted_labeled, association
        )
        instance_metrics = build_instance_metrics(
            golden_instances,
            predicted_instances,
            iou_threshold=instance_threshold,
        )
        bbox_metrics = build_bbox_metrics(
            labels.get("boxes", []),
            predicted_objects,
            association,
            iou_threshold=float(config.get("box_iou_threshold", 0.5)),
            runner=box_runner,
        )
        summary = {
            "schema_version": "1.0",
            "evaluation_kind": "golden_labels",
            "evaluation_id": evaluation_id,
            "asset_id": asset_id,
            "segmentation_run_id": run_id,
            "benchmark_id": benchmark_id,
            "benchmark_version": manifest.get("benchmark_version"),
            "benchmark_split": manifest.get("split"),
            "sample_id": sample_id,
            "scene_type": manifest.get("scene_type"),
            "matched_label_ratio": correspondence["matched_ratio"],
            "point_miou": point_metrics["point_miou"],
            "class_miou": point_metrics["class_miou"],
            "labeled_point_accuracy": point_metrics[
                "labeled_point_accuracy"
            ],
            "noise_f1": point_metrics["noise_f1"],
            "instance_precision": instance_metrics["instance_precision"],
            "instance_recall": instance_metrics["instance_recall"],
            "instance_f1": instance_metrics["instance_f1"],
            "over_segmentation_count": instance_metrics[
                "over_segmentation_count"
            ],
            "under_segmentation_count": instance_metrics[
                "under_segmentation_count"
            ],
            "mean_box_iou": bbox_metrics["mean_box_iou"],
            "box_f1": bbox_metrics["box_f1"],
            "box_metric_engine": bbox_metrics["executed_engine"],
        }
        artifacts = {
            "correspondence": "correspondence.json",
            "point_metrics": "point_metrics.json",
            "instance_metrics": "instance_metrics.json",
            "bbox_metrics": "bbox_metrics.json",
            "summary": "evaluation_summary.json",
        }
        write_json(correspondence, evaluation_dir / artifacts["correspondence"])
        write_json(point_metrics, evaluation_dir / artifacts["point_metrics"])
        write_json(
            instance_metrics, evaluation_dir / artifacts["instance_metrics"]
        )
        write_json(bbox_metrics, evaluation_dir / artifacts["bbox_metrics"])
        write_json(summary, evaluation_dir / artifacts["summary"])
        evaluation["artifacts"] = artifacts
        evaluation["summary"] = summary
        evaluation["status"] = "completed"
        evaluation["completed_at"] = utc_now()
        write_json(evaluation, evaluation_dir / "evaluation_run.json")
        return evaluation
    except Exception as exc:
        evaluation["status"] = "failed"
        evaluation["completed_at"] = utc_now()
        evaluation["error"] = _error_details(exc)
        write_json(evaluation, evaluation_dir / "evaluation_run.json")
        raise
