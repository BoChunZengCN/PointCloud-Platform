import json

import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_retrieval_input import load_retrieval_object
from pc_system.segmentation_correction_events import apply_correction_event
from pc_system.segmentation_correction_releases import (
    publish_correction_release,
    transition_correction_session,
)
from phase14_helpers import correction_session, write_completed_run
from phase15b2_support import AUDITOR, EXPERT


def _publish_confirmed_release(project):
    session = correction_session(project)
    confirmed = apply_correction_event(
        project,
        asset_id="scan",
        session_id="session-001",
        actor="alice",
        expected_revision=session["revision"],
        client_request_id="request-confirm",
        operation={"type": "confirm", "instance_ids": ["obj-001"]},
    )
    reviewed = transition_correction_session(
        project,
        asset_id="scan",
        session_id="session-001",
        action="submit",
        actor="alice",
        expected_revision=confirmed["revision"],
    )
    return publish_correction_release(
        project,
        asset_id="scan",
        session_id="session-001",
        release_id="release-001",
        reviewer="bob",
        expected_revision=reviewed["revision"],
        benchmark_split="development",
        license_name="internal",
    )


def _release_root(project):
    return (
        project
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / "release-001"
    )


def test_production_input_uses_published_release_and_confirmed_evidence(tmp_path):
    release = _publish_confirmed_release(tmp_path)

    query = load_retrieval_object(
        tmp_path,
        source_kind="correction_release",
        asset_id="scan",
        source_id=release["release_id"],
        instance_id="obj-001",
    )

    assert query["source_kind"] == "correction_release"
    assert query["category_trust"] == "human_confirmed"
    assert query["class_id"] == "object_candidate"
    assert query["coordinate_unit"] == "m"
    assert query["point_count"] == 2
    assert query["points"] == [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.1, "y": 0.0, "z": 0.0},
    ]
    assert len(query["object_fingerprint"]) == 64


def test_release_without_declared_review_evidence_is_legacy_unknown(tmp_path):
    _publish_confirmed_release(tmp_path)
    root = _release_root(tmp_path)
    release_path = root / "correction_release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["artifacts"].pop("object_review_evidence")
    release_path.write_text(json.dumps(release), encoding="utf-8")
    (root / "object_review_evidence.json").unlink()

    query = load_retrieval_object(
        tmp_path,
        source_kind="correction_release",
        asset_id="scan",
        source_id="release-001",
        instance_id="obj-001",
    )

    assert query["category_trust"] == "legacy_unknown"


def test_declared_review_evidence_cannot_be_silently_removed(tmp_path):
    _publish_confirmed_release(tmp_path)
    (_release_root(tmp_path) / "object_review_evidence.json").unlink()

    with pytest.raises(ModelMatchingError) as error:
        load_retrieval_object(
            tmp_path,
            source_kind="correction_release",
            asset_id="scan",
            source_id="release-001",
            instance_id="obj-001",
        )

    assert error.value.code == "object_review_evidence_invalid"


def test_release_point_tampering_is_rejected(tmp_path):
    _publish_confirmed_release(tmp_path)
    labels_path = _release_root(tmp_path) / "labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels["point_labels"][0]["x"] = 99.0
    labels_path.write_text(json.dumps(labels), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as error:
        load_retrieval_object(
            tmp_path,
            source_kind="correction_release",
            asset_id="scan",
            source_id="release-001",
            instance_id="obj-001",
        )

    assert error.value.code == "invalid_retrieval_input"


def test_release_source_fingerprint_tampering_is_rejected(tmp_path):
    _publish_confirmed_release(tmp_path)
    release_path = _release_root(tmp_path) / "correction_release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["source_fingerprint"] = "f" * 64
    release_path.write_text(json.dumps(release), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as error:
        load_retrieval_object(
            tmp_path,
            source_kind="correction_release",
            asset_id="scan",
            source_id="release-001",
            instance_id="obj-001",
        )

    assert error.value.code == "invalid_retrieval_input"


def test_draft_source_kind_is_not_a_retrieval_source(tmp_path):
    correction_session(tmp_path)

    with pytest.raises(ModelMatchingError) as error:
        load_retrieval_object(
            tmp_path,
            source_kind="correction_session",
            asset_id="scan",
            source_id="session-001",
            instance_id="obj-001",
        )

    assert error.value.code == "invalid_retrieval_input"


def test_expert_can_load_completed_segmentation_run_as_experimental_input(tmp_path):
    write_completed_run(tmp_path)

    query = load_retrieval_object(
        tmp_path,
        source_kind="segmentation_run",
        asset_id="scan",
        source_id="run-001",
        instance_id="obj-001",
        principal=EXPERT,
    )

    assert query["source_kind"] == "segmentation_run"
    assert query["category_trust"] == "algorithm_only"
    assert query["class_id"] == "object_candidate"
    assert query["coordinate_unit"] == "m"
    assert query["point_count"] == 2


def test_experimental_input_requires_expert_and_completed_run(tmp_path):
    write_completed_run(tmp_path)
    run_path = (
        tmp_path
        / "reports"
        / "segmentation_runs"
        / "scan"
        / "run-001"
        / "segmentation_run.json"
    )

    with pytest.raises(ModelMatchingError) as denied:
        load_retrieval_object(
            tmp_path,
            source_kind="segmentation_run",
            asset_id="scan",
            source_id="run-001",
            instance_id="obj-001",
            principal=AUDITOR,
        )
    assert denied.value.code == "permission_denied"

    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["status"] = "running"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as incomplete:
        load_retrieval_object(
            tmp_path,
            source_kind="segmentation_run",
            asset_id="scan",
            source_id="run-001",
            instance_id="obj-001",
            principal=EXPERT,
        )
    assert incomplete.value.code == "invalid_retrieval_input"


def test_missing_object_has_stable_error(tmp_path):
    _publish_confirmed_release(tmp_path)

    with pytest.raises(ModelMatchingError) as error:
        load_retrieval_object(
            tmp_path,
            source_kind="correction_release",
            asset_id="scan",
            source_id="release-001",
            instance_id="missing-object",
        )

    assert error.value.code == "retrieval_object_not_found"
