from pathlib import Path

import copy
import numpy as np

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


def prepare_schema_1_1_retrieval(project_root: Path) -> dict:
    from test_phase15b2_retrieval import _prepare_project, _retrieve

    _prepare_project(project_root)
    return _retrieve(project_root)


class DeterministicRegistrationEngine:
    def __init__(self, mode: str = "passed"):
        self.mode = mode
        self.calls = {
            "preprocess": 0,
            "coarse_register": 0,
            "fine_register": 0,
            "nearest_neighbor_evidence": 0,
        }

    def describe(self):
        from pc_system.model_registration_engine import EngineDescription

        return EngineDescription("deterministic-test", "1.0", False)

    def preprocess(self, model_points, object_points, config):
        self.calls["preprocess"] += 1
        return {
            "model_points": np.asarray(model_points, dtype=np.float64),
            "object_points": np.asarray(object_points, dtype=np.float64),
        }

    def coarse_register(self, prepared, hypotheses, config):
        self.calls["coarse_register"] += 1
        if self.mode == "coarse_failed":
            return []
        return [
            {
                "hypothesis_id": hypotheses[0]["hypothesis_id"],
                "source": hypotheses[0]["source"],
                "matrix": self._matrix(),
                "score": 0.90,
                "coarse_metrics": {"rmse_m": 0.018, "fitness": 0.90},
            }
        ]

    def fine_register(self, prepared, coarse_results, config):
        self.calls["fine_register"] += 1
        if self.mode == "fine_failed":
            return []
        coarse = coarse_results[0]
        return [
            {
                **coarse,
                "matrix": self._matrix(),
                "score": 0.95,
                "fine_metrics": {"rmse_m": 0.014, "fitness": 0.95},
                "symmetry_equivalent": False,
            }
        ]

    def nearest_neighbor_evidence(self, prepared, transform, config):
        self.calls["nearest_neighbor_evidence"] += 1
        observed_count = len(prepared["object_points"])
        model_count = len(prepared["model_points"])
        if self.mode == "rejected":
            observed = [0.10] * observed_count
            model = [0.10] * model_count
        elif self.mode == "review_required":
            observed = [0.01] * observed_count
            split = max(1, model_count // 2)
            model = [0.01] * split + [0.05] * (model_count - split)
        else:
            observed = [0.01] * observed_count
            model = [0.01] * model_count
        return {
            "observed_to_model_distances_m": observed,
            "model_to_observed_distances_m": model,
            "normal_cosines": None,
        }

    def _matrix(self):
        if self.mode == "non_rigid":
            return [
                [2.0, 0.0, 0.0, 1.0],
                [0.0, 1.0, 0.0, 2.0],
                [0.0, 0.0, 1.0, 3.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        return [
            [1.0, 0.0, 0.0, 1.0],
            [0.0, 1.0, 0.0, 2.0],
            [0.0, 0.0, 1.0, 3.0],
            [0.0, 0.0, 0.0, 1.0],
        ]


def prepare_phase15c_case(project_root: Path) -> dict:
    from pc_system.model_registration_config import publish_registration_config

    retrieval = prepare_schema_1_1_retrieval(project_root)
    config = copy.deepcopy(REGISTRATION_V1)
    config["quality_gates"]["maximum_dimension_relative_error"] = 1.0
    published = publish_registration_config(
        project_root,
        config_id="registration-v1",
        config=config,
        principal=EXPERT,
        operation_id="op-registration-config-v1",
        request_id="req-registration-config-v1",
        idempotency_key="idem-registration-config-v1",
    )
    return {
        "asset_id": retrieval["asset_id"],
        "source_id": retrieval["source_id"],
        "instance_id": retrieval["instance_id"],
        "retrieval_run_id": retrieval["retrieval_run_id"],
        "config_id": published["config_id"],
    }


__all__ = [
    "AUDITOR",
    "DeterministicRegistrationEngine",
    "EXPERT",
    "IDENTITY_TRANSFORM",
    "MODEL_POINTS",
    "OBJECT_POINTS",
    "REGISTRATION_V1",
    "prepare_schema_1_1_retrieval",
    "prepare_phase15c_case",
]
