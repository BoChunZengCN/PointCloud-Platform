import json

import pytest

from pc_system.segmentation_correction_events import apply_correction_event
from pc_system.segmentation_correction_releases import (
    list_correction_releases,
    load_correction_release,
    publish_correction_release,
    transition_correction_session,
)
from pc_system.segmentation_corrections import (
    CorrectionError,
    create_correction_session,
    load_correction_points,
    load_correction_session,
)
from phase14_helpers import correction_session


def reviewed_session(project):
    session = correction_session(project)
    session = apply_correction_event(
        project,
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
    return transition_correction_session(
        project,
        asset_id="scan",
        session_id="session-001",
        action="submit",
        actor="alice",
        expected_revision=session["revision"],
    )


def publish_reviewed(project, *, split="development", search_config=None):
    reviewed = reviewed_session(project)
    return publish_correction_release(
        project,
        asset_id="scan",
        session_id="session-001",
        release_id=f"release-{split.replace('_', '-')}",
        reviewer="bob",
        expected_revision=reviewed["revision"],
        benchmark_split=split,
        license_name="internal",
        search_config=search_config,
    )


def release_root(project, release_id):
    return (
        project
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / release_id
    )


def test_publish_freezes_reviewed_revision_and_refuses_overwrite(tmp_path):
    reviewed = reviewed_session(tmp_path)
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

    assert release["status"] == "published"
    assert release["source_revision"] == reviewed["revision"]
    assert load_correction_session(tmp_path, "scan", "session-001")["status"] == "published"
    assert load_correction_release(tmp_path, "scan", "release-001") == release
    assert [item["release_id"] for item in list_correction_releases(tmp_path, "scan")] == [
        "release-001"
    ]
    original = (release_root(tmp_path, "release-001") / "labels.json").read_bytes()
    with pytest.raises(CorrectionError) as exc_info:
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
    assert exc_info.value.code == "release_exists"
    assert (release_root(tmp_path, "release-001") / "labels.json").read_bytes() == original


def test_publication_writes_derived_benchmark_and_feedback(tmp_path):
    release = publish_reviewed(tmp_path)
    root = release_root(tmp_path, release["release_id"])
    benchmark_root = tmp_path / "benchmarks" / release["derived_benchmark_id"]
    feedback_root = (
        tmp_path / "datasets" / "segmentation_feedback" / release["release_id"]
    )
    labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))

    assert {
        "correction_release.json",
        "labels.json",
        "objects.json",
        "correction_diff.json",
        "provenance.json",
        "publication_tasks.json",
        "training_policy.json",
    } <= {path.name for path in root.iterdir()}
    assert [item["point_index"] for item in labels["point_labels"]] == [0, 1, 2, 3]
    assert (benchmark_root / "benchmark.json").is_file()
    assert (benchmark_root / "samples" / "sample-001" / "labels.json").is_file()
    assert (feedback_root / "feedback_manifest.json").is_file()
    assert (feedback_root / "before_labels.json").is_file()
    assert (feedback_root / "after_labels.json").is_file()
    assert (feedback_root / "operations.jsonl").is_file()


@pytest.mark.parametrize(
    ("split", "expected"),
    [
        ("development", "eligible"),
        ("validation", "evaluation_only"),
        ("golden_regression", "evaluation_only"),
    ],
)
def test_training_policy_is_explicit(tmp_path, split, expected):
    release = publish_reviewed(tmp_path, split=split)
    policy = json.loads(
        (
            release_root(tmp_path, release["release_id"])
            / "training_policy.json"
        ).read_text(encoding="utf-8")
    )

    assert policy["eligibility"] == expected
    assert policy["benchmark_split"] == split


def test_golden_regression_rejects_parameter_search_before_publication(tmp_path):
    reviewed = reviewed_session(tmp_path)

    with pytest.raises(CorrectionError) as exc_info:
        publish_correction_release(
            tmp_path,
            asset_id="scan",
            session_id="session-001",
            release_id="release-golden",
            reviewer="bob",
            expected_revision=reviewed["revision"],
            benchmark_split="golden_regression",
            license_name="internal",
            search_config={"max_trials": 2},
        )

    assert exc_info.value.code == "golden_search_forbidden"
    assert not release_root(tmp_path, "release-golden").exists()
    assert load_correction_session(tmp_path, "scan", "session-001")["status"] == "in_review"


def test_restore_release_creates_new_draft_without_mutating_release(tmp_path):
    release = publish_reviewed(tmp_path)
    release_path = release_root(tmp_path, release["release_id"]) / "labels.json"
    original_release_bytes = release_path.read_bytes()

    restored = create_correction_session(
        tmp_path,
        asset_id="scan",
        run_id="run-001",
        session_id="session-restore",
        sample_id="sample-001",
        actor="alice",
        baseline_release_id=release["release_id"],
    )
    restored_points = load_correction_points(
        tmp_path, "scan", "session-restore"
    )["points"]

    assert restored["baseline"]["kind"] == "correction_release"
    assert restored["supersedes_release_id"] == release["release_id"]
    assert restored_points[0]["draft"]["class_id"] == "pipe"
    assert release_path.read_bytes() == original_release_bytes


def test_only_in_review_session_can_publish(tmp_path):
    session = correction_session(tmp_path)

    with pytest.raises(CorrectionError) as exc_info:
        publish_correction_release(
            tmp_path,
            asset_id="scan",
            session_id="session-001",
            release_id="release-001",
            reviewer="bob",
            expected_revision=session["revision"],
            benchmark_split="development",
            license_name="internal",
        )

    assert exc_info.value.code == "invalid_session_state"
