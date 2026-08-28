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
