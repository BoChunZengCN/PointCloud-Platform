import json

import pytest

from pc_system.segmentation_service import run_segmentation


def sample_points():
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0, "red": 10},
        {"x": 0.1, "y": 0.0, "z": 0.0, "red": 20},
    ]


def test_completed_run_writes_memberships_and_latest_projection(tmp_path):
    source = sample_points()

    run = run_segmentation(
        tmp_path,
        asset_id="scan",
        asset_version="v1",
        source_uri="scan.points.json",
        points=source,
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 0.2,
            "min_points": 1,
        },
        run_id="seg-run-001",
    )

    run_dir = tmp_path / "reports" / "segmentation_runs" / "scan" / "seg-run-001"
    object_report = json.loads((run_dir / "object_segments.json").read_text(encoding="utf-8"))
    membership_path = run_dir / object_report["objects"][0]["point_membership_artifact"]

    assert source == sample_points()
    assert run["status"] == "completed"
    assert membership_path.exists()
    assert json.loads(membership_path.read_text(encoding="utf-8"))["points"] == source
    assert "_point_indices" not in object_report["objects"][0]
    assert (
        tmp_path / "reports" / "object_segments" / "scan" / "object_segments.json"
    ).exists()


def test_failed_run_is_persisted_without_replacing_latest_success(tmp_path):
    run_segmentation(
        tmp_path,
        asset_id="scan",
        asset_version="v1",
        source_uri="scan.points.json",
        points=sample_points(),
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 0.2,
            "min_points": 1,
        },
        run_id="seg-run-001",
    )
    latest_path = tmp_path / "reports" / "object_segments" / "scan" / "object_segments.json"
    latest_before = latest_path.read_text(encoding="utf-8")

    def broken_runner(asset_id, points, config):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_segmentation(
            tmp_path,
            asset_id="scan",
            asset_version="v1",
            source_uri="scan.points.json",
            points=sample_points(),
            config={"engine": "broken"},
            run_id="seg-run-002",
            runners={"broken": broken_runner},
        )

    failed_path = (
        tmp_path
        / "reports"
        / "segmentation_runs"
        / "scan"
        / "seg-run-002"
        / "segmentation_run.json"
    )
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["error"] == {"code": "segmentation_failed", "message": "boom"}
    assert latest_path.read_text(encoding="utf-8") == latest_before


def test_engine_output_without_membership_is_rejected(tmp_path):
    def runner_without_membership(asset_id, points, config):
        return {
            "schema_version": "1.0",
            "asset_id": asset_id,
            "point_count": len(points),
            "object_count": 1,
            "noise_point_count": 0,
            "objects": [{"object_id": "obj-001", "point_count": len(points)}],
        }

    with pytest.raises(ValueError, match="membership"):
        run_segmentation(
            tmp_path,
            asset_id="scan",
            asset_version="v1",
            source_uri="scan.points.json",
            points=sample_points(),
            config={"engine": "external"},
            run_id="seg-run-001",
            runners={"external": runner_without_membership},
        )
