import copy

import pytest

from pc_system.mesh_quality import evaluate_mesh_quality
from pc_system.mesh_reconstruction_config import build_mesh_quality_policy
from pc_system.scan_to_mesh_errors import ScanToMeshError


POLICY = {
    "schema_version": "1.0",
    "policy_kind": "engineering_fixture",
    "production_context": None,
    "requires_watertight": False,
    "hard_limits": {
        "maximum_non_finite_vertices": 0,
        "maximum_non_finite_faces": 0,
        "maximum_degenerate_face_fraction": 0.02,
        "maximum_non_manifold_edge_fraction": 0.05,
        "maximum_component_count": 20,
        "minimum_largest_component_fraction": 0.80,
        "minimum_point_coverage": 0.75,
        "maximum_point_to_mesh_p95_m": 0.05,
        "maximum_repair_displacement_m": 0.01,
        "maximum_triangles": 4000,
    },
    "review_limits": {
        "maximum_boundary_edge_fraction": 0.10,
        "maximum_component_count": 3,
        "minimum_largest_component_fraction": 0.95,
        "minimum_point_coverage": 0.90,
        "maximum_point_to_mesh_p95_m": 0.02,
        "maximum_repair_displacement_m": 0.005,
    },
    "decisions": {
        "hard_failure": "rejected",
        "review_threshold": "review_required",
        "all_passed": "passed",
        "auto_publish_passed": False,
    },
}


def metrics():
    return {
        "schema_version": "1.0",
        "raw_vertex_count": 700,
        "raw_triangle_count": 1100,
        "repaired_vertex_count": 650,
        "repaired_triangle_count": 1000,
        "non_finite_vertex_count": 0,
        "non_finite_face_count": 0,
        "duplicate_vertex_count": 0,
        "degenerate_triangle_count": 1,
        "non_manifold_edge_count": 2,
        "boundary_edge_count": 10,
        "component_count": 1,
        "largest_component_fraction": 0.99,
        "normal_consistency_fraction": 0.99,
        "point_to_mesh_p50_m": 0.005,
        "point_to_mesh_p95_m": 0.01,
        "point_to_mesh_max_m": 0.03,
        "mesh_to_point_coverage": 0.95,
        "repair_max_displacement_m": 0.001,
    }


def policy(**changes):
    value = copy.deepcopy(POLICY)
    for key, replacement in changes.items():
        value[key] = replacement
    return build_mesh_quality_policy("mesh-quality-v1", value)


def test_quality_gate_returns_passed_with_stable_evidence():
    report = evaluate_mesh_quality(metrics(), policy())

    assert report["status"] == "passed"
    assert report["reasons"] == []
    assert report["summary_zh"] == "网格质量门禁通过。"
    assert report["derived"]["degenerate_face_fraction"] == 0.001
    assert report["policy_id"] == "mesh-quality-v1"
    assert len(report["metrics_fingerprint"]) == 64


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("mesh_to_point_coverage", 0.85, "point_coverage_review"),
        ("component_count", 4, "component_count_review"),
        ("point_to_mesh_p95_m", 0.03, "point_distance_review"),
        ("repair_max_displacement_m", 0.007, "repair_displacement_review"),
        ("boundary_edge_count", 400, "boundary_edges_review"),
    ],
)
def test_quality_gate_returns_review_required(field, value, reason):
    value_metrics = metrics()
    value_metrics[field] = value

    report = evaluate_mesh_quality(value_metrics, policy())

    assert report["status"] == "review_required"
    assert reason in report["reasons"]
    assert report["summary_zh"] == "网格质量需要专家复核。"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("non_finite_vertex_count", 1, "non_finite_vertices"),
        ("non_finite_face_count", 1, "non_finite_faces"),
        ("degenerate_triangle_count", 30, "degenerate_faces"),
        ("non_manifold_edge_count", 160, "non_manifold_edges"),
        ("component_count", 21, "component_count_exceeded"),
        ("largest_component_fraction", 0.79, "largest_component_too_small"),
        ("mesh_to_point_coverage", 0.70, "point_coverage_insufficient"),
        ("point_to_mesh_p95_m", 0.06, "point_distance_exceeded"),
        ("repair_max_displacement_m", 0.02, "repair_displacement_exceeded"),
        ("repaired_triangle_count", 4001, "triangle_limit_exceeded"),
    ],
)
def test_quality_gate_returns_rejected_for_hard_failures(field, value, reason):
    value_metrics = metrics()
    value_metrics[field] = value
    if field == "point_to_mesh_p95_m":
        value_metrics["point_to_mesh_max_m"] = value

    report = evaluate_mesh_quality(value_metrics, policy())

    assert report["status"] == "rejected"
    assert reason in report["reasons"]
    assert report["summary_zh"] == "网格质量未通过硬门禁。"


def test_watertight_policy_rejects_any_boundary_edge():
    strict = copy.deepcopy(POLICY)
    strict["requires_watertight"] = True

    report = evaluate_mesh_quality(
        metrics(), build_mesh_quality_policy("watertight-v1", strict)
    )

    assert report["status"] == "rejected"
    assert "watertight_required" in report["reasons"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "0.1"])
def test_quality_gate_rejects_nonfinite_or_wrongly_typed_metrics(value):
    value_metrics = metrics()
    value_metrics["point_to_mesh_p95_m"] = value

    with pytest.raises(ScanToMeshError) as caught:
        evaluate_mesh_quality(value_metrics, policy())

    assert caught.value.code == "mesh_quality_invalid"
