from pathlib import Path

import numpy as np
import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_mesh import inspect_mesh, trimesh_mesh_reader


FIXTURES = Path(__file__).parent / "fixtures" / "models"


@pytest.mark.parametrize("name", ["minimal.obj", "minimal.stl", "minimal.ply"])
def test_supported_meshes_are_inspected_in_meters(name):
    result = inspect_mesh(FIXTURES / name, "mm", reader=trimesh_mesh_reader)

    assert result["coordinate_unit"] == "m"
    assert result["vertex_count"] == 3
    assert result["face_count"] == 1
    assert result["bounds_m"]["max"] == [1.0, 1.0, 0.0]


def test_unknown_unit_is_rejected():
    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(FIXTURES / "minimal.obj", "inch", reader=trimesh_mesh_reader)

    assert exc_info.value.code == "invalid_model_unit"


def test_non_string_unit_is_rejected_without_coercion():
    class UnitWithBrokenStringConversion:
        def __str__(self):
            raise AssertionError("declared units must not be coerced")

    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(
            FIXTURES / "minimal.obj",
            UnitWithBrokenStringConversion(),
            reader=trimesh_mesh_reader,
        )

    assert exc_info.value.code == "invalid_model_unit"


def test_unknown_format_is_rejected(tmp_path):
    path = tmp_path / "model.step"
    path.write_text("not a supported mesh", encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=trimesh_mesh_reader)

    assert exc_info.value.code == "invalid_model_format"


def test_empty_geometry_is_rejected(tmp_path):
    path = tmp_path / "empty.obj"
    path.write_text("# empty", encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=lambda _path: {"vertices": [], "faces": []})

    assert exc_info.value.code == "invalid_model_geometry"


@pytest.mark.parametrize(
    "mesh",
    [
        {"vertices": [[0, 0, 0], [1, 0, 0], [0, float("nan"), 0]], "faces": [[0, 1, 2]]},
        {"vertices": [[0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]]},
        {"vertices": [[0, 0, 0], ["1", 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]]},
        {"vertices": [[0, 0, 0], [True, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]]},
        {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, True]]},
        {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2.0]]},
        {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, -1]]},
        {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 3]]},
    ],
)
def test_malformed_geometry_is_rejected(tmp_path, mesh):
    path = tmp_path / "invalid.obj"
    path.write_text("# invalid", encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=lambda _path: mesh)

    assert exc_info.value.code == "invalid_model_geometry"


def test_reader_parse_error_is_mapped_to_invalid_geometry(tmp_path):
    path = tmp_path / "malformed.obj"
    path.write_text("# malformed", encoding="utf-8")

    def failing_reader(_path):
        raise ValueError("unable to parse mesh")

    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=failing_reader)

    assert exc_info.value.code == "invalid_model_geometry"


def test_model_matching_reader_error_is_preserved(tmp_path):
    path = tmp_path / "unavailable.obj"
    path.write_text("# unavailable", encoding="utf-8")

    def unavailable_reader(_path):
        raise ModelMatchingError("mesh_engine_unavailable", "Install mesh engine")

    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=unavailable_reader)

    assert exc_info.value.code == "mesh_engine_unavailable"


def test_runtime_error_while_reading_watertightness_is_mapped_to_invalid_geometry(tmp_path):
    path = tmp_path / "invalid.obj"
    path.write_text("# invalid", encoding="utf-8")

    class MeshWithBrokenWatertightness(dict):
        def get(self, key, default=None):
            if key == "is_watertight":
                raise RuntimeError("unavailable mesh property")
            return super().get(key, default)

    mesh = MeshWithBrokenWatertightness(
        vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        faces=[[0, 1, 2]],
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=lambda _path: mesh)

    assert exc_info.value.code == "invalid_model_geometry"


def test_float_only_coordinate_is_rejected(tmp_path):
    path = tmp_path / "invalid.obj"
    path.write_text("# invalid", encoding="utf-8")

    class FloatOnlyCoordinate:
        def __float__(self):
            return 1.0

    mesh = {
        "vertices": [[0, 0, 0], [FloatOnlyCoordinate(), 0, 0], [0, 1, 0]],
        "faces": [[0, 1, 2]],
    }
    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=lambda _path: mesh)

    assert exc_info.value.code == "invalid_model_geometry"


def test_vertex_normalization_runtime_error_is_mapped_to_invalid_geometry(tmp_path):
    path = tmp_path / "invalid.obj"
    path.write_text("# invalid", encoding="utf-8")

    class VerticesWithBrokenConversion:
        def tolist(self):
            raise RuntimeError("conversion failed")

    mesh = {"vertices": VerticesWithBrokenConversion(), "faces": [[0, 1, 2]]}
    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=lambda _path: mesh)

    assert exc_info.value.code == "invalid_model_geometry"


def test_base_exception_from_vertex_normalization_is_not_caught(tmp_path):
    path = tmp_path / "invalid.obj"
    path.write_text("# invalid", encoding="utf-8")

    class StopMeshInspection(BaseException):
        pass

    class VerticesThatStopInspection:
        def tolist(self):
            raise StopMeshInspection("stop")

    mesh = {"vertices": VerticesThatStopInspection(), "faces": [[0, 1, 2]]}
    with pytest.raises(StopMeshInspection):
        inspect_mesh(path, "m", reader=lambda _path: mesh)


@pytest.mark.parametrize("scalar_type", [np.float32, np.float64, np.int64])
def test_numpy_real_coordinates_are_accepted(tmp_path, scalar_type):
    path = tmp_path / "valid.obj"
    path.write_text("# valid", encoding="utf-8")
    mesh = {
        "vertices": [
            [scalar_type(0), scalar_type(0), scalar_type(0)],
            [scalar_type(1), scalar_type(0), scalar_type(0)],
            [scalar_type(0), scalar_type(1), scalar_type(0)],
        ],
        "faces": [[0, 1, 2]],
    }

    result = inspect_mesh(path, "m", reader=lambda _path: mesh)

    assert result["bounds_m"]["max"] == [1.0, 1.0, 0.0]
