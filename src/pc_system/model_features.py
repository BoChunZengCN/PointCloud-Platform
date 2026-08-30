import hashlib
import json
import math

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_retrieval_config import _normalize_feature


_POINT_FIELDS = {"x", "y", "z"}
_AXIS_PAIRS = ((0, 1), (0, 2), (1, 2))
_ROUND_DIGITS = 12


def _invalid_input(message: str) -> ModelMatchingError:
    return ModelMatchingError("invalid_retrieval_input", message)


def _round(value: float) -> float:
    result = round(float(value), _ROUND_DIGITS)
    return 0.0 if result == 0 else result


def _distribution(values: list[float]) -> list[float]:
    total = math.fsum(values)
    if total <= 0:
        return [1.0, *([0.0] * (len(values) - 1))]
    normalized = [value / total for value in values]
    rounded = [_round(value) for value in normalized[:-1]]
    rounded.append(_round(1.0 - math.fsum(rounded)))
    return rounded


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "feature_integrity_error", "Feature vector is not canonical JSON."
        ) from exc


def feature_vector_fingerprint(features: dict) -> str:
    if type(features) is not dict:
        raise ModelMatchingError(
            "feature_integrity_error", "Feature vector must be an object."
        )
    return hashlib.sha256(_canonical_bytes(features)).hexdigest()


def _normalize_points(points: object, config: dict) -> list[tuple[float, float, float]]:
    if type(points) is not list:
        raise _invalid_input("Point input must be a JSON array.")
    if not config["minimum_points"] <= len(points) <= config["maximum_points"]:
        raise _invalid_input("Point count is outside configured bounds.")
    normalized: list[tuple[float, float, float]] = []
    for point in points:
        if type(point) is not dict or set(point) != _POINT_FIELDS:
            raise _invalid_input("Each point must contain exactly x, y and z.")
        coordinates: list[float] = []
        for axis in ("x", "y", "z"):
            value = point[axis]
            if type(value) not in {int, float}:
                raise _invalid_input("Point coordinates must be finite numbers.")
            coordinate = float(value)
            if not math.isfinite(coordinate):
                raise _invalid_input("Point coordinates must be finite numbers.")
            coordinates.append(coordinate)
        normalized.append((coordinates[0], coordinates[1], coordinates[2]))
    return sorted(normalized)


def _center(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    count = len(points)
    centroid = tuple(math.fsum(point[axis] for point in points) / count for axis in range(3))
    return [
        (point[0] - centroid[0], point[1] - centroid[1], point[2] - centroid[2])
        for point in points
    ]


def _covariance(points: list[tuple[float, float, float]]) -> list[list[float]]:
    count = len(points)
    return [
        [
            math.fsum(point[row] * point[column] for point in points) / count
            for column in range(3)
        ]
        for row in range(3)
    ]


def _canonical_axis(axis: list[float]) -> list[float]:
    pivot = max(range(3), key=lambda index: (abs(axis[index]), -index))
    if axis[pivot] < 0:
        axis = [-value for value in axis]
    return [0.0 if abs(value) <= 1e-15 else value for value in axis]


def _eigendecomposition_symmetric_3x3(
    matrix: list[list[float]],
) -> tuple[list[float], list[list[float]]]:
    working = [row[:] for row in matrix]
    vectors = [[1.0 if row == column else 0.0 for column in range(3)] for row in range(3)]
    for _ in range(64):
        p, q = max(_AXIS_PAIRS, key=lambda pair: abs(working[pair[0]][pair[1]]))
        scale = max(1.0, *(abs(working[index][index]) for index in range(3)))
        if abs(working[p][q]) <= 1e-15 * scale:
            break
        app = working[p][p]
        aqq = working[q][q]
        apq = working[p][q]
        tau = (aqq - app) / (2.0 * apq)
        tangent = math.copysign(
            1.0 / (abs(tau) + math.sqrt(1.0 + tau * tau)), tau
        )
        cosine = 1.0 / math.sqrt(1.0 + tangent * tangent)
        sine = tangent * cosine
        for index in range(3):
            if index in {p, q}:
                continue
            aip = working[index][p]
            aiq = working[index][q]
            working[index][p] = working[p][index] = cosine * aip - sine * aiq
            working[index][q] = working[q][index] = sine * aip + cosine * aiq
        working[p][p] = (
            cosine * cosine * app
            - 2.0 * sine * cosine * apq
            + sine * sine * aqq
        )
        working[q][q] = (
            sine * sine * app
            + 2.0 * sine * cosine * apq
            + cosine * cosine * aqq
        )
        working[p][q] = working[q][p] = 0.0
        for row in range(3):
            vip = vectors[row][p]
            viq = vectors[row][q]
            vectors[row][p] = cosine * vip - sine * viq
            vectors[row][q] = sine * vip + cosine * viq
    pairs = [
        (
            max(working[index][index], 0.0),
            _canonical_axis([vectors[row][index] for row in range(3)]),
        )
        for index in range(3)
    ]
    pairs.sort(key=lambda pair: (-pair[0], tuple(-abs(value) for value in pair[1]), tuple(pair[1])))
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _axis_spans(
    points: list[tuple[float, float, float]], axes: list[list[float]]
) -> list[float]:
    spans = []
    for axis in axes:
        projections = [math.fsum(point[i] * axis[i] for i in range(3)) for point in points]
        spans.append(max(projections) - min(projections))
    return spans


def _radial_histogram(
    points: list[tuple[float, float, float]], bins: int
) -> list[float]:
    squared = [math.fsum(value * value for value in point) for point in points]
    rms = math.sqrt(math.fsum(squared) / len(squared))
    counts = [0.0] * bins
    if rms <= 1e-15:
        counts[0] = float(len(points))
    else:
        for value in squared:
            normalized = math.sqrt(value) / rms
            index = min(int(normalized * bins / 2.0), bins - 1)
            counts[index] += 1.0
    return _distribution(counts)


def _voxel_occupancy(
    points: list[tuple[float, float, float]],
    axes: list[list[float]],
    spans: list[float],
    grid_size: int,
    active_axes: list[int],
) -> float:
    if not active_axes:
        return 0.0
    projected = [
        [math.fsum(point[i] * axis[i] for i in range(3)) for axis in axes]
        for point in points
    ]
    minima = [min(point[axis] for point in projected) for axis in range(3)]
    occupied: set[tuple[int, ...]] = set()
    for point in projected:
        cell = []
        for axis in active_axes:
            relative = (point[axis] - minima[axis]) / spans[axis]
            cell.append(min(int(relative * grid_size), grid_size - 1))
        occupied.add(tuple(cell))
    return _round(len(occupied) / (grid_size ** len(active_axes)))


def extract_geometric_features(points: object, feature_config: dict) -> dict:
    config = _normalize_feature(feature_config)
    normalized = _normalize_points(points, config)
    centered = _center(normalized)
    eigenvalues, axes = _eigendecomposition_symmetric_3x3(_covariance(centered))
    total = math.fsum(eigenvalues)
    threshold = config["degenerate_eigenvalue_ratio"]
    if total <= threshold:
        eigen_ratios = [1.0, 0.0, 0.0]
        spans = [0.0, 0.0, 0.0]
        reasons = ["geometry_degenerate"]
        status = "metadata_only"
        active_axes: list[int] = []
    else:
        eigen_ratios = _distribution(eigenvalues)
        active_axes = [index for index, value in enumerate(eigen_ratios) if value > threshold]
        spans = _axis_spans(centered, axes)
        spans = [span if index in active_axes else 0.0 for index, span in enumerate(spans)]
        reasons = []
        if len(active_axes) < 3:
            reasons.append("rank_deficient")
        gap = config["ambiguous_axis_relative_gap"]
        if any(
            abs(eigen_ratios[index] - eigen_ratios[index + 1])
            <= gap * max(eigen_ratios[index], threshold)
            for index in range(2)
        ):
            reasons.append("axis_ambiguous")
        status = "degraded" if reasons else "usable"
    rounded_spans = sorted((_round(value) for value in spans), reverse=True)
    maximum_span = rounded_spans[0]
    span_ratios = (
        [_round(value / maximum_span) for value in rounded_spans]
        if maximum_span > 0
        else [0.0, 0.0, 0.0]
    )
    return {
        "observed_spans_m": rounded_spans,
        "span_ratios": span_ratios,
        "observed_box_volume_m3": _round(math.prod(rounded_spans)),
        "principal_value_ratios": eigen_ratios,
        "radial_histogram": _radial_histogram(centered, config["radial_bins"]),
        "voxel_occupancy": _voxel_occupancy(
            centered,
            axes,
            spans,
            config["voxel_grid_size"],
            active_axes,
        ),
        "point_count": len(normalized),
        "quality": {"status": status, "reasons": reasons},
    }
