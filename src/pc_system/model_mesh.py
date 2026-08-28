import math
from collections.abc import Mapping
from numbers import Integral, Real
from pathlib import Path
from typing import Any, Callable

from pc_system.model_matching_errors import ModelMatchingError


MeshReader = Callable[[Path], dict[str, Any]]
SUPPORTED_MESH_FORMATS = frozenset({".stl", ".obj", ".ply"})
UNIT_SCALE_TO_METERS = {"mm": 0.001, "cm": 0.01, "m": 1.0}


def trimesh_mesh_reader(path: Path) -> dict[str, Any]:
    """Read a supported mesh through the optional trimesh dependency."""
    try:
        import trimesh
    except ImportError as exc:
        raise ModelMatchingError(
            "mesh_engine_unavailable",
            "Install pc-system[models] to inspect production meshes.",
        ) from exc

    try:
        loaded = trimesh.load_mesh(path, process=False)
        if isinstance(loaded, trimesh.Scene):
            loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
        return {
            "vertices": loaded.vertices.tolist(),
            "faces": loaded.faces.tolist(),
            "is_watertight": bool(loaded.is_watertight),
        }
    except ModelMatchingError:
        raise
    except Exception as exc:
        raise ModelMatchingError("invalid_model_geometry", "Unable to read mesh geometry.") from exc


def _read_mesh_geometry_with_metadata(
    path: Path, declared_unit: str, *, reader: MeshReader
) -> tuple[list[list[float]], list[list[int]], Mapping, str, str, float]:
    if not path.is_file():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_MESH_FORMATS:
        raise ModelMatchingError("invalid_model_format", f"Unsupported model format: {suffix}")

    if type(declared_unit) is not str:
        raise ModelMatchingError("invalid_model_unit", "Unsupported model unit.")
    unit = declared_unit.strip().lower()
    if unit not in UNIT_SCALE_TO_METERS:
        raise ModelMatchingError("invalid_model_unit", f"Unsupported model unit: {unit}")

    try:
        mesh = reader(path)
    except ModelMatchingError:
        raise
    except Exception as exc:
        raise ModelMatchingError("invalid_model_geometry", "Unable to read mesh geometry.") from exc

    try:
        vertices, faces = _validated_geometry(mesh)
        scale = UNIT_SCALE_TO_METERS[unit]
        vertices_m = [
            [coordinate * scale for coordinate in vertex]
            for vertex in vertices
        ]
    except ModelMatchingError:
        raise
    except Exception as exc:
        raise ModelMatchingError("invalid_model_geometry", "Unable to read mesh geometry.") from exc

    return vertices_m, faces, mesh, unit, suffix, scale


def read_mesh_geometry_m(
    path: Path, declared_unit: str, *, reader: MeshReader
) -> tuple[list[list[float]], list[list[int]]]:
    """Read one validated mesh and convert every vertex to meters once."""
    vertices, faces, _mesh, _unit, _suffix, _scale = (
        _read_mesh_geometry_with_metadata(path, declared_unit, reader=reader)
    )
    return vertices, faces


def inspect_mesh(path: Path, declared_unit: str, *, reader: MeshReader) -> dict[str, Any]:
    """Inspect a supported mesh and report its geometry in meters."""
    vertices, faces, mesh, unit, suffix, scale = _read_mesh_geometry_with_metadata(
        path, declared_unit, reader=reader
    )
    try:
        minimum = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
        maximum = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
        is_watertight = mesh.get("is_watertight")
    except Exception as exc:
        raise ModelMatchingError(
            "invalid_model_geometry", "Unable to read mesh geometry."
        ) from exc

    return {
        "schema_version": "1.0",
        "source_format": suffix[1:],
        "declared_unit": unit,
        "coordinate_unit": "m",
        "unit_scale_to_m": scale,
        "vertex_count": len(vertices),
        "face_count": len(faces),
        "bounds_m": {"min": minimum, "max": maximum},
        "is_watertight": is_watertight,
    }


def _validated_geometry(mesh: object) -> tuple[list[list[float]], list[list[int]]]:
    try:
        if not isinstance(mesh, Mapping):
            raise TypeError("mesh must be a mapping")
        vertices = _as_list(mesh.get("vertices"))
        faces = _as_list(mesh.get("faces"))
        if not vertices or not faces:
            raise ValueError("mesh must contain vertices and faces")

        normalized_vertices = [_validated_vertex(vertex) for vertex in vertices]
        normalized_faces = [
            _validated_face(face, vertex_count=len(normalized_vertices)) for face in faces
        ]
    except ModelMatchingError:
        raise
    except Exception as exc:
        raise ModelMatchingError(
            "invalid_model_geometry", "Mesh must contain finite vertices and valid faces."
        ) from exc

    return normalized_vertices, normalized_faces


def _as_list(values: object) -> list[Any]:
    tolist = getattr(values, "tolist", None)
    if callable(tolist):
        values = tolist()
    return list(values)  # type: ignore[arg-type]


def _validated_vertex(vertex: object) -> list[float]:
    values = _as_list(vertex)
    if len(values) != 3:
        raise ValueError("vertex must be three-dimensional")
    if any(type(value) is bool or not isinstance(value, Real) for value in values):
        raise TypeError("vertex coordinate must be a real number")
    normalized = [float(value) for value in values]
    if not all(math.isfinite(value) for value in normalized):
        raise ValueError("vertex coordinates must be finite")
    return normalized


def _validated_face(face: object, *, vertex_count: int) -> list[int]:
    indices = _as_list(face)
    if len(indices) < 3:
        raise ValueError("face must contain at least three indices")
    if any(type(index) is bool or not isinstance(index, Integral) for index in indices):
        raise TypeError("face index must be an integer")
    normalized = [int(index) for index in indices]
    if any(index < 0 or index >= vertex_count for index in normalized):
        raise ValueError("face index outside vertex range")
    return normalized
