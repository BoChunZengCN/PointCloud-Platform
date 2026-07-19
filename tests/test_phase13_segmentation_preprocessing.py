import math

import pytest

from pc_system.segmentation_preprocessing import preprocess_points


def test_preprocessing_rejects_non_finite_coordinates():
    with pytest.raises(ValueError, match="finite"):
        preprocess_points([{"x": math.nan, "y": 0, "z": 0}], {})


def test_preprocessing_deduplicates_without_mutating_input():
    source = [
        {"x": 0, "y": 0, "z": 0},
        {"x": 0, "y": 0, "z": 0},
        {"x": 1, "y": 0, "z": 0},
    ]

    result, report = preprocess_points(source, {"deduplicate": True})

    assert len(source) == 3
    assert len(result) == 2
    assert report["duplicate_points_removed"] == 1
    assert report["retention_ratio"] == pytest.approx(2 / 3, rel=1e-4)


def test_voxel_sampling_is_deterministic_and_preserves_complete_first_record():
    points = [
        {"x": 0.1, "y": 0.1, "z": 0.1, "red": 11},
        {"x": 0.2, "y": 0.2, "z": 0.2, "red": 22},
        {"x": 1.1, "y": 0.1, "z": 0.1, "red": 33},
    ]

    result, report = preprocess_points(points, {"deduplicate": False, "voxel_size": 1.0})

    assert [point["red"] for point in result] == [11, 33]
    assert report["voxel_points_removed"] == 1


@pytest.mark.parametrize("voxel_size", [0, -1, math.inf])
def test_voxel_sampling_rejects_invalid_size(voxel_size):
    with pytest.raises(ValueError, match="voxel_size"):
        preprocess_points([{"x": 0, "y": 0, "z": 0}], {"voxel_size": voxel_size})


def test_low_retention_requires_thin_structure_review():
    points = [
        {"x": 0.1, "y": 0.1, "z": 0.1},
        {"x": 0.2, "y": 0.2, "z": 0.2},
        {"x": 0.3, "y": 0.3, "z": 0.3},
        {"x": 1.1, "y": 0.1, "z": 0.1},
    ]

    _, report = preprocess_points(
        points,
        {"deduplicate": False, "voxel_size": 1.0, "min_retention_ratio": 0.8},
    )

    assert report["retention_ratio"] == 0.5
    assert report["findings"] == [
        {
            "code": "low_point_retention",
            "severity": "warning",
            "message": "Preprocessing removed enough points to require thin-structure review.",
        }
    ]
