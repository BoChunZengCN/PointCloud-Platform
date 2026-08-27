import bisect
import hashlib
import json
import math

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_mesh import _validated_geometry


_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "algorithm",
        "point_count",
        "random_seed",
        "coordinate_unit",
        "coordinate_precision_decimals",
    }
)


def _sampling_error(message: str) -> ModelMatchingError:
    return ModelMatchingError("invalid_sampling_config", message)


def build_sampling_config(point_count: int, random_seed: int) -> dict:
    if (
        type(point_count) is not int
        or not 1 <= point_count <= 500_000
        or type(random_seed) is not int
        or not 0 <= random_seed <= 9_223_372_036_854_775_807
    ):
        raise _sampling_error("Sampling point count or random seed is invalid.")
    return {
        "schema_version": "1.0",
        "algorithm": "sha256_area_weighted_v1",
        "point_count": point_count,
        "random_seed": random_seed,
        "coordinate_unit": "m",
        "coordinate_precision_decimals": 12,
    }


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_config(config: object) -> dict:
    if type(config) is not dict or set(config) != _CONFIG_FIELDS:
        raise _sampling_error("Sampling configuration structure is invalid.")
    if (
        type(config.get("schema_version")) is not str
        or type(config.get("algorithm")) is not str
        or type(config.get("point_count")) is not int
        or type(config.get("random_seed")) is not int
        or type(config.get("coordinate_unit")) is not str
        or type(config.get("coordinate_precision_decimals")) is not int
    ):
        raise _sampling_error("Sampling configuration types are invalid.")
    canonical = build_sampling_config(
        config.get("point_count"), config.get("random_seed")
    )
    if config != canonical:
        raise _sampling_error("Sampling configuration values are invalid.")
    return canonical


def sampling_config_fingerprint(config: dict) -> str:
    canonical = _validate_config(config)
    return hashlib.sha256(_canonical_bytes(canonical)).hexdigest()


def _uniform(config_fingerprint: str, sample_index: int, lane: int) -> float:
    payload = (
        b"phase15b1"
        + bytes.fromhex(config_fingerprint)
        + sample_index.to_bytes(8, "big")
        + bytes([lane])
    )
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return min(value / 2**64, math.nextafter(1.0, 0.0))


def _triangle_area(a: list[float], b: list[float], c: list[float]) -> float:
    ab = [b[index] - a[index] for index in range(3)]
    ac = [c[index] - a[index] for index in range(3)]
    cross = [
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    ]
    return math.sqrt(sum(value * value for value in cross)) / 2.0


def _triangles(vertices: list[list[float]], faces: list[list[int]]):
    result = []
    cumulative = 0.0
    for face in faces:
        for index in range(1, len(face) - 1):
            triangle = (face[0], face[index], face[index + 1])
            area = _triangle_area(*(vertices[item] for item in triangle))
            if area == 0.0:
                continue
            if not math.isfinite(area):
                raise ModelMatchingError(
                    "invalid_model_geometry", "Mesh triangle area is not finite."
                )
            cumulative += area
            result.append((triangle, cumulative))
    if not result or not math.isfinite(cumulative) or cumulative <= 0.0:
        raise ModelMatchingError(
            "invalid_model_geometry", "Mesh has no non-degenerate surface area."
        )
    return result, cumulative


def _rounded(value: float) -> float:
    if not math.isfinite(value):
        raise ModelMatchingError(
            "invalid_model_geometry", "Sampled coordinate is not finite."
        )
    rounded = round(value, 12)
    if not math.isfinite(rounded):
        raise ModelMatchingError(
            "invalid_model_geometry", "Sampled coordinate is not finite."
        )
    return 0.0 if rounded == 0.0 else rounded


def sample_mesh_surface(vertices_m, faces, config: dict) -> dict:
    canonical = _validate_config(config)
    vertices, normalized_faces = _validated_geometry(
        {"vertices": vertices_m, "faces": faces}
    )
    triangles, total_area = _triangles(vertices, normalized_faces)
    cumulative = [item[1] for item in triangles]
    fingerprint = sampling_config_fingerprint(canonical)
    points = []
    for sample_index in range(canonical["point_count"]):
        target = _uniform(fingerprint, sample_index, 0) * total_area
        triangle_index = bisect.bisect_right(cumulative, target)
        triangle = triangles[min(triangle_index, len(triangles) - 1)][0]
        a, b, c = (vertices[index] for index in triangle)
        root = math.sqrt(_uniform(fingerprint, sample_index, 1))
        v = _uniform(fingerprint, sample_index, 2)
        weights = (1.0 - root, root * (1.0 - v), root * v)
        try:
            point = [
                _rounded(
                    math.fsum(
                        (
                            weights[0] * a[axis],
                            weights[1] * b[axis],
                            weights[2] * c[axis],
                        )
                    )
                )
                for axis in range(3)
            ]
        except (OverflowError, ValueError) as exc:
            raise ModelMatchingError(
                "invalid_model_geometry", "Sampled coordinate is not finite."
            ) from exc
        points.append(point)
    return {
        "schema_version": "1.0",
        "coordinate_unit": "m",
        "point_count": canonical["point_count"],
        "points": points,
    }
