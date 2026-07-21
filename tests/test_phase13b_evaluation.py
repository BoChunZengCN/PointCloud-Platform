import json
from pathlib import Path

import pytest

from pc_system.segmentation_benchmarks import import_benchmark
from pc_system.segmentation_correspondence import CorrespondenceError
from pc_system.segmentation_evaluation import evaluate_segmentation_run
from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_service import run_segmentation


def sample_points():
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.1, "y": 0.1, "z": 0.1},
        {"x": 10.0, "y": 10.0, "z": 10.0},
        {"x": 10.1, "y": 10.1, "z": 10.1},
    ]


def prepare_run_and_benchmark(
    project: Path, *, benchmark_fingerprint: str | None = None
) -> None:
    source = project / "samples" / "scan.points.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(sample_points()), encoding="utf-8")
    run_segmentation(
        project,
        asset_id="scan",
        asset_version="v1",
        source_uri=str(source),
        points=sample_points(),
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 0.3,
            "min_points": 1,
            "max_points": 10000,
            "quality_thresholds": {"max_largest_object_ratio": 1.0},
        },
        run_id="seg-run-001",
    )

    benchmark_source = project / "benchmark-source"
    benchmark_source.mkdir()
    labels = {
        "schema_version": "1.0",
        "point_labels": [
            {
                "point_index": index,
                **point,
                "instance_id": "gold-a" if index < 2 else "gold-b",
                "class_id": "pipe" if index < 2 else "valve",
                "is_noise": False,
            }
            for index, point in enumerate(sample_points())
        ],
        "boxes": [
            {
                "instance_id": "gold-a",
                "class_id": "pipe",
                "center": [0.05, 0.05, 0.05],
                "size": [0.1, 0.1, 0.1],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            },
            {
                "instance_id": "gold-b",
                "class_id": "valve",
                "center": [10.05, 10.05, 10.05],
                "size": [0.1, 0.1, 0.1],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            },
        ],
    }
    labels_path = benchmark_source / "labels.json"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "benchmark_id": "bench-001",
        "benchmark_version": "v1",
        "split": "golden_regression",
        "scene_type": "pipe-rack",
        "point_density": 100.0,
        "coordinate_unit": "m",
        "label_version": "labels-v1",
        "license": "internal",
        "samples": [
            {
                "sample_id": "sample-001",
                "asset_id": "scan",
                "asset_version": "v1",
                "source_uri": str(source),
                "source_fingerprint": benchmark_fingerprint
                or fingerprint_points(sample_points()),
                "labels_path": "labels.json",
                "labels_format": "json",
            }
        ],
    }
    manifest_path = benchmark_source / "benchmark.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    import_benchmark(project, manifest_path)


def test_completed_evaluation_writes_all_metric_artifacts(tmp_path):
    prepare_run_and_benchmark(tmp_path)

    evaluation = evaluate_segmentation_run(
        tmp_path,
        asset_id="scan",
        run_id="seg-run-001",
        benchmark_id="bench-001",
        sample_id="sample-001",
        evaluation_id="eval-001",
        config={"instance_iou_threshold": 0.5, "box_iou_threshold": 0.5},
    )

    evaluation_dir = (
        tmp_path / "reports" / "segmentation_evaluations" / "scan" / "eval-001"
    )
    assert evaluation["status"] == "completed"
    assert evaluation["evaluation_kind"] == "golden_labels"
    assert evaluation["summary"]["point_miou"] == 1.0
    assert evaluation["summary"]["instance_f1"] == 1.0
    assert evaluation["summary"]["mean_box_iou"] == pytest.approx(1.0)
    for filename in (
        "evaluation_run.json",
        "correspondence.json",
        "point_metrics.json",
        "instance_metrics.json",
        "bbox_metrics.json",
        "evaluation_summary.json",
    ):
        assert (evaluation_dir / filename).is_file()


def test_evaluation_uses_public_source_point_indices(tmp_path):
    prepare_run_and_benchmark(tmp_path)
    membership_path = (
        tmp_path
        / "reports"
        / "segmentation_runs"
        / "scan"
        / "seg-run-001"
        / "artifacts"
        / "obj-001.points.json"
    )
    membership = json.loads(membership_path.read_text(encoding="utf-8"))
    assert membership["source_point_indices"] == [0, 1]

    evaluation = evaluate_segmentation_run(
        tmp_path,
        asset_id="scan",
        run_id="seg-run-001",
        benchmark_id="bench-001",
        sample_id="sample-001",
        evaluation_id="eval-001",
        config={},
    )

    assert evaluation["summary"]["matched_label_ratio"] == 1.0


def test_evaluation_failure_is_persisted_without_summary(tmp_path):
    prepare_run_and_benchmark(tmp_path, benchmark_fingerprint="0" * 64)

    with pytest.raises(CorrespondenceError):
        evaluate_segmentation_run(
            tmp_path,
            asset_id="scan",
            run_id="seg-run-001",
            benchmark_id="bench-001",
            sample_id="sample-001",
            evaluation_id="eval-001",
            config={},
        )

    evaluation_dir = (
        tmp_path / "reports" / "segmentation_evaluations" / "scan" / "eval-001"
    )
    failed = json.loads(
        (evaluation_dir / "evaluation_run.json").read_text(encoding="utf-8")
    )
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "source_fingerprint_mismatch"
    assert not (evaluation_dir / "evaluation_summary.json").exists()


def test_failed_coordinate_evaluation_persists_correspondence_diagnostics(tmp_path):
    prepare_run_and_benchmark(tmp_path)
    labels_path = (
        tmp_path
        / "benchmarks"
        / "bench-001"
        / "samples"
        / "sample-001"
        / "labels.json"
    )
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    for item in labels["point_labels"]:
        item["x"] += 1000
    labels_path.write_text(json.dumps(labels), encoding="utf-8")

    with pytest.raises(CorrespondenceError) as exc_info:
        evaluate_segmentation_run(
            tmp_path,
            asset_id="scan",
            run_id="seg-run-001",
            benchmark_id="bench-001",
            sample_id="sample-001",
            evaluation_id="eval-001",
            config={
                "correspondence_mode": "coordinate_tolerance",
                "coordinate_tolerance": 0.01,
                "min_match_coverage": 1.0,
            },
        )

    assert exc_info.value.code == "insufficient_match_coverage"
    evaluation_dir = (
        tmp_path / "reports" / "segmentation_evaluations" / "scan" / "eval-001"
    )
    failed = json.loads(
        (evaluation_dir / "evaluation_run.json").read_text(encoding="utf-8")
    )
    correspondence = json.loads(
        (evaluation_dir / "correspondence.json").read_text(encoding="utf-8")
    )
    assert failed["status"] == "failed"
    assert failed["artifacts"]["correspondence"] == "correspondence.json"
    assert failed["error"]["diagnostic_artifact"] == "correspondence.json"
    assert correspondence["matched_count"] == 0
    assert correspondence["unmatched_count"] == len(sample_points())
    assert not (evaluation_dir / "evaluation_summary.json").exists()


def test_evaluation_refuses_existing_evaluation_id(tmp_path):
    prepare_run_and_benchmark(tmp_path)
    arguments = {
        "asset_id": "scan",
        "run_id": "seg-run-001",
        "benchmark_id": "bench-001",
        "sample_id": "sample-001",
        "evaluation_id": "eval-001",
        "config": {},
    }
    evaluate_segmentation_run(tmp_path, **arguments)

    with pytest.raises(FileExistsError):
        evaluate_segmentation_run(tmp_path, **arguments)


def test_operational_proxy_and_accuracy_remain_separate(tmp_path):
    prepare_run_and_benchmark(tmp_path)

    evaluation = evaluate_segmentation_run(
        tmp_path,
        asset_id="scan",
        run_id="seg-run-001",
        benchmark_id="bench-001",
        sample_id="sample-001",
        evaluation_id="eval-001",
        config={},
    )

    assert evaluation["summary"]["evaluation_kind"] == "golden_labels"
    assert "operational_proxy" not in json.dumps(evaluation["summary"])
    assert "retention_ratio" not in evaluation["summary"]


def test_box_only_benchmark_is_evaluated_without_point_associations(tmp_path):
    prepare_run_and_benchmark(tmp_path)
    labels_path = (
        tmp_path
        / "benchmarks"
        / "bench-001"
        / "samples"
        / "sample-001"
        / "labels.json"
    )
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels["point_labels"] = []
    labels_path.write_text(json.dumps(labels), encoding="utf-8")

    evaluation = evaluate_segmentation_run(
        tmp_path,
        asset_id="scan",
        run_id="seg-run-001",
        benchmark_id="bench-001",
        sample_id="sample-001",
        evaluation_id="eval-box-only",
        config={"box_iou_threshold": 0.5},
    )

    bbox_metrics = json.loads(
        (
            tmp_path
            / "reports"
            / "segmentation_evaluations"
            / "scan"
            / "eval-box-only"
            / "bbox_metrics.json"
        ).read_text(encoding="utf-8")
    )
    assert evaluation["summary"]["matched_label_ratio"] == 1.0
    assert evaluation["summary"]["mean_box_iou"] == pytest.approx(1.0)
    assert bbox_metrics["true_positive_count"] == 2
    assert bbox_metrics["missing_golden_instance_ids"] == []
    assert bbox_metrics["extra_predicted_object_ids"] == []
