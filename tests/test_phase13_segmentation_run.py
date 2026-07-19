import json

import pytest

from pc_system.segmentation_run import (
    build_segmentation_run,
    fingerprint_config,
    publish_latest_success,
    write_segmentation_run,
)


def test_config_fingerprint_is_order_independent():
    assert fingerprint_config({"min_points": 10, "distance": 0.2}) == fingerprint_config(
        {"distance": 0.2, "min_points": 10}
    )


def test_build_run_records_requested_and_executed_engine_separately():
    run = build_segmentation_run(
        run_id="seg-run-001",
        asset_id="scan-a",
        asset_version="v1",
        source_uri="data/assets/scan-a/source.las",
        source_point_count=120,
        config={"engine": "open3d_dbscan"},
        requested_engine="open3d_dbscan",
    )

    assert run["status"] == "planned"
    assert run["requested_engine"] == "open3d_dbscan"
    assert run["executed_engine"] is None
    assert run["config_fingerprint"]
    assert run["artifacts"] == {}


def test_write_segmentation_run_writes_manifest(tmp_path):
    run = build_segmentation_run(
        run_id="seg-run-001",
        asset_id="scan-a",
        asset_version="v1",
        source_uri="scan.points.json",
        source_point_count=2,
        config={"engine": "builtin_geometric"},
        requested_engine="builtin_geometric",
    )

    path = write_segmentation_run(run, tmp_path)

    assert path == tmp_path / "segmentation_run.json"
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "seg-run-001"


def test_latest_projection_accepts_only_completed_runs(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "object_segments.json"
    source.write_text('{"asset_id": "scan-a"}', encoding="utf-8")
    run = {
        "status": "completed",
        "artifacts": {"object_segments": "object_segments.json"},
    }

    output = publish_latest_success(run, run_dir, tmp_path / "latest")

    assert output == tmp_path / "latest" / "object_segments.json"
    assert json.loads(output.read_text(encoding="utf-8"))["asset_id"] == "scan-a"

    with pytest.raises(ValueError, match="completed"):
        publish_latest_success(
            {"status": "failed", "artifacts": {"object_segments": "object_segments.json"}},
            run_dir,
            tmp_path / "failed-latest",
        )
