import copy
import json

import pytest

from pc_system.mesh_reconstruction_config import (
    build_mesh_config,
    build_mesh_quality_policy,
    list_mesh_configs,
    list_mesh_quality_policies,
    load_mesh_config,
    load_mesh_quality_policy,
    publish_mesh_config,
    publish_mesh_quality_policy,
)
from pc_system.model_matching_audit import load_operation
from pc_system.model_matching_identity import Principal
from pc_system.scan_to_mesh_errors import ScanToMeshError


EXPERT = Principal("mesh-expert", frozenset({"expert"}), "configured_token")
AUDITOR = Principal("mesh-auditor", frozenset({"auditor"}), "configured_token")


POISSON_CONFIG = {
    "schema_version": "1.0",
    "algorithm": "poisson",
    "worker_version": "open3d-mesh-0.19-v1",
    "normal_estimation": {
        "radius_m": 0.05,
        "max_nn": 30,
        "orientation": "consistent_tangent_plane",
        "orientation_k": 20,
    },
    "voxel_size_m": 0.01,
    "poisson": {"depth": 9, "density_quantile": 0.02},
    "ball_pivoting": None,
    "repair": {
        "remove_duplicate_vertices": True,
        "remove_degenerate_triangles": True,
        "remove_non_manifold_duplicates": True,
        "minimum_component_triangles": 20,
        "maximum_removed_component_fraction": 0.02,
        "fill_holes": False,
    },
    "preview": {"target_triangles": 50_000},
    "limits": {
        "maximum_input_points": 1_000_000,
        "maximum_vertices": 2_000_000,
        "maximum_triangles": 4_000_000,
        "maximum_artifact_bytes": 536_870_912,
        "timeout_seconds": 600,
    },
    "random_seed": 17,
}


BALL_PIVOTING_CONFIG = {
    **copy.deepcopy(POISSON_CONFIG),
    "algorithm": "ball_pivoting",
    "poisson": None,
    "ball_pivoting": {"radii_m": [0.01, 0.02, 0.04]},
}


QUALITY_POLICY = {
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
        "maximum_triangles": 4_000_000,
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


def _publish_config(root, *, sequence="1", config=None, principal=EXPERT):
    return publish_mesh_config(
        root,
        config_id="mesh-poisson-v1",
        config=POISSON_CONFIG if config is None else config,
        principal=principal,
        operation_id=f"mesh-config-{sequence}",
        request_id=f"request-mesh-config-{sequence}",
        idempotency_key=f"idem-mesh-config-{sequence}",
    )


def _publish_policy(root, *, sequence="1", policy=None, principal=EXPERT):
    return publish_mesh_quality_policy(
        root,
        policy_id="mesh-quality-v1",
        policy=QUALITY_POLICY if policy is None else policy,
        principal=principal,
        operation_id=f"mesh-policy-{sequence}",
        request_id=f"request-mesh-policy-{sequence}",
        idempotency_key=f"idem-mesh-policy-{sequence}",
    )


def test_poisson_and_ball_pivoting_configs_are_canonical_and_fingerprinted():
    poisson = build_mesh_config("mesh-poisson-v1", POISSON_CONFIG)
    pivoting = build_mesh_config("mesh-bpa-v1", BALL_PIVOTING_CONFIG)

    assert poisson["schema_version"] == "1.0"
    assert poisson["algorithm"] == "poisson"
    assert poisson["ball_pivoting"] is None
    assert pivoting["algorithm"] == "ball_pivoting"
    assert pivoting["poisson"] is None
    assert pivoting["ball_pivoting"]["radii_m"] == [0.01, 0.02, 0.04]
    assert len(poisson["config_fingerprint"]) == 64
    assert len(pivoting["config_fingerprint"]) == 64
    assert poisson["config_fingerprint"] != pivoting["config_fingerprint"]


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.__setitem__("algorithm", "silent-auto"),
        lambda value: value.__setitem__(
            "ball_pivoting", {"radii_m": [0.01]}
        ),
        lambda value: value["normal_estimation"].__setitem__(
            "radius_m", float("nan")
        ),
        lambda value: value["limits"].__setitem__(
            "maximum_input_points", 1_000_001
        ),
        lambda value: value["repair"].__setitem__("fill_holes", True),
        lambda value: value.__setitem__("unknown", 1),
    ],
)
def test_mesh_config_rejects_unknown_mixed_non_finite_or_unbounded_values(change):
    value = copy.deepcopy(POISSON_CONFIG)
    change(value)

    with pytest.raises(ScanToMeshError) as caught:
        build_mesh_config("mesh-invalid", value)

    assert caught.value.code == "mesh_config_invalid"


def test_config_and_policy_publication_is_immutable_audited_and_replayable(tmp_path):
    config = _publish_config(tmp_path)
    policy = _publish_policy(tmp_path)

    assert _publish_config(tmp_path) == config
    assert _publish_policy(tmp_path) == policy
    assert _publish_config(tmp_path, sequence="2") == config
    assert _publish_policy(tmp_path, sequence="2") == policy
    assert load_mesh_config(tmp_path, "mesh-poisson-v1") == config
    assert load_mesh_quality_policy(tmp_path, "mesh-quality-v1") == policy
    assert list_mesh_configs(tmp_path) == [config]
    assert list_mesh_quality_policies(tmp_path) == [policy]
    assert load_operation(tmp_path, "mesh-config-1")["status"] == "completed"
    assert load_operation(tmp_path, "mesh-config-2")["status"] == "completed"
    assert load_operation(tmp_path, "mesh-policy-1")["status"] == "completed"
    assert load_operation(tmp_path, "mesh-policy-2")["status"] == "completed"

    changed = copy.deepcopy(POISSON_CONFIG)
    changed["poisson"]["depth"] = 10
    with pytest.raises(ScanToMeshError) as caught:
        _publish_config(tmp_path, sequence="3", config=changed)
    assert caught.value.code == "mesh_config_integrity_error"


@pytest.mark.parametrize("kind", ["config", "policy"])
def test_nonexpert_publication_is_denied_and_audited(tmp_path, kind):
    if kind == "config":
        call = lambda: _publish_config(
            tmp_path, sequence="auditor", principal=AUDITOR
        )
        operation_id = "mesh-config-auditor"
    else:
        call = lambda: _publish_policy(
            tmp_path, sequence="auditor", principal=AUDITOR
        )
        operation_id = "mesh-policy-auditor"

    with pytest.raises(ScanToMeshError) as caught:
        call()

    assert caught.value.code == "permission_denied"
    assert load_operation(tmp_path, operation_id)["status"] == "failed"


def test_quality_policy_cannot_configure_hard_failures_as_passed():
    valid = build_mesh_quality_policy("mesh-quality-v1", QUALITY_POLICY)
    assert valid["decisions"]["hard_failure"] == "rejected"

    for field, replacement in (
        ("hard_failure", "passed"),
        ("review_threshold", "passed"),
        ("all_passed", "review_required"),
    ):
        policy = copy.deepcopy(QUALITY_POLICY)
        policy["decisions"][field] = replacement
        with pytest.raises(ScanToMeshError) as caught:
            build_mesh_quality_policy("mesh-quality-invalid", policy)
        assert caught.value.code == "mesh_policy_invalid"


def test_production_policy_requires_scanning_and_approval_context():
    missing = copy.deepcopy(QUALITY_POLICY)
    missing["policy_kind"] = "production"
    with pytest.raises(ScanToMeshError) as caught:
        build_mesh_quality_policy("mesh-quality-production", missing)
    assert caught.value.code == "mesh_policy_invalid"

    production = copy.deepcopy(missing)
    production["production_context"] = {
        "scanner_model": "Leica RTC360",
        "nominal_point_spacing_m": 0.003,
        "preprocessing": "统一坐标、去除离群点并裁剪为单对象",
        "object_families": ["pipe", "flange"],
        "approved_by": "mesh-expert",
    }
    value = build_mesh_quality_policy("mesh-quality-production", production)
    assert value["production_context"] == {
        **production["production_context"],
        "object_families": ["flange", "pipe"],
    }


@pytest.mark.parametrize("kind", ["config", "policy"])
def test_changed_published_artifact_is_rejected(tmp_path, kind):
    if kind == "config":
        _publish_config(tmp_path)
        path = (
            tmp_path
            / "scan_to_cad"
            / "mesh_configs"
            / "mesh-poisson-v1"
            / "config.json"
        )
        load = lambda: load_mesh_config(tmp_path, "mesh-poisson-v1")
        code = "mesh_config_integrity_error"
    else:
        _publish_policy(tmp_path)
        path = (
            tmp_path
            / "scan_to_cad"
            / "mesh_quality_policies"
            / "mesh-quality-v1"
            / "policy.json"
        )
        load = lambda: load_mesh_quality_policy(tmp_path, "mesh-quality-v1")
        code = "mesh_policy_integrity_error"
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(ScanToMeshError) as caught:
        load()

    assert caught.value.code == code


def test_config_owner_must_remain_bound_to_the_producer_audit(tmp_path):
    _publish_config(tmp_path)
    path = (
        tmp_path
        / "scan_to_cad"
        / "mesh_configs"
        / "mesh-poisson-v1"
        / "owner.json"
    )
    owner = json.loads(path.read_text(encoding="utf-8"))
    owner["request_id"] = "different-request"
    path.write_bytes(
        json.dumps(
            owner,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
    )

    with pytest.raises(ScanToMeshError) as caught:
        load_mesh_config(tmp_path, "mesh-poisson-v1")

    assert caught.value.code == "mesh_config_integrity_error"
