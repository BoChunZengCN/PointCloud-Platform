import copy

import pytest

from pc_system.model_registration_gate import evaluate_registration_gate
from phase15c_support import REGISTRATION_V1


def _metrics(**overrides):
    result = {
        "observed_to_model_coverage": 0.95,
        "model_to_observed_coverage": 0.90,
        "inlier_rmse_m": 0.01,
        "chamfer_distance_m": 0.02,
        "maximum_dimension_relative_error": 0.02,
    }
    result.update(overrides)
    return result


def _gate(metrics=None, **overrides):
    arguments = {
        "metrics": metrics or _metrics(),
        "coarse_metrics": {"rmse_m": 0.018},
        "fine_metrics": {"rmse_m": 0.014},
        "pose_score_margin": 0.20,
        "symmetry_equivalent": False,
        "config": REGISTRATION_V1,
    }
    arguments.update(overrides)
    return evaluate_registration_gate(**arguments)


def test_complete_high_quality_match_passes():
    result = _gate()

    assert result == {
        "status": "passed",
        "reasons": [],
        "fine_regression_ratio": pytest.approx(0.014 / 0.018),
    }


def test_partial_occlusion_requires_review_instead_of_rejection():
    result = _gate(
        _metrics(
            observed_to_model_coverage=0.90,
            model_to_observed_coverage=0.45,
            inlier_rmse_m=0.014,
            chamfer_distance_m=0.025,
            maximum_dimension_relative_error=0.04,
        )
    )

    assert result["status"] == "review_required"
    assert result["reasons"] == ["partial_observation"]


@pytest.mark.parametrize(
    "metrics,reason",
    [
        (
            _metrics(observed_to_model_coverage=0.60),
            "insufficient_observed_coverage",
        ),
        (_metrics(model_to_observed_coverage=0.20), "insufficient_model_coverage"),
        (_metrics(inlier_rmse_m=0.03), "inlier_rmse_exceeded"),
        (_metrics(chamfer_distance_m=0.05), "chamfer_exceeded"),
        (
            _metrics(maximum_dimension_relative_error=0.15),
            "dimension_mismatch",
        ),
    ],
)
def test_hard_quality_failures_are_rejected(metrics, reason):
    result = _gate(metrics)

    assert result["status"] == "rejected"
    assert reason in result["reasons"]
    assert "registration_gate_rejected" in result["reasons"]
    assert result["reasons"] == sorted(set(result["reasons"]))


@pytest.mark.parametrize(
    "symmetry_equivalent,reason",
    [
        (False, "ambiguous_pose"),
        (True, "equivalent_symmetric_pose"),
    ],
)
def test_close_pose_scores_require_review(symmetry_equivalent, reason):
    result = _gate(
        pose_score_margin=0.01,
        symmetry_equivalent=symmetry_equivalent,
    )

    assert result["status"] == "review_required"
    assert result["reasons"] == [reason]


def test_fine_regression_is_reviewed_then_rejected_at_policy_limit():
    reviewed = _gate(
        coarse_metrics={"rmse_m": 0.010},
        fine_metrics={"rmse_m": 0.0102},
    )
    rejected = _gate(
        coarse_metrics={"rmse_m": 0.010},
        fine_metrics={"rmse_m": 0.011},
    )

    assert reviewed["status"] == "review_required"
    assert reviewed["reasons"] == ["fine_registration_regressed"]
    assert rejected["status"] == "rejected"
    assert rejected["reasons"] == [
        "fine_registration_regressed",
        "registration_gate_rejected",
    ]


def test_zero_coarse_rmse_keeps_rejected_result_json_safe():
    result = _gate(
        coarse_metrics={"rmse_m": 0.0},
        fine_metrics={"rmse_m": 0.001},
    )

    assert result["status"] == "rejected"
    assert result["fine_regression_ratio"] is None
    assert "fine_registration_regressed" in result["reasons"]


def test_category_override_replaces_default_quality_policy():
    config = copy.deepcopy(REGISTRATION_V1)
    config["category_overrides"] = {
        "valve": {
            **config["quality_gates"],
            "passed_model_coverage": 0.40,
        }
    }

    result = _gate(
        _metrics(category_id="valve", model_to_observed_coverage=0.45),
        config=config,
    )

    assert result["status"] == "passed"
