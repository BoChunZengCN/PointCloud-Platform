import json

import pytest

from pc_system.segmentation_correction_events import (
    active_correction_events,
    apply_correction_event,
)
from pc_system.segmentation_correction_releases import (
    publish_correction_release,
    transition_correction_session,
)
from pc_system.segmentation_review_evidence import (
    build_object_review_evidence,
    load_object_review_evidence,
)
from pc_system.segmentation_corrections import CorrectionError
from phase14_helpers import correction_session


def _assignment(index, *, instance_id="obj-001", class_id="pump", origin="automatic_segmentation"):
    return {
        "source_point_index": index,
        "x": float(index),
        "y": 0.0,
        "z": 0.0,
        "instance_id": instance_id,
        "class_id": class_id,
        "is_noise": False,
        "origin": origin,
    }


def _event(revision, operation):
    return {
        "schema_version": "1.0",
        "event_id": f"event-{revision:06d}",
        "session_id": "session-001",
        "actor": "alice",
        "timestamp": f"2026-08-28T00:00:0{revision}+00:00",
        "client_request_id": f"request-{revision:03d}",
        "resulting_revision": revision,
        "operation": operation,
    }


def _objects(*items):
    return {
        "schema_version": "1.0",
        "object_count": len(items),
        "objects": list(items),
    }


def _object(instance_id="obj-001", class_id="pump", *, indices=(0, 1)):
    return {
        "instance_id": instance_id,
        "class_id": class_id,
        "point_count": len(indices),
        "source_point_indices": list(indices),
        "center": [0.5, 0.0, 0.0],
        "size": [1.0, 0.001, 0.001],
        "rotation": [0.0, 0.0, 0.0, 1.0],
        "review_state": "unreviewed",
    }


def test_active_events_respect_undo_redo_without_mutating_source():
    confirm = _event(1, {"type": "confirm", "instance_ids": ["obj-001"]})
    relabel = _event(
        2,
        {"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"},
    )
    events = [
        confirm,
        relabel,
        _event(3, {"type": "undo"}),
        _event(4, {"type": "redo"}),
        _event(5, {"type": "undo"}),
    ]

    assert active_correction_events(events) == [confirm]
    assert events[1] == relabel


def test_review_evidence_binds_latest_final_confirmation():
    assignments = [
        _assignment(0, class_id="pipe", origin="human_correction"),
        _assignment(1, class_id="pipe", origin="human_correction"),
    ]
    events = [
        _event(1, {"type": "confirm", "instance_ids": ["obj-001"]}),
        _event(
            2,
            {"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"},
        ),
        _event(3, {"type": "confirm", "instance_ids": ["obj-001"]}),
    ]

    evidence = build_object_review_evidence(
        asset_id="scan",
        release_id="release-001",
        source_fingerprint="a" * 64,
        draft={
            "assignments": assignments,
            "confirmed_instance_ids": ["obj-001"],
        },
        objects=_objects(_object(class_id="pipe")),
        active_events=events,
    )

    item = evidence["objects"][0]
    assert item["review_state"] == "confirmed"
    assert item["classification_source"] == "human_confirmed"
    assert item["confirmation_event_sequence"] == 3
    assert item["confirmation_request_id"] == "request-003"
    assert len(item["object_fingerprint"]) == 64
    assert len(evidence["objects_fingerprint"]) == 64


def test_unconfirmed_human_edit_is_not_promoted_to_human_confirmed():
    assignments = [
        _assignment(0, class_id="pipe", origin="human_correction"),
        _assignment(1, class_id="pipe", origin="human_correction"),
    ]

    evidence = build_object_review_evidence(
        asset_id="scan",
        release_id="release-001",
        source_fingerprint="b" * 64,
        draft={"assignments": assignments, "confirmed_instance_ids": []},
        objects=_objects(_object(class_id="pipe")),
        active_events=[],
    )

    item = evidence["objects"][0]
    assert item["review_state"] == "unreviewed"
    assert item["classification_source"] == "human_edited_unconfirmed"
    assert item["confirmation_event_sequence"] is None
    assert item["confirmation_request_id"] is None


def test_non_object_review_entry_has_stable_domain_error():
    with pytest.raises(CorrectionError) as error:
        build_object_review_evidence(
            asset_id="scan",
            release_id="release-001",
            source_fingerprint="c" * 64,
            draft={
                "assignments": [_assignment(0)],
                "confirmed_instance_ids": [],
            },
            objects={"objects": [None]},
            active_events=[],
        )

    assert error.value.code == "invalid_review_evidence"


def test_publish_writes_review_evidence_as_release_artifact(tmp_path):
    session = correction_session(tmp_path)
    confirmed = apply_correction_event(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        actor="alice",
        expected_revision=session["revision"],
        client_request_id="request-confirm",
        operation={"type": "confirm", "instance_ids": ["obj-001"]},
    )
    reviewed = transition_correction_session(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        action="submit",
        actor="alice",
        expected_revision=confirmed["revision"],
    )

    release = publish_correction_release(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        release_id="release-001",
        reviewer="bob",
        expected_revision=reviewed["revision"],
        benchmark_split="development",
        license_name="internal",
    )
    root = (
        tmp_path
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / "release-001"
    )

    assert release["artifacts"]["object_review_evidence"] == "object_review_evidence.json"
    assert json.loads((root / "object_review_evidence.json").read_text(encoding="utf-8"))[
        "objects"
    ][0]["review_state"] == "confirmed"
    assert load_object_review_evidence(tmp_path, "scan", "release-001")["release_id"] == "release-001"


def test_legacy_release_without_review_evidence_returns_none(tmp_path):
    legacy = (
        tmp_path
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / "release-legacy"
    )
    legacy.mkdir(parents=True)

    assert load_object_review_evidence(tmp_path, "scan", "release-legacy") is None


def test_tampered_review_state_is_rejected(tmp_path):
    session = correction_session(tmp_path)
    reviewed = transition_correction_session(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        action="submit",
        actor="alice",
        expected_revision=session["revision"],
    )
    publish_correction_release(
        tmp_path,
        asset_id="scan",
        session_id="session-001",
        release_id="release-001",
        reviewer="bob",
        expected_revision=reviewed["revision"],
        benchmark_split="development",
        license_name="internal",
    )
    path = (
        tmp_path
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / "release-001"
        / "object_review_evidence.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["objects"][0]["review_state"] = "confirmed"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(CorrectionError) as error:
        load_object_review_evidence(tmp_path, "scan", "release-001")

    assert error.value.code == "invalid_review_evidence"
