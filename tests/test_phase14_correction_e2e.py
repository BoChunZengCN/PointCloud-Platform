import json

import pytest
from fastapi.testclient import TestClient

import pc_system.segmentation_correction_releases as releases_module
from pc_system.api import create_app
from pc_system.segmentation_benchmarks import load_benchmark_sample
from pc_system.segmentation_correction_events import apply_correction_event
from pc_system.segmentation_correction_releases import (
    publish_correction_release,
    transition_correction_session,
)
from pc_system.segmentation_corrections import (
    create_correction_session,
    load_correction_baseline,
    load_correction_points,
    load_correction_session,
)
from pc_system.json_io import write_json
from phase14_helpers import correction_session, write_completed_run


def test_end_to_end_correct_review_publish_and_load_benchmark(tmp_path):
    write_completed_run(tmp_path)
    client = TestClient(create_app(tmp_path, api_key="secret"))
    headers = {"X-API-Key": "secret"}
    created = client.post(
        "/segmentation-corrections/scan",
        headers=headers,
        json={
            "run_id": "run-001",
            "session_id": "session-001",
            "sample_id": "sample-001",
            "actor": "alice",
        },
    ).json()
    corrected = client.post(
        "/segmentation-corrections/scan/session-001/events",
        headers=headers,
        json={
            "actor": "alice",
            "expected_revision": created["revision"],
            "client_request_id": "request-merge",
            "operation": {
                "type": "merge",
                "instance_ids": ["obj-001", "obj-002"],
                "target_instance_id": "obj-001",
            },
        },
    ).json()
    reviewed = client.post(
        "/segmentation-corrections/scan/session-001/submit",
        headers=headers,
        json={"actor": "alice", "expected_revision": corrected["revision"]},
    ).json()
    release = client.post(
        "/segmentation-corrections/scan/session-001/publish",
        headers=headers,
        json={
            "release_id": "release-001",
            "reviewer": "bob",
            "expected_revision": reviewed["revision"],
            "benchmark_split": "development",
            "license": "internal",
        },
    ).json()
    manifest, labels = load_benchmark_sample(
        tmp_path, release["derived_benchmark_id"], "sample-001"
    )
    policy = json.loads(
        (
            tmp_path
            / "reports"
            / "segmentation_correction_releases"
            / "scan"
            / "release-001"
            / "training_policy.json"
        ).read_text(encoding="utf-8")
    )

    assert manifest["split"] == "development"
    assert len(labels["point_labels"]) == 4
    assert {item["instance_id"] for item in labels["point_labels"]} == {"obj-001"}
    assert policy["eligibility"] == "eligible"
    assert (
        client.post(
            "/segmentation-corrections/scan/session-001/events",
            headers=headers,
            json={
                "actor": "alice",
                "expected_revision": reviewed["revision"] + 1,
                "client_request_id": "request-after-publish",
                "operation": {"type": "confirm", "instance_ids": ["obj-001"]},
            },
        ).status_code
        == 409
    )


def test_load_recovers_materialized_draft_from_appended_event(tmp_path):
    session = correction_session(tmp_path)
    apply_correction_event(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        actor="alice",
        expected_revision=0,
        client_request_id="request-relabel",
        operation={
            "type": "relabel",
            "instance_ids": ["obj-001"],
            "class_id": "pipe",
        },
    )
    session_dir = (
        tmp_path
        / "reports"
        / "segmentation_corrections"
        / "scan"
        / "session-001"
    )
    write_json(session, session_dir / "correction_session.json")
    write_json(
        load_correction_baseline(tmp_path, "scan", "session-001"),
        session_dir / "draft_labels.json",
    )

    recovered = load_correction_session(tmp_path, "scan", "session-001")

    assert recovered["revision"] == 1
    assert load_correction_points(tmp_path, "scan", "session-001")["points"][0][
        "draft"
    ]["class_id"] == "pipe"


def test_required_publication_failure_leaves_no_final_artifacts(tmp_path, monkeypatch):
    session = correction_session(tmp_path)
    reviewed = transition_correction_session(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        action="submit",
        actor="alice",
        expected_revision=session["revision"],
    )
    original_write_json = releases_module.write_json

    def failing_write_json(data, path):
        if path.name == "provenance.json":
            raise OSError("injected publication failure")
        return original_write_json(data, path)

    monkeypatch.setattr(releases_module, "write_json", failing_write_json)

    with pytest.raises(OSError, match="injected"):
        publish_correction_release(
            tmp_path,
            asset_id="scan",
            session_id="session-001",
            release_id="release-failed",
            reviewer="bob",
            expected_revision=reviewed["revision"],
            benchmark_split="development",
            license_name="internal",
        )

    assert not (
        tmp_path
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / "release-failed"
    ).exists()
    assert not (tmp_path / "benchmarks" / "release-failed-benchmark").exists()
    assert not (
        tmp_path / "datasets" / "segmentation_feedback" / "release-failed"
    ).exists()


def test_session_publish_state_failure_rolls_back_final_directories(
    tmp_path, monkeypatch
):
    session = correction_session(tmp_path)
    reviewed = transition_correction_session(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        action="submit",
        actor="alice",
        expected_revision=session["revision"],
    )
    original_write_json = releases_module.write_json

    def failing_session_publish(data, path):
        if path.name == "correction_session.json" and data.get("status") == "published":
            raise OSError("injected session-state failure")
        return original_write_json(data, path)

    monkeypatch.setattr(releases_module, "write_json", failing_session_publish)

    with pytest.raises(OSError, match="session-state"):
        publish_correction_release(
            tmp_path,
            asset_id="scan",
            session_id="session-001",
            release_id="release-state-failed",
            reviewer="bob",
            expected_revision=reviewed["revision"],
            benchmark_split="development",
            license_name="internal",
        )

    assert not (
        tmp_path
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / "release-state-failed"
    ).exists()
    assert load_correction_session(tmp_path, "scan", "session-001")["status"] == "in_review"
