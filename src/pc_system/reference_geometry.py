"""扫描参考点集的纯几何处理；不发布、不核验，也不改写原始数据。"""

import hashlib
import json
import math

import numpy as np

from pc_system.model_matching_errors import ModelMatchingError


MAXIMUM_POINTS = 1_000_000
_UNIT_SCALE = {"mm": 0.001, "cm": 0.01, "m": 1.0}
_SPACING_SAMPLE_COUNT = 4096


def _error(code: str, message: str) -> ModelMatchingError:
    return ModelMatchingError(code, message)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _rounded(value: float) -> float:
    result = round(value, 12)
    return 0.0 if result == 0 else result


def _finite(value: object) -> float:
    if type(value) not in {int, float}:
        raise _error("reference_points_invalid", "点坐标必须是有限数值。")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise _error("reference_points_invalid", "点坐标超出数值范围。") from exc
    if not math.isfinite(result):
        raise _error("reference_points_invalid", "点坐标必须是有限数值。")
    return result


def _points(value: object, maximum_points: int) -> list[tuple[float, float, float]]:
    if type(value) is not list or not value:
        raise _error("reference_points_invalid", "参考点集必须是非空数组。")
    if len(value) > maximum_points:
        raise _error("reference_input_limit", "参考点数超过 limit 限制。")
    result = []
    for point in value:
        if type(point) not in {list, tuple} or len(point) != 3:
            raise _error("reference_points_invalid", "每个点必须包含三个坐标。")
        result.append(tuple(_finite(coordinate) for coordinate in point))
    return result


def _geometry_fingerprint(points: list[list[float]]) -> str:
    return hashlib.sha256(_canonical_bytes({"coordinate_unit": "m", "points": points})).hexdigest()


def normalize_reference_points(points, *, declared_unit: str, maximum_points: int = MAXIMUM_POINTS) -> dict:
    """转为米制局部坐标并去重；低点数等质量问题交给质量报告表达。"""
    if type(maximum_points) is not int or not 1 <= maximum_points <= MAXIMUM_POINTS:
        raise _error("reference_limit_invalid", "点数上限必须是允许范围内的整数。")
    if type(declared_unit) is not str or declared_unit not in _UNIT_SCALE:
        raise _error("reference_unit_unsupported", "请明确声明 mm、cm 或 m。")
    raw = _points(points, maximum_points)
    scale = _UNIT_SCALE[declared_unit]
    meters = [tuple(coordinate * scale for coordinate in point) for point in raw]
    minima = [min(point[axis] for point in meters) for axis in range(3)]
    maxima = [max(point[axis] for point in meters) for axis in range(3)]
    dimensions = [maxima[axis] - minima[axis] for axis in range(3)]
    if not all(math.isfinite(value) for value in dimensions):
        raise _error("reference_points_invalid", "几何跨度超出数值范围。")
    # 分别减半避免同号大坐标求和溢出；局部轴保持源数据方向。
    origin = [minima[axis] / 2.0 + maxima[axis] / 2.0 for axis in range(3)]
    local = sorted({tuple(_rounded(point[axis] - origin[axis]) for axis in range(3)) for point in meters})
    if not all(math.isfinite(value) for point in local for value in point):
        raise _error("reference_points_invalid", "局部坐标超出数值范围。")
    result_points = [list(point) for point in local]
    matrix = [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]
    for axis in range(3):
        matrix[axis][3] = 0.0 if origin[axis] == 0 else -origin[axis]
    return {
        "algorithm_version": "scan-normalization-v1",
        "coordinate_unit": "m",
        "declared_unit": declared_unit,
        "unit_scale_to_m": scale,
        "source_origin_m": origin,
        "source_m_to_reference_4x4": matrix,
        "source_bounds_m": {"min": minima, "max": maxima},
        "dimensions_m": [_rounded(value) for value in dimensions],
        "input_point_count": len(raw),
        "point_count": len(result_points),
        "duplicate_fraction": (len(raw) - len(result_points)) / len(raw),
        "points": result_points,
        "geometry_fingerprint": _geometry_fingerprint(result_points),
    }


def select_reference_points(points, *, point_count: int, random_seed: int) -> list[list[float]]:
    """按坐标与种子的 SHA-256 排序选取实测点，不进行插值或补点。"""
    if (
        type(point_count) is not int or not 1 <= point_count <= MAXIMUM_POINTS
        or type(random_seed) is not int or not 0 <= random_seed < 2**63
    ):
        raise _error("reference_sampling_invalid", "扫描选点数量或种子无效。")
    unique = sorted(set(_points(points, MAXIMUM_POINTS)))
    if len(unique) <= point_count:
        return [list(point) for point in unique]
    prefix = _canonical_bytes({"algorithm": "sha256_point_subset_v1", "random_seed": random_seed}) + b"\0"
    chosen = sorted(unique, key=lambda point: (hashlib.sha256(prefix + _canonical_bytes(point)).digest(), point))[:point_count]
    return [list(point) for point in sorted(chosen)]


def _spacing_metrics(points: list[list[float]]) -> dict:
    sample = select_reference_points(points, point_count=_SPACING_SAMPLE_COUNT, random_seed=0)
    count = len(sample)
    metrics = {
        "spacing_sample_count": count,
        "spacing_is_estimate": count < len(points),
        "nearest_neighbor_p50_m": None,
        "nearest_neighbor_p95_m": None,
    }
    if count < 2:
        return metrics
    array = np.asarray(sample, dtype=np.float64)
    scale = float(np.max(np.abs(array)))
    if scale == 0:
        return metrics
    array = array / scale
    nearest = np.empty(count)
    # 分块按轴累积，峰值为 128 × 4096，而不是 N × N × 3。
    for offset in range(0, count, 128):
        block = array[offset:offset + 128]
        squared = np.zeros((len(block), count))
        for axis in range(3):
            delta = block[:, axis, None] - array[None, :, axis]
            squared += delta * delta
        squared[np.arange(len(block)), offset + np.arange(len(block))] = np.inf
        nearest[offset:offset + len(block)] = np.sqrt(np.min(squared, axis=1)) * scale
    if not np.all(np.isfinite(nearest)):
        raise _error("reference_points_invalid", "点间距超出数值范围。")
    metrics["nearest_neighbor_p50_m"] = _rounded(float(np.percentile(nearest, 50)))
    metrics["nearest_neighbor_p95_m"] = _rounded(float(np.percentile(nearest, 95)))
    return metrics


def assess_reference_quality(normalized: dict) -> dict:
    """只输出自动检查证据；任何 passed 状态都不代表专家已通过。"""
    if type(normalized) is not dict:
        raise _error("reference_points_invalid", "缺少标准化参考点集。")
    points = [list(point) for point in _points(normalized.get("points"), MAXIMUM_POINTS)]
    input_count = normalized.get("input_point_count")
    if (
        normalized.get("coordinate_unit") != "m"
        or type(input_count) is not int or not len(points) <= input_count <= MAXIMUM_POINTS
        or normalized.get("point_count") != len(points)
        or points != [list(point) for point in sorted(set(map(tuple, points)))]
        or normalized.get("geometry_fingerprint") != _geometry_fingerprint(points)
    ):
        raise _error("reference_points_invalid", "标准化点集的计数或指纹不一致。")
    rejection_codes, warning_codes = [], []
    count = len(points)
    duplicate_fraction = (input_count - count) / input_count
    if count < 64:
        rejection_codes.append("insufficient_unique_points")
    if duplicate_fraction >= 0.2:
        warning_codes.append("high_duplicate_fraction")
    array = np.asarray(points, dtype=np.float64)
    scale = float(np.max(np.abs(array)))
    ratios = [0.0, 0.0, 0.0]
    if scale == 0:
        rejection_codes.append("geometry_zero_span")
    else:
        scaled = array / scale
        centered = scaled - np.mean(scaled, axis=0)
        covariance = centered.T @ centered / count
        eigenvalues = np.maximum(np.linalg.eigvalsh(covariance)[::-1], 0.0)
        if eigenvalues[0] == 0:
            rejection_codes.append("geometry_zero_span")
        else:
            ratios = [float(value / eigenvalues[0]) for value in eigenvalues]
            if ratios[1] <= 1e-8:
                rejection_codes.append("geometry_collinear")
            elif ratios[2] <= 1e-8:
                warning_codes.append("geometry_planar")
    metrics = {
        "input_point_count": input_count,
        "point_count": count,
        "duplicate_fraction": duplicate_fraction,
        "eigenvalue_ratios": [_rounded(value) for value in ratios],
        **_spacing_metrics(points),
    }
    return {
        "schema_version": "1.0",
        "quality_policy_version": "scan-quality-v1",
        "geometry_fingerprint": normalized["geometry_fingerprint"],
        "status": "rejected" if rejection_codes else ("review_required" if warning_codes else "passed"),
        "rejection_codes": rejection_codes,
        "warning_codes": warning_codes,
        "metrics": metrics,
    }
