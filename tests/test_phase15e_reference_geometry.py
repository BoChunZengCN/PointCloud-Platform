"""扫描参考几何的行为测试；预期坐标和风险来自手算夹具。"""

import copy
import importlib
import math

import pytest

from pc_system.model_matching_errors import ModelMatchingError


def geometry():
    """在测试体内确认入口存在，使红灯明确指出缺失功能。"""
    spec = importlib.util.find_spec("pc_system.reference_geometry")
    assert spec is not None, "缺少扫描参考几何实现"
    return importlib.import_module("pc_system.reference_geometry")


def box_points():
    return [[x, y, z] for x in range(4) for y in range(4) for z in range(4)]


@pytest.mark.parametrize("unit,end", [("mm", [1000, 2000, 3000]), ("cm", [100, 200, 300]), ("m", [1, 2, 3])])
def test_normalization_converts_units_once_and_preserves_axes(unit, end):
    value = geometry().normalize_reference_points([[0, 0, 0], end], declared_unit=unit)
    assert value["points"] == [[-0.5, -1.0, -1.5], [0.5, 1.0, 1.5]]
    assert value["source_origin_m"] == [0.5, 1.0, 1.5]
    assert value["dimensions_m"] == [1.0, 2.0, 3.0]
    assert value["source_bounds_m"] == {"min": [0.0, 0.0, 0.0], "max": [1.0, 2.0, 3.0]}
    assert value["source_m_to_reference_4x4"] == [
        [1.0, 0.0, 0.0, -0.5], [0.0, 1.0, 0.0, -1.0],
        [0.0, 0.0, 1.0, -1.5], [0.0, 0.0, 0.0, 1.0],
    ]
    assert value["coordinate_unit"] == "m"


def test_normalization_is_order_independent_deduplicates_without_mutating_input():
    points = box_points() + [[0, 0, 0]] * 16
    original = copy.deepcopy(points)
    normalizer = geometry().normalize_reference_points
    first = normalizer(points, declared_unit="m")
    second = normalizer(list(reversed(points)), declared_unit="m")
    assert first == second
    assert points == original
    assert first["input_point_count"] == 80
    assert first["point_count"] == 64
    assert first["duplicate_fraction"] == 0.2


def test_equivalent_geometry_in_different_units_has_same_fingerprint():
    normalizer = geometry().normalize_reference_points
    meters = normalizer(box_points(), declared_unit="m")
    millimeters = normalizer([[v * 1000 for v in p] for p in box_points()], declared_unit="mm")
    assert meters["points"] == millimeters["points"]
    assert meters["geometry_fingerprint"] == millimeters["geometry_fingerprint"]


def test_large_coordinates_do_not_overflow_midpoint_and_retain_local_offset():
    value = geometry().normalize_reference_points([[1e15, 2e15, 3e15], [1e15 + 2, 2e15 + 4, 3e15 + 6]], declared_unit="m")
    assert value["points"] == [[-1.0, -2.0, -3.0], [1.0, 2.0, 3.0]]
    large = geometry().normalize_reference_points([[1e308, 0, 0], [1.1e308, 1, 1]], declared_unit="m")
    assert all(math.isfinite(v) for p in large["points"] for v in p)


@pytest.mark.parametrize("points", [[], None, [[True, 0, 0]], [["1", 0, 0]], [[0, 0]], [[0, 0, 0, 0]], [[math.nan, 0, 0]], [[math.inf, 0, 0]], [[10**500, 0, 0]]])
def test_invalid_point_data_is_rejected_without_silent_filtering(points):
    with pytest.raises(ModelMatchingError) as caught:
        geometry().normalize_reference_points(points, declared_unit="m")
    assert caught.value.code == "reference_points_invalid"


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5, 1_000_001])
def test_request_cannot_disable_or_raise_point_limit(limit):
    with pytest.raises(ModelMatchingError) as caught:
        geometry().normalize_reference_points([[0, 0, 0]], declared_unit="m", maximum_points=limit)
    assert caught.value.code == "reference_limit_invalid"


def test_count_limit_is_enforced_before_deduplication():
    with pytest.raises(ModelMatchingError) as caught:
        geometry().normalize_reference_points([[0, 0, 0]] * 65, declared_unit="m", maximum_points=64)
    assert caught.value.code == "reference_input_limit"


@pytest.mark.parametrize("unit", ["ft", "M", None, True])
def test_unit_is_explicit_not_guessed(unit):
    with pytest.raises(ModelMatchingError) as caught:
        geometry().normalize_reference_points([[0, 0, 0]], declared_unit=unit)
    assert caught.value.code == "reference_unit_unsupported"


def test_unrepresentable_extent_is_rejected():
    with pytest.raises(ModelMatchingError) as caught:
        geometry().normalize_reference_points([[-1e308, 0, 0], [1e308, 1, 1]], declared_unit="m")
    assert caught.value.code == "reference_points_invalid"


def test_selection_is_a_stable_real_subset_and_does_not_fill_missing_points():
    selector = geometry().select_reference_points
    source = geometry().normalize_reference_points(box_points(), declared_unit="m")["points"]
    subset = selector(source, point_count=16, random_seed=7)
    assert len(subset) == 16
    assert subset == sorted(subset)
    assert all(point in source for point in subset)
    assert selector(list(reversed(source)), point_count=16, random_seed=7) == subset
    assert selector(source, point_count=16, random_seed=8) != subset
    assert selector(source, point_count=128, random_seed=7) == source


@pytest.mark.parametrize("count,seed", [(True, 0), (0, 0), (1_000_001, 0), (1, True), (1, -1), (1, 2**63)])
def test_invalid_selection_configuration_is_rejected(count, seed):
    with pytest.raises(ModelMatchingError) as caught:
        geometry().select_reference_points([[0, 0, 0]], point_count=count, random_seed=seed)
    assert caught.value.code == "reference_sampling_invalid"


def test_volumetric_cloud_passes_automatic_checks_but_has_no_manual_approval():
    module = geometry()
    report = module.assess_reference_quality(module.normalize_reference_points(box_points(), declared_unit="m"))
    assert report["status"] == "passed"
    assert report["rejection_codes"] == []
    assert report["warning_codes"] == []
    assert report["metrics"]["nearest_neighbor_p50_m"] == 1.0
    assert report["metrics"]["nearest_neighbor_p95_m"] == 1.0
    assert report["metrics"]["spacing_sample_count"] == 64
    assert report["metrics"]["spacing_is_estimate"] is False
    assert "approved" not in report


@pytest.mark.parametrize("points,codes", [
    ([[i, 0, 0] for i in range(64)], ["geometry_collinear"]),
    ([[0, 0, 0]] * 64, ["insufficient_unique_points", "geometry_zero_span"]),
    (box_points()[:63], ["insufficient_unique_points"]),
])
def test_unusable_geometry_has_non_overridable_rejection_reasons(points, codes):
    module = geometry()
    report = module.assess_reference_quality(module.normalize_reference_points(points, declared_unit="m"))
    assert report["status"] == "rejected"
    assert report["rejection_codes"] == codes


def test_planar_and_duplicate_risks_are_reported_separately():
    module = geometry()
    points = [[x, y, 0] for x in range(8) for y in range(8)] + [[0, 0, 0]] * 16
    report = module.assess_reference_quality(module.normalize_reference_points(points, declared_unit="m"))
    assert report["status"] == "review_required"
    assert report["rejection_codes"] == []
    assert report["warning_codes"] == ["high_duplicate_fraction", "geometry_planar"]


def test_spacing_estimate_is_bounded_and_independent_of_source_order():
    module = geometry()
    points = [[x, y, z] for x in range(17) for y in range(17) for z in range(17)]
    report = module.assess_reference_quality(module.normalize_reference_points(points, declared_unit="m"))
    assert report["metrics"]["spacing_sample_count"] == 4096
    assert report["metrics"]["spacing_is_estimate"] is True
    assert report == module.assess_reference_quality(module.normalize_reference_points(points[::-1], declared_unit="m"))


def test_rounding_collapsed_geometry_is_not_reported_as_usable():
    module = geometry()
    points = [[x * 1e-16, y * 1e-16, z * 1e-16] for x in range(4) for y in range(4) for z in range(4)]
    report = module.assess_reference_quality(module.normalize_reference_points(points, declared_unit="m"))
    assert report["status"] == "rejected"
    assert "geometry_zero_span" in report["rejection_codes"]
