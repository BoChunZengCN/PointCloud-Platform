import pytest

from pc_system.segmentation_correspondence import (
    CorrespondenceError,
    match_point_labels,
)
from pc_system.segmentation_provenance import fingerprint_points


def points():
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 1.0, "y": 0.0, "z": 0.0},
    ]


def labels():
    return [
        {
            "point_index": 0,
            "x": 0.01,
            "y": 0.0,
            "z": 0.0,
            "instance_id": "obj-a",
            "class_id": "pipe",
            "is_noise": False,
        },
        {
            "point_index": 1,
            "x": 1.01,
            "y": 0.0,
            "z": 0.0,
            "instance_id": "obj-b",
            "class_id": "valve",
            "is_noise": False,
        },
    ]


def test_strict_mode_matches_indices_when_fingerprint_matches():
    matched, report = match_point_labels(
        labels(),
        points(),
        expected_fingerprint=fingerprint_points(points()),
    )

    assert [item["source_point_index"] for item in matched] == [0, 1]
    assert report["mode"] == "strict_index"
    assert report["matched_ratio"] == 1.0
    assert report["unmatched_count"] == 0
    assert report["ambiguous_count"] == 0


def test_strict_mode_rejects_fingerprint_mismatch():
    with pytest.raises(CorrespondenceError) as exc_info:
        match_point_labels(
            labels(),
            points(),
            expected_fingerprint="wrong",
        )

    assert exc_info.value.code == "source_fingerprint_mismatch"


def test_strict_mode_rejects_out_of_range_index():
    invalid = labels()
    invalid[1]["point_index"] = 3

    with pytest.raises(CorrespondenceError) as exc_info:
        match_point_labels(
            invalid,
            points(),
            expected_fingerprint=fingerprint_points(points()),
        )

    assert exc_info.value.code == "point_index_out_of_range"


def test_coordinate_mode_matches_unique_points_within_tolerance():
    matched, report = match_point_labels(
        labels(),
        points(),
        expected_fingerprint="different-source-is-allowed",
        mode="coordinate_tolerance",
        tolerance=0.05,
    )

    assert [item["source_point_index"] for item in matched] == [0, 1]
    assert report["mode"] == "coordinate_tolerance"
    assert report["matched_count"] == 2
    assert report["tolerance"] == 0.05


def test_coordinate_mode_reports_unmatched_and_ambiguous_points():
    source = [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.01, "y": 0.0, "z": 0.0},
    ]
    candidate_labels = [
        {
            "point_index": 7,
            "x": 0.005,
            "y": 0.0,
            "z": 0.0,
            "instance_id": "obj-a",
            "class_id": "pipe",
            "is_noise": False,
        },
        {
            "point_index": 8,
            "x": 10.0,
            "y": 0.0,
            "z": 0.0,
            "instance_id": "obj-b",
            "class_id": "pipe",
            "is_noise": False,
        },
    ]

    matched, report = match_point_labels(
        candidate_labels,
        source,
        expected_fingerprint="ignored",
        mode="coordinate_tolerance",
        tolerance=0.01,
        min_coverage=0.0,
    )

    assert matched == []
    assert report["matched_count"] == 0
    assert report["ambiguous_count"] == 1
    assert report["unmatched_count"] == 1


def test_coordinate_mode_rejects_two_labels_competing_for_one_source_point():
    competing_labels = [
        {
            "point_index": index,
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "instance_id": f"obj-{index}",
            "class_id": "pipe",
            "is_noise": False,
        }
        for index in range(2)
    ]

    matched, report = match_point_labels(
        competing_labels,
        [{"x": 0.0, "y": 0.0, "z": 0.0}],
        expected_fingerprint="ignored",
        mode="coordinate_tolerance",
        tolerance=0.01,
        min_coverage=0.0,
    )

    assert matched == []
    assert report["matched_count"] == 0
    assert report["ambiguous_count"] == 2
    assert report["unmatched_count"] == 0


def test_coordinate_mode_fails_below_minimum_coverage():
    with pytest.raises(CorrespondenceError) as exc_info:
        match_point_labels(
            labels(),
            [{"x": 100.0, "y": 0.0, "z": 0.0}],
            expected_fingerprint="ignored",
            mode="coordinate_tolerance",
            tolerance=0.01,
            min_coverage=0.5,
        )

    assert exc_info.value.code == "insufficient_match_coverage"
    assert exc_info.value.report["matched_ratio"] == 0.0


@pytest.mark.parametrize("tolerance", [None, 0.0, -1.0, float("inf")])
def test_coordinate_mode_rejects_invalid_tolerance(tolerance):
    with pytest.raises(CorrespondenceError) as exc_info:
        match_point_labels(
            labels(),
            points(),
            expected_fingerprint="ignored",
            mode="coordinate_tolerance",
            tolerance=tolerance,
        )

    assert exc_info.value.code == "invalid_tolerance"


def test_coordinate_mode_is_deterministic():
    first = match_point_labels(
        labels(),
        points(),
        expected_fingerprint="ignored",
        mode="coordinate_tolerance",
        tolerance=0.05,
    )
    second = match_point_labels(
        labels(),
        points(),
        expected_fingerprint="ignored",
        mode="coordinate_tolerance",
        tolerance=0.05,
    )

    assert first == second
