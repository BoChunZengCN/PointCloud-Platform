import json
import math
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.segmentation_run import utc_now


HIGHER_IS_BETTER = (
    "point_miou",
    "instance_f1",
    "mean_box_iou",
    "noise_f1",
)
LOWER_IS_BETTER = (
    "over_segmentation_count",
    "under_segmentation_count",
)
REQUIRED_METRICS = HIGHER_IS_BETTER + LOWER_IS_BETTER


def _validated_thresholds(thresholds: dict) -> dict:
    normalized = {}
    for metric in REQUIRED_METRICS:
        rule = thresholds.get(metric)
        key = "allowed_drop" if metric in HIGHER_IS_BETTER else "allowed_increase"
        if not isinstance(rule, dict) or key not in rule:
            raise ValueError(f"Regression threshold is required for {metric}.{key}.")
        value = float(rule[key])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Regression threshold must be non-negative: {metric}.{key}")
        normalized[metric] = {key: value}
    return normalized


def _delta(baseline_value: float, candidate_value: float) -> dict:
    absolute_delta = candidate_value - baseline_value
    return {
        "baseline": baseline_value,
        "candidate": candidate_value,
        "absolute_delta": absolute_delta,
        "relative_delta": (
            absolute_delta / abs(baseline_value) if baseline_value else None
        ),
    }


def _scope_deltas(baseline: dict, candidate: dict) -> dict:
    result = {}
    for scope_id in sorted(set(baseline) & set(candidate)):
        baseline_metrics = baseline[scope_id]
        candidate_metrics = candidate[scope_id]
        common_metrics = sorted(set(baseline_metrics) & set(candidate_metrics))
        rows = {}
        for metric in common_metrics:
            try:
                baseline_value = float(baseline_metrics[metric])
                candidate_value = float(candidate_metrics[metric])
            except (TypeError, ValueError):
                continue
            if math.isfinite(baseline_value) and math.isfinite(candidate_value):
                rows[metric] = _delta(baseline_value, candidate_value)
        if rows:
            result[scope_id] = rows
    return result


def _regression_finding(
    metric: str,
    values: dict,
    rule: dict,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
) -> dict | None:
    if metric in HIGHER_IS_BETTER:
        limit = rule["allowed_drop"]
        regressed = values["absolute_delta"] < -limit
        threshold_key = "allowed_drop"
    elif metric in LOWER_IS_BETTER:
        limit = rule["allowed_increase"]
        regressed = values["absolute_delta"] > limit
        threshold_key = "allowed_increase"
    else:
        return None
    if not regressed:
        return None
    finding = {
        "code": "metric_regression",
        "severity": "error",
        "metric": metric,
        "absolute_delta": values["absolute_delta"],
        threshold_key: limit,
        "message": f"Candidate {metric} exceeds the allowed regression threshold.",
    }
    if scope_kind and scope_id:
        finding["scope_kind"] = scope_kind
        finding["scope_id"] = scope_id
    return finding


def build_regression_comparison(
    baseline: dict,
    candidate: dict,
    thresholds: dict,
) -> tuple[dict, dict]:
    """比较两次黄金评估并构造显式回归门禁。"""

    normalized_thresholds = _validated_thresholds(thresholds)
    metrics = {}
    findings = []
    for metric in REQUIRED_METRICS:
        baseline_value = float(baseline[metric])
        candidate_value = float(candidate[metric])
        metrics[metric] = _delta(baseline_value, candidate_value)
        finding = _regression_finding(
            metric, metrics[metric], normalized_thresholds[metric]
        )
        if finding:
            findings.append(finding)

    by_scene = _scope_deltas(
        dict(baseline.get("by_scene", {})), dict(candidate.get("by_scene", {}))
    )
    by_class = _scope_deltas(
        dict(baseline.get("by_class", {})), dict(candidate.get("by_class", {}))
    )
    for scope_kind, scopes in (("scene", by_scene), ("class", by_class)):
        for scope_id, scoped_metrics in scopes.items():
            for metric, values in scoped_metrics.items():
                if metric not in normalized_thresholds:
                    continue
                finding = _regression_finding(
                    metric,
                    values,
                    normalized_thresholds[metric],
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                )
                if finding:
                    findings.append(finding)

    comparison = {
        "schema_version": "1.0",
        "baseline_evaluation_id": baseline.get("evaluation_id"),
        "candidate_evaluation_id": candidate.get("evaluation_id"),
        "metrics": metrics,
        "by_scene": by_scene,
        "by_class": by_class,
    }
    gate = {
        "schema_version": "1.0",
        "status": "failed" if findings else "passed",
        "thresholds": normalized_thresholds,
        "finding_count": len(findings),
        "findings": findings,
    }
    return comparison, gate


def _load_completed_evaluation(
    project_root: Path, asset_id: str, evaluation_id: str
) -> dict:
    path = (
        project_root
        / "reports"
        / "segmentation_evaluations"
        / asset_id
        / evaluation_id
        / "evaluation_run.json"
    )
    if not path.is_file():
        raise FileNotFoundError(path)
    evaluation = json.loads(path.read_text(encoding="utf-8"))
    if evaluation.get("status") != "completed" or not evaluation.get("summary"):
        raise ValueError(
            f"Only completed evaluations can be compared: {evaluation_id}"
        )
    return evaluation


def compare_evaluations(
    project_root: Path,
    *,
    asset_id: str,
    comparison_id: str,
    baseline_evaluation_id: str,
    candidate_evaluation_id: str,
    thresholds: dict,
) -> dict:
    """写出独立且不可覆盖的候选/基线比较与回归门禁。"""

    asset_id = validate_identifier(asset_id, "asset_id")
    comparison_id = validate_identifier(comparison_id, "comparison_id")
    baseline_evaluation_id = validate_identifier(
        baseline_evaluation_id, "baseline_evaluation_id"
    )
    candidate_evaluation_id = validate_identifier(
        candidate_evaluation_id, "candidate_evaluation_id"
    )
    output_dir = (
        project_root
        / "reports"
        / "segmentation_comparisons"
        / asset_id
        / comparison_id
    )
    if output_dir.exists():
        raise FileExistsError(output_dir)
    baseline = _load_completed_evaluation(
        project_root, asset_id, baseline_evaluation_id
    )
    candidate = _load_completed_evaluation(
        project_root, asset_id, candidate_evaluation_id
    )
    comparison, gate = build_regression_comparison(
        baseline["summary"], candidate["summary"], thresholds
    )
    comparison.update(
        {
            "comparison_id": comparison_id,
            "asset_id": asset_id,
            "created_at": utc_now(),
        }
    )
    gate.update(
        {
            "comparison_id": comparison_id,
            "asset_id": asset_id,
            "created_at": utc_now(),
        }
    )
    write_json(comparison, output_dir / "comparison.json")
    write_json(gate, output_dir / "regression_gate.json")
    return {"comparison": comparison, "gate": gate}
