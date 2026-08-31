from phase15b2_support import AUDITOR, EXPERT


REGISTRATION_V1 = {
    "schema_version": "1.0",
    "engine_name": "deterministic-test",
    "preprocessing": {
        "voxel_sizes_m": [0.08, 0.04, 0.02],
        "normal_radius_multiplier": 2.5,
        "fpfh_radius_multiplier": 5.0,
        "normal_max_nn": 30,
        "fpfh_max_nn": 100,
        "minimum_points": 8,
        "maximum_points": 2_000_000,
    },
    "initial_hypotheses": {
        "include_identity": True,
        "include_principal_axes": True,
        "maximum_hypotheses": 24,
        "rotation_dedup_tolerance_rad": 0.001,
        "translation_dedup_tolerance_m": 0.001,
    },
    "coarse_registration": {
        "method": "ransac",
        "fgr_enabled": False,
        "ransac_n": 4,
        "maximum_iterations": 100_000,
        "confidence": 0.999,
        "distance_multiplier": 1.5,
        "edge_length_ratio": 0.9,
        "normal_angle_rad": 0.5235987755982988,
        "top_n": 4,
        "random_seed": 20260831,
    },
    "fine_registration": {
        "levels": [
            {
                "voxel_size_m": 0.08,
                "max_correspondence_distance_m": 0.12,
                "maximum_iterations": 40,
            },
            {
                "voxel_size_m": 0.04,
                "max_correspondence_distance_m": 0.06,
                "maximum_iterations": 30,
            },
            {
                "voxel_size_m": 0.02,
                "max_correspondence_distance_m": 0.03,
                "maximum_iterations": 20,
            },
        ],
        "relative_fitness": 1e-6,
        "relative_rmse": 1e-6,
    },
    "transform_validation": {
        "homogeneous_tolerance": 1e-8,
        "orthogonality_tolerance": 1e-6,
        "determinant_tolerance": 1e-6,
        "singular_value_tolerance": 1e-6,
        "maximum_translation_m": 1000.0,
        "maximum_rotation_rad": 3.141592653589793,
    },
    "residual_metrics": {
        "inlier_distance_m": 0.03,
        "normal_consistency_minimum": 0.8,
    },
    "quality_gates": {
        "passed_observed_coverage": 0.85,
        "passed_model_coverage": 0.70,
        "review_observed_coverage": 0.70,
        "review_model_coverage": 0.30,
        "maximum_inlier_rmse_m": 0.02,
        "maximum_chamfer_m": 0.04,
        "maximum_dimension_relative_error": 0.10,
        "minimum_pose_score_margin": 0.05,
        "maximum_fine_regression_ratio": 1.05,
    },
    "category_overrides": {},
}


MODEL_POINTS = [
    [0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.0, 2.0, 0.0],
    [0.0, 0.0, 3.0],
    [1.0, 2.0, 3.0],
    [0.25, 0.75, 1.5],
    [0.8, 1.5, 2.5],
    [0.4, 1.7, 0.6],
]
OBJECT_POINTS = [[x + 1.0, y + 2.0, z + 3.0] for x, y, z in MODEL_POINTS]
IDENTITY_TRANSFORM = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


__all__ = [
    "AUDITOR",
    "EXPERT",
    "IDENTITY_TRANSFORM",
    "MODEL_POINTS",
    "OBJECT_POINTS",
    "REGISTRATION_V1",
]
