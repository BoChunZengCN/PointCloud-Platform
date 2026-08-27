from pathlib import Path

import pytest

import pc_system.model_sampling as sampling_module
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_mesh import read_mesh_geometry_m
from pc_system.model_sampling import (
    build_sampling_config,
    sample_mesh_surface,
    sampling_config_fingerprint,
)


def test_same_mesh_and_config_produce_literal_points():
    config = build_sampling_config(point_count=3, random_seed=7)

    assert sampling_config_fingerprint(config) == (
        "eaa98cd4674118a8cdca4215d9a4296ce1ec003ef15fa55a0a922a7550f97961"
    )
    assert sample_mesh_surface(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        [[0, 1, 2]],
        config,
    ) == {
        "schema_version": "1.0",
        "coordinate_unit": "m",
        "point_count": 3,
        "points": [
            [0.081298607113, 0.640763524138, 0.0],
            [0.549106701442, 0.023405678479, 0.0],
            [0.125583899168, 0.188869152696, 0.0],
        ],
    }


@pytest.mark.parametrize(
    "point_count,random_seed",
    [(True, 0), (1, False), (0, 0), (500001, 0), (1, -1), (1, 2**63)],
)
def test_sampling_config_rejects_invalid_exact_types_and_bounds(
    point_count, random_seed
):
    with pytest.raises(ModelMatchingError) as exc_info:
        build_sampling_config(point_count=point_count, random_seed=random_seed)
    assert exc_info.value.code == "invalid_sampling_config"


def test_fan_triangulation_ignores_degenerate_triangle_and_normalizes_zero():
    result = sample_mesh_surface(
        [
            [0.0, 0.0, -0.0],
            [1.0, 0.0, -0.0],
            [0.0, 1.0, -0.0],
            [0.0, 1.0, -0.0],
        ],
        [[0, 1, 2, 3]],
        build_sampling_config(point_count=2, random_seed=1),
    )
    assert len(result["points"]) == 2
    assert all(point[2] == 0.0 for point in result["points"])
    assert all(0.0 <= point[0] <= 1.0 for point in result["points"])
    assert all(0.0 <= point[1] <= 1.0 for point in result["points"])


def test_all_degenerate_triangles_fail_closed():
    with pytest.raises(ModelMatchingError) as exc_info:
        sample_mesh_surface(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
            [[0, 1, 2]],
            build_sampling_config(point_count=1, random_seed=0),
        )
    assert exc_info.value.code == "invalid_model_geometry"


def test_read_mesh_geometry_converts_once_to_meters(tmp_path):
    path = tmp_path / "mesh.obj"
    path.write_text("placeholder", encoding="utf-8")

    vertices, faces = read_mesh_geometry_m(
        path,
        "mm",
        reader=lambda _path: {
            "vertices": [[0, 0, 0], [1000, 0, 0], [0, 500, 0]],
            "faces": [[0, 1, 2]],
        },
    )

    assert vertices == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.5, 0.0]]
    assert faces == [[0, 1, 2]]


def test_sampling_rejects_non_finite_vertices():
    with pytest.raises(ModelMatchingError) as exc_info:
        sample_mesh_surface(
            [[0.0, 0.0, 0.0], [float("inf"), 0.0, 0.0], [0.0, 1.0, 0.0]],
            [[0, 1, 2]],
            build_sampling_config(point_count=1, random_seed=0),
        )
    assert exc_info.value.code == "invalid_model_geometry"


def test_uniform_uint64_max_remains_strictly_below_one(monkeypatch):
    class MaximumDigest:
        def digest(self):
            return b"\xff" * 32

    monkeypatch.setattr(
        sampling_module.hashlib, "sha256", lambda _payload: MaximumDigest()
    )
    assert 0.0 <= sampling_module._uniform("00" * 32, 0, 0) < 1.0


def test_config_fixed_fields_require_exact_types():
    config = build_sampling_config(point_count=1, random_seed=0)
    config["coordinate_precision_decimals"] = 12.0
    with pytest.raises(ModelMatchingError) as exc_info:
        sampling_config_fingerprint(config)
    assert exc_info.value.code == "invalid_sampling_config"


def test_area_weighting_and_source_order_select_both_triangles(monkeypatch):
    def fixed_uniform(_fingerprint, sample_index, lane):
        if lane == 0:
            return (0.1, 0.9)[sample_index]
        return 0.25 if lane == 1 else 0.5

    monkeypatch.setattr(sampling_module, "_uniform", fixed_uniform)
    vertices = [
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0, 0, 10], [3, 0, 10], [0, 1, 10],
    ]
    config = build_sampling_config(point_count=2, random_seed=0)
    forward = sample_mesh_surface(vertices, [[0, 1, 2], [3, 4, 5]], config)
    reverse = sample_mesh_surface(vertices, [[3, 4, 5], [0, 1, 2]], config)
    assert [point[2] for point in forward["points"]] == [0.0, 10.0]
    assert [point[2] for point in reverse["points"]] == [10.0, 0.0]


@pytest.mark.parametrize("unit,value,expected", [("cm", 100, 1.0), ("m", 1, 1.0)])
def test_read_mesh_geometry_supports_cm_and_m(tmp_path, unit, value, expected):
    path = tmp_path / "mesh.obj"
    path.write_text("placeholder", encoding="utf-8")
    vertices, _faces = read_mesh_geometry_m(
        path,
        unit,
        reader=lambda _path: {
            "vertices": [[0, 0, 0], [value, 0, 0], [0, value, 0]],
            "faces": [[0, 1, 2]],
        },
    )
    assert vertices[1][0] == expected


def test_sampling_config_accepts_maximum_boundaries():
    assert build_sampling_config(500_000, 2**63 - 1)["point_count"] == 500_000


def test_large_finite_convex_coordinates_remain_finite():
    result = sample_mesh_surface(
        [[1e308, 0, 0], [1e308, 1, 0], [1e308, 0, 1]],
        [[0, 1, 2]],
        build_sampling_config(1, 0),
    )
    assert all(__import__("math").isfinite(value) for value in result["points"][0])
