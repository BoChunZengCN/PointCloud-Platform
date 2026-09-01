import math

from pc_system.model_matching_errors import ModelMatchingError


_QUALITY_FIELDS = {
    "passed_observed_coverage",
    "passed_model_coverage",
    "review_observed_coverage",
    "review_model_coverage",
    "maximum_inlier_rmse_m",
    "maximum_chamfer_m",
    "maximum_dimension_relative_error",
    "minimum_pose_score_margin",
    "maximum_fine_regression_ratio",
}


def _invalid(message: str) -> ModelMatchingError:
    return ModelMatchingError("registration_config_invalid", message)


def _number(mapping: dict, field: str, *, nonnegative: bool = True) -> float:
    try:
        raw = mapping[field]
    except (KeyError, TypeError) as exc:
        raise _invalid(f"Registration gate {field} is invalid.") from exc
    if type(raw) not in (int, float):
        raise _invalid(f"Registration gate {field} is invalid.")
    value = float(raw)
    if not math.isfinite(value) or (nonnegative and value < 0.0):
        raise _invalid(f"Registration gate {field} is invalid.")
    return value


def _policy(metrics: dict, config: dict) -> dict:
    try:
        default = config["quality_gates"]
        overrides = config.get("category_overrides", {})
    except (KeyError, TypeError, AttributeError) as exc:
        raise _invalid("Registration quality policy is invalid.") from exc
    category_id = metrics.get("category_id")
    selected = (
        overrides.get(category_id, default) if category_id is not None else default
    )
    if type(selected) is not dict or set(selected) != _QUALITY_FIELDS:
        raise _invalid("Registration quality policy is invalid.")
    return selected


def _rmse(mapping: dict, label: str) -> float:
    try:
        value = mapping["rmse_m"]
    except (KeyError, TypeError) as exc:
        raise ModelMatchingError(
            "registration_engine_failed", f"{label} registration metrics are invalid."
        ) from exc
    if type(value) not in (int, float) or not math.isfinite(float(value)) or value < 0:
        raise ModelMatchingError(
            "registration_engine_failed", f"{label} registration metrics are invalid."
        )
    return float(value)


def _plain(value: float) -> float:
    number = round(float(value), 12)
    return 0.0 if number == 0.0 else number


def evaluate_registration_gate(
    metrics: dict,
    *,
    coarse_metrics: dict,
    fine_metrics: dict,
    pose_score_margin: float | None,
    symmetry_equivalent: bool,
    config: dict,
) -> dict:
    """Return a stable three-state quality decision without transport side effects."""

    if type(metrics) is not dict or type(symmetry_equivalent) is not bool:
        raise _invalid("Registration gate input is invalid.")
    policy = _policy(metrics, config)
    observed_coverage = _number(metrics, "observed_to_model_coverage")
    model_coverage = _number(metrics, "model_to_observed_coverage")
    inlier_rmse = _number(metrics, "inlier_rmse_m")
    chamfer = _number(metrics, "chamfer_distance_m")
    dimension_error = _number(metrics, "maximum_dimension_relative_error")
    coarse_rmse = _rmse(coarse_metrics, "Coarse")
    fine_rmse = _rmse(fine_metrics, "Fine")
    if coarse_rmse == 0.0:
        regression_ratio = 1.0 if fine_rmse == 0.0 else math.inf
    else:
        regression_ratio = fine_rmse / coarse_rmse

    reject_reasons: set[str] = set()
    review_reasons: set[str] = set()
    if observed_coverage < _number(policy, "review_observed_coverage"):
        reject_reasons.add("insufficient_observed_coverage")
    elif observed_coverage < _number(policy, "passed_observed_coverage"):
        review_reasons.add("observed_coverage_buffer")

    if model_coverage < _number(policy, "review_model_coverage"):
        reject_reasons.add("insufficient_model_coverage")
    elif model_coverage < _number(policy, "passed_model_coverage"):
        if observed_coverage >= _number(policy, "passed_observed_coverage"):
            review_reasons.add("partial_observation")
        else:
            review_reasons.add("model_coverage_buffer")

    if inlier_rmse > _number(policy, "maximum_inlier_rmse_m"):
        reject_reasons.add("inlier_rmse_exceeded")
    if chamfer > _number(policy, "maximum_chamfer_m"):
        reject_reasons.add("chamfer_exceeded")
    if dimension_error > _number(policy, "maximum_dimension_relative_error"):
        reject_reasons.add("dimension_mismatch")

    maximum_regression = _number(policy, "maximum_fine_regression_ratio")
    if regression_ratio > maximum_regression:
        reject_reasons.add("fine_registration_regressed")
    elif regression_ratio > 1.0:
        review_reasons.add("fine_registration_regressed")

    if pose_score_margin is not None:
        if type(pose_score_margin) not in (int, float) or not math.isfinite(
            float(pose_score_margin)
        ) or pose_score_margin < 0.0:
            raise ModelMatchingError(
                "registration_engine_failed", "Registration pose margin is invalid."
            )
        if pose_score_margin < _number(policy, "minimum_pose_score_margin"):
            review_reasons.add(
                "equivalent_symmetric_pose"
                if symmetry_equivalent
                else "ambiguous_symmetric_pose"
            )

    if reject_reasons:
        reasons = reject_reasons | review_reasons | {"registration_gate_rejected"}
        status = "rejected"
    elif review_reasons:
        reasons = review_reasons
        status = "review_required"
    else:
        reasons = set()
        status = "passed"
    return {
        "status": status,
        "reasons": sorted(reasons),
        "fine_regression_ratio": (
            _plain(regression_ratio) if math.isfinite(regression_ratio) else None
        ),
    }
