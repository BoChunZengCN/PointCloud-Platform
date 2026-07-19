import json

import pytest

from pc_system.segmentation_regression import (
    build_regression_comparison,
    compare_evaluations,
)


def thresholds():
    return {
        "point_miou": {"allowed_drop": 0.02},
        "instance_f1": {"allowed_drop": 0.02},
        "mean_box_iou": {"allowed_drop": 0.03},
        "noise_f1": {"allowed_drop": 0.05},
        "over_segmentation_count": {"allowed_increase": 0},
        "under_segmentation_count": {"allowed_increase": 0},
    }


def summary(**overrides):
    result = {
        "evaluation_kind": "golden_labels",
        "evaluation_id": "eval",
        "point_miou": 0.8,
        "instance_f1": 0.8,
        "mean_box_iou": 0.7,
        "noise_f1": 0.9,
        "over_segmentation_count": 1,
        "under_segmentation_count": 1,
    }
    result.update(overrides)
    return result


def test_candidate_improvement_passes_gate():
    comparison, gate = build_regression_comparison(
        summary(),
        summary(
            point_miou=0.85,
            instance_f1=0.84,
            mean_box_iou=0.75,
            noise_f1=0.91,
            over_segmentation_count=0,
            under_segmentation_count=1,
        ),
        thresholds(),
    )

    assert comparison["metrics"]["point_miou"]["absolute_delta"] == pytest.approx(
        0.05
    )
    assert gate["status"] == "passed"
    assert gate["findings"] == []


def test_metric_drop_beyond_tolerance_fails_gate():
    _, gate = build_regression_comparison(
        summary(),
        summary(point_miou=0.7),
        thresholds(),
    )

    assert gate["status"] == "failed"
    assert gate["findings"][0]["metric"] == "point_miou"
    assert gate["findings"][0]["code"] == "metric_regression"


def test_increased_split_and_merge_counts_fail_gate():
    _, gate = build_regression_comparison(
        summary(),
        summary(over_segmentation_count=2, under_segmentation_count=3),
        thresholds(),
    )

    assert gate["status"] == "failed"
    assert {item["metric"] for item in gate["findings"]} == {
        "over_segmentation_count",
        "under_segmentation_count",
    }


def test_thresholds_are_recorded_in_gate():
    _, gate = build_regression_comparison(
        summary(), summary(), thresholds()
    )

    assert gate["thresholds"] == thresholds()


def test_comparison_reports_available_scene_and_class_deltas():
    baseline = summary(
        by_scene={"pipe-rack": {"instance_f1": 0.8}},
        by_class={"pipe": {"point_miou": 0.7}},
    )
    candidate = summary(
        by_scene={"pipe-rack": {"instance_f1": 0.85}},
        by_class={"pipe": {"point_miou": 0.72}},
    )

    comparison, gate = build_regression_comparison(
        baseline, candidate, thresholds()
    )

    assert comparison["by_scene"]["pipe-rack"]["instance_f1"][
        "absolute_delta"
    ] == pytest.approx(0.05)
    assert comparison["by_class"]["pipe"]["point_miou"][
        "absolute_delta"
    ] == pytest.approx(0.02)
    assert gate["status"] == "passed"


def write_evaluation(project, evaluation_id, status="completed", **summary_values):
    evaluation_dir = (
        project
        / "reports"
        / "segmentation_evaluations"
        / "scan"
        / evaluation_id
    )
    evaluation_dir.mkdir(parents=True)
    payload = summary(evaluation_id=evaluation_id, **summary_values)
    manifest = {
        "schema_version": "1.0",
        "evaluation_kind": "golden_labels",
        "evaluation_id": evaluation_id,
        "asset_id": "scan",
        "status": status,
        "summary": payload if status == "completed" else None,
    }
    (evaluation_dir / "evaluation_run.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return evaluation_dir / "evaluation_run.json"


def test_compare_evaluations_writes_immutable_artifacts(tmp_path):
    baseline_path = write_evaluation(tmp_path, "baseline")
    candidate_path = write_evaluation(
        tmp_path, "candidate", instance_f1=0.82
    )
    baseline_before = baseline_path.read_text(encoding="utf-8")
    candidate_before = candidate_path.read_text(encoding="utf-8")

    result = compare_evaluations(
        tmp_path,
        asset_id="scan",
        comparison_id="cmp-001",
        baseline_evaluation_id="baseline",
        candidate_evaluation_id="candidate",
        thresholds=thresholds(),
    )

    comparison_dir = (
        tmp_path
        / "reports"
        / "segmentation_comparisons"
        / "scan"
        / "cmp-001"
    )
    assert result["gate"]["status"] == "passed"
    assert (comparison_dir / "comparison.json").is_file()
    assert (comparison_dir / "regression_gate.json").is_file()
    assert baseline_path.read_text(encoding="utf-8") == baseline_before
    assert candidate_path.read_text(encoding="utf-8") == candidate_before


def test_comparison_requires_completed_evaluations(tmp_path):
    write_evaluation(tmp_path, "baseline", status="failed")
    write_evaluation(tmp_path, "candidate")

    with pytest.raises(ValueError, match="completed"):
        compare_evaluations(
            tmp_path,
            asset_id="scan",
            comparison_id="cmp-001",
            baseline_evaluation_id="baseline",
            candidate_evaluation_id="candidate",
            thresholds=thresholds(),
        )
