import math


def _validated_copy(point: dict) -> dict:
    """复制并校验单个点，避免修改调用方输入。"""

    if not {"x", "y", "z"}.issubset(point):
        raise ValueError("Point records require x, y, and z.")
    copied = dict(point)
    for key in ("x", "y", "z"):
        copied[key] = float(copied[key])
        if not math.isfinite(copied[key]):
            raise ValueError("Point coordinates must be finite.")
    return copied


def _deduplicate(points: list[dict]) -> tuple[list[dict], int]:
    """按精确 XYZ 保留首次出现的完整点记录。"""

    seen: set[tuple[float, float, float]] = set()
    result: list[dict] = []
    for point in points:
        key = (point["x"], point["y"], point["z"])
        if key in seen:
            continue
        seen.add(key)
        result.append(point)
    return result, len(points) - len(result)


def _voxel_sample(points: list[dict], voxel_size: float) -> tuple[list[dict], int]:
    """每个体素保留首次出现的完整点记录，保证结果稳定。"""

    if not math.isfinite(voxel_size) or voxel_size <= 0:
        raise ValueError("voxel_size must be a finite number greater than 0.")
    seen: set[tuple[int, int, int]] = set()
    result: list[dict] = []
    for point in points:
        key = (
            math.floor(point["x"] / voxel_size),
            math.floor(point["y"] / voxel_size),
            math.floor(point["z"] / voxel_size),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(point)
    return result, len(points) - len(result)


def preprocess_points(points: list[dict], config: dict) -> tuple[list[dict], dict]:
    """执行默认保守、可审计的分割预处理。"""

    result = [_validated_copy(point) for point in points]
    input_count = len(result)
    duplicate_points_removed = 0
    voxel_points_removed = 0

    if bool(config.get("deduplicate", True)):
        result, duplicate_points_removed = _deduplicate(result)

    voxel_size = config.get("voxel_size")
    if voxel_size is not None:
        result, voxel_points_removed = _voxel_sample(result, float(voxel_size))

    min_retention_ratio = float(config.get("min_retention_ratio", 0.8))
    if not math.isfinite(min_retention_ratio) or not 0 <= min_retention_ratio <= 1:
        raise ValueError("min_retention_ratio must be between 0 and 1.")
    retention_ratio = len(result) / input_count if input_count else 1.0
    findings: list[dict[str, str]] = []
    if retention_ratio < min_retention_ratio:
        findings.append(
            {
                "code": "low_point_retention",
                "severity": "warning",
                "message": "Preprocessing removed enough points to require thin-structure review.",
            }
        )

    return result, {
        "schema_version": "1.0",
        "input_point_count": input_count,
        "output_point_count": len(result),
        "duplicate_points_removed": duplicate_points_removed,
        "voxel_points_removed": voxel_points_removed,
        "retention_ratio": round(retention_ratio, 4),
        "findings": findings,
    }
