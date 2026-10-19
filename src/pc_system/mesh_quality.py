"""网格质量指标规范化与确定性三态门禁。"""

import hashlib
import json
import math

from pc_system.mesh_reconstruction_config import build_mesh_quality_policy
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.scan_to_mesh_errors import ScanToMeshError


_METRIC_FIELDS = {
    "schema_version",
    "raw_vertex_count",
    "raw_triangle_count",
    "repaired_vertex_count",
    "repaired_triangle_count",
    "non_finite_vertex_count",
    "non_finite_face_count",
    "duplicate_vertex_count",
    "degenerate_triangle_count",
    "non_manifold_edge_count",
    "boundary_edge_count",
    "component_count",
    "largest_component_fraction",
    "normal_consistency_fraction",
    "point_to_mesh_p50_m",
    "point_to_mesh_p95_m",
    "point_to_mesh_max_m",
    "mesh_to_point_coverage",
    "repair_max_displacement_m",
}
_POLICY_FIELDS = {
    "schema_version",
    "policy_kind",
    "production_context",
    "requires_watertight",
    "hard_limits",
    "review_limits",
    "decisions",
}
_INTEGER_FIELDS = {
    "raw_vertex_count",
    "raw_triangle_count",
    "repaired_vertex_count",
    "repaired_triangle_count",
    "non_finite_vertex_count",
    "non_finite_face_count",
    "duplicate_vertex_count",
    "degenerate_triangle_count",
    "non_manifold_edge_count",
    "boundary_edge_count",
    "component_count",
}
_FRACTION_FIELDS = {
    "largest_component_fraction",
    "normal_consistency_fraction",
    "mesh_to_point_coverage",
}
_DISTANCE_FIELDS = {
    "point_to_mesh_p50_m",
    "point_to_mesh_p95_m",
    "point_to_mesh_max_m",
    "repair_max_displacement_m",
}


def _invalid(message: str) -> ScanToMeshError:
    return ScanToMeshError("mesh_quality_invalid", message)


def _validated_policy(policy: object) -> dict:
    if type(policy) is not dict or type(policy.get("policy_id")) is not str:
        raise _invalid("缺少已发布网格质量策略。")
    try:
        rebuilt = build_mesh_quality_policy(
            policy["policy_id"], {key: policy[key] for key in _POLICY_FIELDS}
        )
    except (KeyError, ModelMatchingError) as exc:
        raise _invalid("网格质量策略无效。") from exc
    if policy.get("policy_fingerprint") != rebuilt["policy_fingerprint"]:
        raise _invalid("网格质量策略指纹不一致。")
    return rebuilt


def _validated_metrics(metrics: object) -> dict:
    if (
        type(metrics) is not dict
        or set(metrics) != _METRIC_FIELDS
        or metrics.get("schema_version") != "1.0"
    ):
        raise _invalid("网格质量指标字段无效。")
    normalized = dict(metrics)
    for field in _INTEGER_FIELDS:
        value = metrics[field]
        if type(value) is not int or value < 0:
            raise _invalid("网格质量整数指标无效。")
    for field in _FRACTION_FIELDS | _DISTANCE_FIELDS:
        value = metrics[field]
        if type(value) not in {int, float} or not math.isfinite(float(value)):
            raise _invalid("网格质量数值指标无效。")
        value = float(value)
        if value < 0 or (field in _FRACTION_FIELDS and value > 1):
            raise _invalid("网格质量数值指标超出范围。")
        normalized[field] = value
    if not (
        normalized["point_to_mesh_p50_m"]
        <= normalized["point_to_mesh_p95_m"]
        <= normalized["point_to_mesh_max_m"]
    ):
        raise _invalid("网格距离分位数顺序无效。")
    if (
        normalized["duplicate_vertex_count"]
        > normalized["repaired_vertex_count"]
        or normalized["degenerate_triangle_count"]
        > normalized["repaired_triangle_count"]
        or normalized["non_finite_face_count"]
        > normalized["repaired_triangle_count"]
        or normalized["non_finite_vertex_count"]
        > normalized["repaired_vertex_count"]
    ):
        raise _invalid("网格问题计数超过网格规模。")
    if normalized["repaired_triangle_count"] and not normalized["component_count"]:
        raise _invalid("非空网格必须包含连通组件。")
    return normalized


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / max(denominator, 1), 12)


def evaluate_mesh_quality(metrics: object, policy: object) -> dict:
    """依据不可变策略返回 passed、review_required 或 rejected。"""

    values = _validated_metrics(metrics)
    selected = _validated_policy(policy)
    triangles = values["repaired_triangle_count"]
    derived = {
        "degenerate_face_fraction": _ratio(
            values["degenerate_triangle_count"], triangles
        ),
        "non_manifold_edge_fraction": _ratio(
            values["non_manifold_edge_count"], triangles * 3
        ),
        "boundary_edge_fraction": _ratio(
            values["boundary_edge_count"], triangles * 3
        ),
    }
    hard = selected["hard_limits"]
    review = selected["review_limits"]
    hard_reasons = set()
    review_reasons = set()
    if values["non_finite_vertex_count"] > hard["maximum_non_finite_vertices"]:
        hard_reasons.add("non_finite_vertices")
    if values["non_finite_face_count"] > hard["maximum_non_finite_faces"]:
        hard_reasons.add("non_finite_faces")
    if derived["degenerate_face_fraction"] > hard["maximum_degenerate_face_fraction"]:
        hard_reasons.add("degenerate_faces")
    if derived["non_manifold_edge_fraction"] > hard["maximum_non_manifold_edge_fraction"]:
        hard_reasons.add("non_manifold_edges")
    if values["component_count"] > hard["maximum_component_count"]:
        hard_reasons.add("component_count_exceeded")
    if values["largest_component_fraction"] < hard["minimum_largest_component_fraction"]:
        hard_reasons.add("largest_component_too_small")
    if values["mesh_to_point_coverage"] < hard["minimum_point_coverage"]:
        hard_reasons.add("point_coverage_insufficient")
    if values["point_to_mesh_p95_m"] > hard["maximum_point_to_mesh_p95_m"]:
        hard_reasons.add("point_distance_exceeded")
    if values["repair_max_displacement_m"] > hard["maximum_repair_displacement_m"]:
        hard_reasons.add("repair_displacement_exceeded")
    if triangles > hard["maximum_triangles"]:
        hard_reasons.add("triangle_limit_exceeded")
    if selected["requires_watertight"] and values["boundary_edge_count"]:
        hard_reasons.add("watertight_required")

    if derived["boundary_edge_fraction"] > review["maximum_boundary_edge_fraction"]:
        review_reasons.add("boundary_edges_review")
    if values["component_count"] > review["maximum_component_count"]:
        review_reasons.add("component_count_review")
    if values["largest_component_fraction"] < review["minimum_largest_component_fraction"]:
        review_reasons.add("largest_component_review")
    if values["mesh_to_point_coverage"] < review["minimum_point_coverage"]:
        review_reasons.add("point_coverage_review")
    if values["point_to_mesh_p95_m"] > review["maximum_point_to_mesh_p95_m"]:
        review_reasons.add("point_distance_review")
    if values["repair_max_displacement_m"] > review["maximum_repair_displacement_m"]:
        review_reasons.add("repair_displacement_review")

    if hard_reasons:
        status = selected["decisions"]["hard_failure"]
        reasons = sorted(hard_reasons)
        summary = "网格质量未通过硬门禁。"
    elif review_reasons:
        status = selected["decisions"]["review_threshold"]
        reasons = sorted(review_reasons)
        summary = "网格质量需要专家复核。"
    else:
        status = selected["decisions"]["all_passed"]
        reasons = []
        summary = "网格质量门禁通过。"
    return {
        "schema_version": "1.0",
        "status": status,
        "reasons": reasons,
        "summary_zh": summary,
        "policy_id": selected["policy_id"],
        "policy_fingerprint": selected["policy_fingerprint"],
        "metrics_fingerprint": hashlib.sha256(
            json.dumps(
                metrics,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "derived": derived,
    }
