import math

import numpy as np

from pc_system.model_matching_errors import ModelMatchingError


def _engine_failed(message: str) -> ModelMatchingError:
    return ModelMatchingError("registration_engine_failed", message)


def _points(value: object, label: str) -> np.ndarray:
    try:
        points = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "registration_input_incomplete", f"{label} points are invalid."
        ) from exc
    if (
        points.ndim != 2
        or points.shape[1:] != (3,)
        or points.shape[0] < 3
        or not np.isfinite(points).all()
    ):
        raise ModelMatchingError(
            "registration_input_incomplete", f"{label} points are invalid."
        )
    return points


def _transform(value: object) -> np.ndarray:
    try:
        transform = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "non_rigid_transform", "Registration transform is invalid."
        ) from exc
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        raise ModelMatchingError(
            "non_rigid_transform", "Registration transform is invalid."
        )
    if not np.allclose(transform[3], [0.0, 0.0, 0.0, 1.0], atol=1e-8):
        raise ModelMatchingError(
            "non_rigid_transform", "Registration transform is invalid."
        )
    return transform


def _distances(evidence: dict, field: str) -> np.ndarray:
    try:
        raw = evidence[field]
        values = np.asarray(raw, dtype=np.float64)
    except (KeyError, TypeError, ValueError) as exc:
        raise _engine_failed(f"Registration engine {field} evidence is invalid.") from exc
    if (
        values.ndim != 1
        or values.size == 0
        or not np.isfinite(values).all()
        or np.any(values < 0.0)
    ):
        raise _engine_failed(f"Registration engine {field} evidence is invalid.")
    return values


def _normal_consistency(evidence: dict, minimum: float) -> float | None:
    raw = evidence.get("normal_cosines")
    if raw is None:
        return None
    try:
        values = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise _engine_failed("Registration engine normal evidence is invalid.") from exc
    if (
        values.ndim != 1
        or values.size == 0
        or not np.isfinite(values).all()
        or np.any(np.abs(values) > 1.0)
    ):
        raise _engine_failed("Registration engine normal evidence is invalid.")
    return _plain(np.mean(np.abs(values) >= minimum))


def _plain(value: object) -> float:
    number = round(float(value), 12)
    return 0.0 if number == 0.0 else number


def _config_number(
    config: dict,
    field: str,
    *,
    maximum: float | None = None,
    allow_zero: bool = False,
) -> float:
    try:
        raw = config[field]
    except (KeyError, TypeError) as exc:
        raise ModelMatchingError(
            "registration_config_invalid", f"Registration metric {field} is invalid."
        ) from exc
    if type(raw) not in (int, float):
        raise ModelMatchingError(
            "registration_config_invalid", f"Registration metric {field} is invalid."
        )
    value = float(raw)
    if (
        not math.isfinite(value)
        or value < 0.0
        or (value == 0.0 and not allow_zero)
        or (maximum is not None and value > maximum)
    ):
        raise ModelMatchingError(
            "registration_config_invalid", f"Registration metric {field} is invalid."
        )
    return value


def compute_registration_metrics(
    model_points: np.ndarray,
    object_points: np.ndarray,
    transform: np.ndarray,
    evidence: dict,
    config: dict,
) -> dict:
    """Compute auditable bidirectional metrics for a model-to-object transform."""

    model = _points(model_points, "Model")
    observed = _points(object_points, "Object")
    matrix = _transform(transform)
    if type(evidence) is not dict:
        raise _engine_failed("Registration engine evidence is invalid.")

    inlier_distance = _config_number(config, "inlier_distance_m")
    normal_minimum = _config_number(
        config, "normal_consistency_minimum", maximum=1.0, allow_zero=True
    )
    observed_distances = _distances(evidence, "observed_to_model_distances_m")
    model_distances = _distances(evidence, "model_to_observed_distances_m")
    observed_inliers = observed_distances <= inlier_distance
    model_inliers = model_distances <= inlier_distance
    combined = np.concatenate([observed_distances, model_distances])
    combined_inliers = combined[combined <= inlier_distance]
    rmse_values = combined_inliers if combined_inliers.size else combined

    homogeneous_model = np.column_stack([model, np.ones(model.shape[0])])
    transformed_model = (matrix @ homogeneous_model.T).T[:, :3]
    model_dimensions = np.ptp(transformed_model, axis=0)
    observed_dimensions = np.ptp(observed, axis=0)
    denominator = np.maximum.reduce(
        [model_dimensions, observed_dimensions, np.full(3, np.finfo(float).eps)]
    )
    dimension_errors = np.abs(model_dimensions - observed_dimensions) / denominator

    return {
        "observed_to_model_coverage": _plain(np.mean(observed_inliers)),
        "model_to_observed_coverage": _plain(np.mean(model_inliers)),
        "observed_to_model_inlier_count": int(np.count_nonzero(observed_inliers)),
        "model_to_observed_inlier_count": int(np.count_nonzero(model_inliers)),
        "observed_to_model_sample_count": int(observed_distances.size),
        "model_to_observed_sample_count": int(model_distances.size),
        "inlier_rmse_m": _plain(np.sqrt(np.mean(np.square(rmse_values)))),
        "chamfer_distance_m": _plain(
            (np.mean(observed_distances) + np.mean(model_distances)) / 2.0
        ),
        "p50_distance_m": _plain(np.quantile(combined, 0.50)),
        "p95_distance_m": _plain(np.quantile(combined, 0.95)),
        "normal_consistency": _normal_consistency(evidence, normal_minimum),
        "dimension_relative_errors": [_plain(value) for value in dimension_errors],
        "maximum_dimension_relative_error": _plain(np.max(dimension_errors)),
    }
