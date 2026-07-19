import pytest

from pc_system.object_segmentation import segment_object_candidates, segment_with_open3d_adapter
from pc_system.segmentation_engines import SegmentationEngineUnavailable, execute_engine


def sample_points():
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.1, "y": 0.0, "z": 0.0},
    ]


def test_open3d_request_fails_when_runner_is_unavailable():
    with pytest.raises(SegmentationEngineUnavailable, match="open3d_dbscan"):
        execute_engine(
            "scan",
            sample_points(),
            {"engine": "open3d_dbscan", "allow_fallback": False},
        )


def test_explicit_fallback_records_actual_engine_and_reason():
    report, execution = execute_engine(
        "scan",
        sample_points(),
        {
            "engine": "open3d_dbscan",
            "allow_fallback": True,
            "distance_threshold": 1.0,
            "min_points": 1,
        },
    )

    assert report["method"] == "builtin_geometric"
    assert execution == {
        "requested_engine": "open3d_dbscan",
        "executed_engine": "builtin_geometric",
        "fallback_reason": "runner_unavailable",
    }


def test_injected_runner_records_requested_engine_as_executed():
    def open3d_runner(asset_id, points, config):
        return segment_object_candidates(
            asset_id,
            points,
            distance_threshold=float(config["distance_threshold"]),
            min_points=int(config["min_points"]),
        )

    report, execution = execute_engine(
        "scan",
        sample_points(),
        {
            "engine": "open3d_dbscan",
            "distance_threshold": 1.0,
            "min_points": 1,
        },
        runners={"open3d_dbscan": open3d_runner},
    )

    assert report["method"] == "open3d_dbscan"
    assert execution["executed_engine"] == "open3d_dbscan"
    assert execution["fallback_reason"] is None


def test_phase10_open3d_adapter_requires_real_runner():
    with pytest.raises(RuntimeError, match="runner"):
        segment_with_open3d_adapter("scan", sample_points(), min_points=1)


def test_builtin_segmentation_exposes_internal_membership_indices():
    report = segment_object_candidates(
        "scan",
        sample_points(),
        distance_threshold=1.0,
        min_points=1,
        include_membership=True,
    )

    assert report["objects"][0]["_point_indices"] == [0, 1]


def test_phase10_default_report_does_not_expose_internal_membership_indices():
    report = segment_object_candidates("scan", sample_points(), distance_threshold=1.0, min_points=1)

    assert "_point_indices" not in report["objects"][0]
