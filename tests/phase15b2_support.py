from pathlib import Path

from pc_system.model_matching_identity import Principal


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
AUDITOR = Principal("auditor", frozenset({"auditor"}), "configured_token")

FEATURE_V1 = {
    "schema_version": "1.0",
    "config_id": "retrieval-v1",
    "algorithm_version": "phase15b2-feature-v1",
    "sampling": {
        "algorithm": "sha256_area_weighted_v1",
        "point_count": 16,
        "random_seed": 20260828,
    },
    "radial_bins": 12,
    "voxel_grid_size": 4,
    "minimum_points": 16,
    "maximum_points": 2_000_000,
    "degenerate_eigenvalue_ratio": 0.000001,
    "ambiguous_axis_relative_gap": 0.001,
}

SCORING_V1 = {
    "schema_version": "1.0",
    "config_id": "retrieval-v1",
    "top_k_default": 10,
    "top_k_maximum": 50,
    "production_minimum_coverage": 0.95,
    "weights": {
        "category": 0.20,
        "terms": 0.15,
        "manufacturer_model": 0.10,
        "dimensions": 0.25,
        "shape": 0.20,
        "occupancy": 0.10,
    },
    "dimension_penalties": {
        "model_smaller_multiplier": 2.0,
        "model_larger_multiplier": 0.75,
    },
}

MAPPING_V1 = {
    "schema_version": "1.0",
    "config_id": "retrieval-v1",
    "mappings": {"centrifugal-pump": "pump"},
}

BOX_POINTS = [
    {"x": x, "y": y, "z": z}
    for x in (0.0, 2.0)
    for y in (0.0, 1.0)
    for z in (0.0, 0.5)
] * 2


def _mesh_reader(_path):
    return {
        "vertices": [
            [0, 0, 0],
            [2000, 0, 0],
            [0, 1000, 0],
            [0, 0, 500],
        ],
        "faces": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]],
    }


def prepare_released_models(project_root: Path) -> dict:
    from pc_system.model_import import import_model_version
    from pc_system.model_library import create_model_asset
    from pc_system.model_release import release_model_version
    from pc_system.model_sampling import sample_model_version

    fixture = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"
    for model_id, category_id, display_name in (
        ("pump-a", "pump", "Pump A"),
        ("valve-a", "valve", "Valve A"),
    ):
        create_model_asset(
            project_root,
            model_id=model_id,
            display_name=display_name,
            category_id=category_id,
            manufacturer="Acme",
            model_number=f"{model_id}-100",
            keywords=[category_id],
            tags=["industrial"],
            principal=EXPERT,
            operation_id=f"op-asset-{model_id}",
            request_id=f"req-asset-{model_id}",
            idempotency_key=f"idem-asset-{model_id}",
        )
    for model_id, version_id, supersedes in (
        ("pump-a", "v1", None),
        ("pump-a", "v2", "v1"),
        ("valve-a", "v1", None),
    ):
        import_model_version(
            project_root,
            model_id=model_id,
            version_id=version_id,
            source_path=fixture,
            declared_unit="mm",
            license_name="internal",
            provenance={"supplier": "Acme"},
            supersedes_version_id=supersedes,
            principal=EXPERT,
            operation_id=f"op-import-{model_id}-{version_id}",
            request_id=f"req-import-{model_id}-{version_id}",
            idempotency_key=f"idem-import-{model_id}-{version_id}",
            mesh_reader=_mesh_reader,
        )
    pump_v1 = release_model_version(
        project_root,
        model_id="pump-a",
        version_id="v1",
        release_id="release-pump-v1",
        action="activate",
        expected_current_release_id=None,
        rollback_of_release_id=None,
        reason="Initial release",
        principal=EXPERT,
        operation_id="op-release-pump-v1",
        request_id="req-release-pump-v1",
        idempotency_key="idem-release-pump-v1",
    )
    pump_v2 = release_model_version(
        project_root,
        model_id="pump-a",
        version_id="v2",
        release_id="release-pump-v2",
        action="activate",
        expected_current_release_id=pump_v1["release_id"],
        rollback_of_release_id=None,
        reason="Upgrade",
        principal=EXPERT,
        operation_id="op-release-pump-v2",
        request_id="req-release-pump-v2",
        idempotency_key="idem-release-pump-v2",
    )
    valve_v1 = release_model_version(
        project_root,
        model_id="valve-a",
        version_id="v1",
        release_id="release-valve-v1",
        action="activate",
        expected_current_release_id=None,
        rollback_of_release_id=None,
        reason="Initial release",
        principal=EXPERT,
        operation_id="op-release-valve-v1",
        request_id="req-release-valve-v1",
        idempotency_key="idem-release-valve-v1",
    )
    representation = sample_model_version(
        project_root,
        model_id="pump-a",
        version_id="v2",
        point_count=FEATURE_V1["sampling"]["point_count"],
        random_seed=FEATURE_V1["sampling"]["random_seed"],
        principal=EXPERT,
        operation_id="op-sample-pump-v2",
        request_id="req-sample-pump-v2",
        idempotency_key="idem-sample-pump-v2",
        mesh_reader=_mesh_reader,
    )
    return {
        "pump_v1_release": pump_v1,
        "pump_v2_release": pump_v2,
        "valve_v1_release": valve_v1,
        "pump_v2_representation": representation,
    }
