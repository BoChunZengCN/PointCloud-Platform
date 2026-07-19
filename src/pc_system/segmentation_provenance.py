import hashlib
import json
import math


def fingerprint_points(points: list[dict]) -> str:
    """对有序 XYZ 点序列生成稳定来源指纹。"""

    canonical: list[list[float]] = []
    for point in points:
        if not {"x", "y", "z"}.issubset(point):
            raise ValueError("Point records require x, y, and z.")
        xyz = [float(point[axis]) for axis in ("x", "y", "z")]
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError("Point coordinates must be finite before fingerprinting.")
        canonical.append(xyz)
    payload = json.dumps(canonical, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
