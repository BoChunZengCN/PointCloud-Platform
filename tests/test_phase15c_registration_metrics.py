import copy

import numpy as np
import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_registration_metrics import compute_registration_metrics
from phase15c_support import (
    IDENTITY_TRANSFORM,
    MODEL_POINTS,
    OBJECT_POINTS,
    REGISTRATION_V1,
)


def test_metrics_keep_directional_coverage_and_chamfer_separate():
    evidence = {
        "observed_to_model_distances_m": [0.01, 0.02, 0.50],
        "model_to_observed_distances_m": [0.01, 0.50, 0.50, 0.50],
        "normal_cosines": None,
    }

    metrics = compute_registration_metrics(
        MODEL_POINTS,
        OBJECT_POINTS,
        IDENTITY_TRANSFORM,
        evidence,
        REGISTRATION_V1["residual_metrics"],
    )

    assert metrics["observed_to_model_coverage"] == pytest.approx(2 / 3)
    assert metrics["model_to_observed_coverage"] == pytest.approx(1 / 4)
    assert metrics["observed_to_model_inlier_count"] == 2
    assert metrics["model_to_observed_inlier_count"] == 1
    assert metrics["chamfer_distance_m"] == pytest.approx(
        (np.mean([0.01, 0.02, 0.50]) + np.mean([0.01, 0.50, 0.50, 0.50])) / 2
    )
    assert metrics["p95_distance_m"] >= metrics["p50_distance_m"]


def test_metrics_measure_model_to_object_dimensions_after_transform():
    transform = copy.deepcopy(IDENTITY_TRANSFORM)
    transform[0][3] = 1.0
    transform[1][3] = 2.0
    transform[2][3] = 3.0
    evidence = {
        "observed_to_model_distances_m": [0.0] * len(OBJECT_POINTS),
        "model_to_observed_distances_m": [0.0] * len(MODEL_POINTS),
        "normal_cosines": [1.0, 0.9, 0.7, -1.0],
    }

    metrics = compute_registration_metrics(
        MODEL_POINTS,
        OBJECT_POINTS,
        transform,
        evidence,
        REGISTRATION_V1["residual_metrics"],
    )

    assert metrics["maximum_dimension_relative_error"] == pytest.approx(0.0)
    assert metrics["dimension_relative_errors"] == pytest.approx([0.0, 0.0, 0.0])
    assert metrics["normal_consistency"] == pytest.approx(3 / 4)
    assert metrics["inlier_rmse_m"] == pytest.approx(0.0)


def test_metrics_accept_zero_normal_consistency_threshold_from_valid_config():
    config = copy.deepcopy(REGISTRATION_V1["residual_metrics"])
    config["normal_consistency_minimum"] = 0.0

    metrics = compute_registration_metrics(
        MODEL_POINTS,
        OBJECT_POINTS,
        IDENTITY_TRANSFORM,
        {
            "observed_to_model_distances_m": [0.01],
            "model_to_observed_distances_m": [0.01],
            "normal_cosines": [0.0],
        },
        config,
    )

    assert metrics["normal_consistency"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_to_model_distances_m", []),
        ("observed_to_model_distances_m", [-0.01]),
        ("model_to_observed_distances_m", [float("nan")]),
        ("normal_cosines", [1.1]),
    ],
)
def test_metrics_reject_invalid_engine_evidence(field, value):
    evidence = {
        "observed_to_model_distances_m": [0.01],
        "model_to_observed_distances_m": [0.01],
        "normal_cosines": None,
    }
    evidence[field] = value

    with pytest.raises(ModelMatchingError) as caught:
        compute_registration_metrics(
            MODEL_POINTS,
            OBJECT_POINTS,
            IDENTITY_TRANSFORM,
            evidence,
            REGISTRATION_V1["residual_metrics"],
        )

    assert caught.value.code == "registration_engine_failed"
