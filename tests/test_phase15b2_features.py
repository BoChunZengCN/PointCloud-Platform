import math

import pytest

from pc_system.model_features import (
    extract_geometric_features,
    feature_vector_fingerprint,
)
from pc_system.model_matching_errors import ModelMatchingError
from phase15b2_support import BOX_POINTS, FEATURE_V1


def _rotate_translate(points):
    z_angle = 0.713
    x_angle = -0.419
    z_cosine, z_sine = math.cos(z_angle), math.sin(z_angle)
    x_cosine, x_sine = math.cos(x_angle), math.sin(x_angle)
    transformed = []
    for point in points:
        x = z_cosine * point["x"] - z_sine * point["y"]
        y = z_sine * point["x"] + z_cosine * point["y"]
        z = point["z"]
        transformed.append(
            {
                "x": x + 2.3,
                "y": x_cosine * y - x_sine * z - 4.7,
                "z": x_sine * y + x_cosine * z + 1.1,
            }
        )
    return transformed


def test_feature_vector_is_invariant_to_order_translation_and_rotation():
    baseline = extract_geometric_features(BOX_POINTS, FEATURE_V1)
    transformed = extract_geometric_features(
        _rotate_translate(list(reversed(BOX_POINTS))), FEATURE_V1
    )

    assert transformed == baseline
    assert baseline["observed_spans_m"] == [2.0, 1.0, 0.5]
    assert baseline["span_ratios"] == [1.0, 0.5, 0.25]
    assert baseline["observed_box_volume_m3"] == 1.0
    assert baseline["point_count"] == 16
    assert baseline["quality"] == {"status": "usable", "reasons": []}


def test_feature_vector_and_fingerprint_are_deterministic():
    first = extract_geometric_features(BOX_POINTS, FEATURE_V1)
    second = extract_geometric_features(list(BOX_POINTS), dict(FEATURE_V1))

    assert second == first
    assert feature_vector_fingerprint(second) == feature_vector_fingerprint(first)
    assert len(feature_vector_fingerprint(first)) == 64
    assert math.isclose(sum(first["principal_value_ratios"]), 1.0)
    assert math.isclose(sum(first["radial_histogram"]), 1.0)
    assert first["voxel_occupancy"] == 0.125


def test_collinear_geometry_is_degraded_without_using_degenerate_axes():
    points = [{"x": float(index), "y": 0.0, "z": 0.0} for index in range(16)]

    features = extract_geometric_features(points, FEATURE_V1)

    assert features["observed_spans_m"] == [15.0, 0.0, 0.0]
    assert features["observed_box_volume_m3"] == 0.0
    assert features["quality"]["status"] == "degraded"
    assert "rank_deficient" in features["quality"]["reasons"]
    assert 0.0 < features["voxel_occupancy"] <= 1.0


def test_coplanar_geometry_and_symmetric_geometry_are_flagged():
    plane = [
        {"x": float(x), "y": float(y), "z": 0.0}
        for x in range(4)
        for y in range(4)
    ]
    symmetric = (
        [
            {"x": 1.0, "y": 0.0, "z": 0.0},
            {"x": -1.0, "y": 0.0, "z": 0.0},
            {"x": 0.0, "y": 1.0, "z": 0.0},
            {"x": 0.0, "y": -1.0, "z": 0.0},
            {"x": 0.0, "y": 0.0, "z": 1.0},
            {"x": 0.0, "y": 0.0, "z": -1.0},
        ]
        * 3
    )

    plane_features = extract_geometric_features(plane, FEATURE_V1)
    symmetric_features = extract_geometric_features(symmetric, FEATURE_V1)

    assert plane_features["quality"]["status"] == "degraded"
    assert "rank_deficient" in plane_features["quality"]["reasons"]
    assert "axis_ambiguous" in plane_features["quality"]["reasons"]
    assert symmetric_features["quality"]["status"] == "degraded"
    assert symmetric_features["quality"]["reasons"] == ["axis_ambiguous"]


def test_zero_span_geometry_is_metadata_only():
    points = [{"x": 1.0, "y": 1.0, "z": 1.0} for _ in range(16)]

    features = extract_geometric_features(points, FEATURE_V1)

    assert features["quality"] == {
        "status": "metadata_only",
        "reasons": ["geometry_degenerate"],
    }
    assert features["observed_spans_m"] == [0.0, 0.0, 0.0]
    assert features["radial_histogram"][0] == 1.0
    assert features["voxel_occupancy"] == 0.0


@pytest.mark.parametrize(
    "points",
    [
        BOX_POINTS[:15],
        [{"x": 0.0, "y": 0.0, "z": float("nan")}] * 16,
        [{"x": 0.0, "y": 0.0}] * 16,
        [[0.0, 0.0, 0.0]] * 16,
        "not-points",
    ],
)
def test_invalid_point_inputs_are_rejected(points):
    with pytest.raises(ModelMatchingError) as error:
        extract_geometric_features(points, FEATURE_V1)

    assert error.value.code == "invalid_retrieval_input"


def test_configured_maximum_point_boundary_is_enforced():
    config = {**FEATURE_V1, "maximum_points": 16}

    assert extract_geometric_features(BOX_POINTS, config)["point_count"] == 16
    with pytest.raises(ModelMatchingError) as error:
        extract_geometric_features(
            [*BOX_POINTS, {"x": 3.0, "y": 0.0, "z": 0.0}], config
        )

    assert error.value.code == "invalid_retrieval_input"


def test_feature_fingerprint_rejects_non_finite_values():
    with pytest.raises(ModelMatchingError) as error:
        feature_vector_fingerprint({"shape": float("inf")})

    assert error.value.code == "feature_integrity_error"
