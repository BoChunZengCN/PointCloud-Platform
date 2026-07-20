import json

import pytest

from pc_system.segmentation_corrections import (
    CorrectionError,
    create_correction_session,
    list_correction_sessions,
    load_correction_baseline,
    load_correction_objects,
    load_correction_points,
    load_correction_session,
)
from phase14_helpers import write_completed_run, write_development_benchmark


def test_create_session_materializes_exact_automatic_baseline(tmp_path):
    write_completed_run(tmp_path)

    session = create_correction_session(
        tmp_path,
        asset_id="scan",
        run_id="run-001",
        session_id="session-001",
        sample_id="sample-001",
        actor="alice",
    )
    page = load_correction_points(tmp_path, "scan", "session-001")

    assert session["status"] == "draft"
    assert session["revision"] == 0
    assert session["active_editor"] == "alice"
    assert session["baseline"]["kind"] == "automatic_segmentation"
    assert page["total"] == 4
    assert [point["source_point_index"] for point in page["points"]] == [0, 1, 2, 3]
    assert all({"baseline", "draft"} <= point.keys() for point in page["points"])
    assert all(point["baseline"] == point["draft"] for point in page["points"])
    assert len({point["draft"]["instance_id"] for point in page["points"]}) == 2


def test_session_writes_complete_initial_artifact_set(tmp_path):
    write_completed_run(tmp_path)

    session = create_correction_session(
        tmp_path,
        asset_id="scan",
        run_id="run-001",
        session_id="session-001",
        sample_id="sample-001",
        actor="alice",
    )
    session_dir = (
        tmp_path
        / "reports"
        / "segmentation_corrections"
        / "scan"
        / "session-001"
    )

    assert set(session["artifacts"]) >= {
        "baseline_labels",
        "events",
        "draft_labels",
        "draft_objects",
    }
    assert (session_dir / "events.jsonl").read_text(encoding="utf-8") == ""
    assert load_correction_baseline(tmp_path, "scan", "session-001")["point_count"] == 4
    assert load_correction_objects(tmp_path, "scan", "session-001")["object_count"] == 2
    assert load_correction_session(tmp_path, "scan", "session-001")["session_id"] == "session-001"
    assert [item["session_id"] for item in list_correction_sessions(tmp_path, "scan")] == [
        "session-001"
    ]


def test_existing_labels_overlay_only_matching_source_indices(tmp_path):
    write_completed_run(tmp_path)
    write_development_benchmark(tmp_path)

    create_correction_session(
        tmp_path,
        asset_id="scan",
        run_id="run-001",
        session_id="session-001",
        sample_id="sample-001",
        actor="alice",
        benchmark_id="bench-dev",
    )
    page = load_correction_points(
        tmp_path, "scan", "session-001", offset=0, limit=2
    )

    assert page["total"] == 4
    assert len(page["points"]) == 2
    assert page["points"][0]["draft"]["class_id"] == "pipe"
    assert page["points"][0]["draft"]["instance_id"] == "verified-pipe"
    assert page["points"][1]["draft"] == page["points"][1]["baseline"]


@pytest.mark.parametrize(
    ("offset", "limit"),
    [(-1, 10), (0, 0), (0, 50001)],
)
def test_point_pagination_is_bounded(tmp_path, offset, limit):
    write_completed_run(tmp_path)
    create_correction_session(
        tmp_path,
        asset_id="scan",
        run_id="run-001",
        session_id="session-001",
        sample_id="sample-001",
        actor="alice",
    )

    with pytest.raises(CorrectionError) as exc_info:
        load_correction_points(
            tmp_path, "scan", "session-001", offset=offset, limit=limit
        )

    assert exc_info.value.code == "invalid_pagination"


def test_create_session_rejects_missing_run_without_partial_directory(tmp_path):
    with pytest.raises(CorrectionError) as exc_info:
        create_correction_session(
            tmp_path,
            asset_id="scan",
            run_id="missing",
            session_id="session-001",
            sample_id="sample-001",
            actor="alice",
        )

    assert exc_info.value.code == "segmentation_run_not_found"
    assert not (
        tmp_path
        / "reports"
        / "segmentation_corrections"
        / "scan"
        / "session-001"
    ).exists()


def test_create_session_rejects_duplicate_session(tmp_path):
    write_completed_run(tmp_path)
    arguments = {
        "asset_id": "scan",
        "run_id": "run-001",
        "session_id": "session-001",
        "sample_id": "sample-001",
        "actor": "alice",
    }
    create_correction_session(tmp_path, **arguments)

    with pytest.raises(CorrectionError) as exc_info:
        create_correction_session(tmp_path, **arguments)

    assert exc_info.value.code == "session_exists"


def test_source_fingerprint_mismatch_is_rejected(tmp_path):
    source = write_completed_run(tmp_path)
    source.write_text(
        json.dumps([{"x": 9.0, "y": 9.0, "z": 9.0}]), encoding="utf-8"
    )

    with pytest.raises(CorrectionError) as exc_info:
        create_correction_session(
            tmp_path,
            asset_id="scan",
            run_id="run-001",
            session_id="session-001",
            sample_id="sample-001",
            actor="alice",
        )

    assert exc_info.value.code == "source_fingerprint_mismatch"
