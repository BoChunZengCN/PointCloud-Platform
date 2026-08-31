import math

import numpy as np

from pc_system.model_matching_errors import ModelMatchingError


def _non_rigid(message: str) -> ModelMatchingError:
    return ModelMatchingError("non_rigid_transform", message)


def _point_array(value: object, label: str) -> np.ndarray:
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


def _matrix(value: object) -> np.ndarray:
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise _non_rigid("Registration transform is not a finite 4x4 matrix.") from exc
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise _non_rigid("Registration transform is not a finite 4x4 matrix.")
    return matrix


def _plain_matrix(matrix: np.ndarray) -> list[list[float]]:
    result = []
    for row in matrix:
        plain_row = []
        for value in row:
            normalized = round(float(value), 12)
            plain_row.append(0.0 if normalized == 0.0 else normalized)
        result.append(plain_row)
    return result


def validate_rigid_transform(matrix: object, policy: dict) -> dict:
    candidate = _matrix(matrix)
    expected_last_row = np.asarray([0.0, 0.0, 0.0, 1.0])
    homogeneous_error = float(np.max(np.abs(candidate[3] - expected_last_row)))
    if homogeneous_error > float(policy["homogeneous_tolerance"]):
        raise _non_rigid("Registration transform has an invalid homogeneous row.")

    rotation = candidate[:3, :3]
    gram = rotation.T @ rotation
    orthogonality_error = float(np.max(np.abs(gram - np.eye(3))))
    if orthogonality_error > float(policy["orthogonality_tolerance"]):
        raise _non_rigid("Registration transform contains scale or shear.")

    determinant = float(np.linalg.det(rotation))
    if determinant <= 0.0 or abs(determinant - 1.0) > float(
        policy["determinant_tolerance"]
    ):
        raise _non_rigid("Registration transform contains reflection or scaling.")

    singular_values = np.linalg.svd(rotation, compute_uv=False)
    singular_value_error = float(np.max(np.abs(singular_values - 1.0)))
    if singular_value_error > float(policy["singular_value_tolerance"]):
        raise _non_rigid("Registration transform singular values are invalid.")

    translation_m = float(np.linalg.norm(candidate[:3, 3]))
    if translation_m > float(policy["maximum_translation_m"]):
        raise _non_rigid("Registration transform translation exceeds policy.")

    cosine = float(np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0))
    rotation_rad = float(math.acos(cosine))
    if rotation_rad > float(policy["maximum_rotation_rad"]):
        raise _non_rigid("Registration transform rotation exceeds policy.")

    return {
        "matrix": _plain_matrix(candidate),
        "homogeneous_error": homogeneous_error,
        "orthogonality_error": orthogonality_error,
        "determinant": determinant,
        "singular_values": [float(value) for value in singular_values],
        "singular_value_error": singular_value_error,
        "translation_m": translation_m,
        "rotation_rad": rotation_rad,
    }


def _principal_axes(points: np.ndarray) -> np.ndarray:
    centered = points - points.mean(axis=0)
    covariance = centered.T @ centered / float(len(points))
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    axes = vectors[:, order]
    if float(np.linalg.det(axes)) < 0.0:
        axes[:, -1] *= -1.0
    return axes


def _centered_transform(
    rotation: np.ndarray,
    model_center: np.ndarray,
    object_center: np.ndarray,
) -> np.ndarray:
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    transform[:3, 3] = object_center - rotation @ model_center
    return transform


def _rotation_distance(first: np.ndarray, second: np.ndarray) -> float:
    relative = first.T @ second
    cosine = float(np.clip((np.trace(relative) - 1.0) / 2.0, -1.0, 1.0))
    return float(math.acos(cosine))


def _is_duplicate(
    candidate: np.ndarray,
    existing: list[tuple[str, np.ndarray]],
    *,
    rotation_tolerance: float,
    translation_tolerance: float,
) -> bool:
    return any(
        _rotation_distance(candidate[:3, :3], other[:3, :3])
        <= rotation_tolerance
        and float(np.linalg.norm(candidate[:3, 3] - other[:3, 3]))
        <= translation_tolerance
        for _source, other in existing
    )


def _symmetry_rotation(value: object) -> np.ndarray:
    matrix = _matrix(value)
    rotation = matrix[:3, :3]
    if (
        not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-8, rtol=0.0)
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0.0)
        or not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-6)
    ):
        raise ModelMatchingError(
            "registration_input_incomplete", "Declared symmetry is not rigid."
        )
    return rotation


def generate_initial_hypotheses(
    model_points: np.ndarray,
    object_points: np.ndarray,
    symmetry_transforms: list[object],
    config: dict,
) -> list[dict]:
    model = _point_array(model_points, "Model")
    observed = _point_array(object_points, "Object")
    if type(symmetry_transforms) is not list:
        raise ModelMatchingError(
            "registration_input_incomplete", "Declared symmetries are invalid."
        )
    maximum = int(config["maximum_hypotheses"])
    rotation_tolerance = float(config["rotation_dedup_tolerance_rad"])
    translation_tolerance = float(config["translation_dedup_tolerance_m"])
    model_center = model.mean(axis=0)
    object_center = observed.mean(axis=0)
    candidates: list[tuple[str, np.ndarray]] = []

    def add(source: str, rotation: np.ndarray) -> None:
        if len(candidates) >= maximum:
            return
        if (
            rotation.shape != (3, 3)
            or not np.isfinite(rotation).all()
            or float(np.linalg.det(rotation)) <= 0.0
        ):
            return
        candidate = _centered_transform(rotation, model_center, object_center)
        if not _is_duplicate(
            candidate,
            candidates,
            rotation_tolerance=rotation_tolerance,
            translation_tolerance=translation_tolerance,
        ):
            candidates.append((source, candidate))

    if config["include_identity"]:
        add("identity", np.eye(3, dtype=np.float64))

    if config["include_principal_axes"]:
        model_axes = _principal_axes(model)
        object_axes = _principal_axes(observed)
        for signs in (
            np.diag([1.0, 1.0, 1.0]),
            np.diag([1.0, -1.0, -1.0]),
            np.diag([-1.0, 1.0, -1.0]),
            np.diag([-1.0, -1.0, 1.0]),
        ):
            add("principal_axes", object_axes @ signs @ model_axes.T)

    for symmetry in symmetry_transforms:
        add("declared_symmetry", _symmetry_rotation(symmetry))

    if not candidates:
        raise ModelMatchingError(
            "registration_input_incomplete", "No initial registration hypothesis exists."
        )
    return [
        {
            "hypothesis_id": f"hypothesis-{index:03d}",
            "source": source,
            "matrix": _plain_matrix(matrix),
        }
        for index, (source, matrix) in enumerate(candidates, start=1)
    ]
