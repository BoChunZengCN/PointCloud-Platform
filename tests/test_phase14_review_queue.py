from pc_system.segmentation_correction_events import apply_correction_event
from pc_system.segmentation_corrections import load_correction_session
from pc_system.segmentation_review_queue import (
    build_correction_diff,
    build_review_queue,
)
from phase14_helpers import correction_session


def labels(assignments):
    return {"schema_version": "1.0", "assignments": assignments}


def point(index, instance_id, class_id="object_candidate", is_noise=False):
    return {
        "source_point_index": index,
        "x": float(index),
        "y": 0.0,
        "z": 0.0,
        "instance_id": instance_id,
        "class_id": class_id,
        "is_noise": is_noise,
    }


def test_queue_prioritizes_evaluation_errors_before_proxy_flags():
    queue = build_review_queue(
        session={"session_id": "session-001"},
        baseline=labels([point(0, "obj-1"), point(1, "obj-2")]),
        draft=labels([point(0, "obj-1"), point(1, "obj-2")]),
        quality={
            "flags": [
                {
                    "object_id": "obj-2",
                    "code": "small_fragment",
                    "severity": "high",
                }
            ]
        },
        evaluation={
            "instance_errors": [
                {
                    "instance_id": "obj-1",
                    "kind": "under_segmented",
                    "severity": "high",
                }
            ]
        },
    )

    assert queue["items"][0]["instance_id"] == "obj-1"
    assert queue["items"][0]["suggested_action"] == "split"
    assert queue["items"][0]["source"] == "golden_evaluation"


def test_diff_counts_changed_points_instances_and_classes():
    baseline = labels(
        [
            point(0, "obj-1"),
            point(1, "obj-1"),
            point(2, "obj-2"),
        ]
    )
    draft = labels(
        [
            point(0, "obj-1", class_id="pipe"),
            point(1, "split-1"),
            point(2, "noise", class_id="noise", is_noise=True),
        ]
    )

    diff = build_correction_diff(baseline, draft)

    assert diff["changed_point_count"] == 3
    assert diff["created_instance_count"] == 1
    assert diff["removed_instance_count"] == 1
    assert diff["class_change_count"] == 1
    assert diff["noise_added_point_count"] == 1


def test_session_and_event_materialize_queue_and_diff_artifacts(tmp_path):
    session = correction_session(tmp_path)
    session_dir = (
        tmp_path
        / "reports"
        / "segmentation_corrections"
        / "scan"
        / "session-001"
    )

    assert (session_dir / "review_queue.json").is_file()
    assert (session_dir / "correction_diff.json").is_file()
    updated = apply_correction_event(
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
    persisted = load_correction_session(tmp_path, "scan", "session-001")

    assert updated["artifacts"]["review_queue"] == "review_queue.json"
    assert persisted["correction_diff"]["class_change_count"] == 1


def test_confirm_marks_matching_queue_item_without_mutating_suggestion():
    queue = build_review_queue(
        session={"session_id": "session-001"},
        baseline=labels([point(0, "obj-1")]),
        draft={
            **labels([point(0, "obj-1")]),
            "confirmed_instance_ids": ["obj-1"],
        },
        quality={
            "flags": [
                {
                    "object_id": "obj-1",
                    "code": "small_fragment",
                    "severity": "medium",
                }
            ]
        },
    )

    assert queue["items"][0]["confirmed"] is True
    assert queue["items"][0]["suggested_action"] == "merge"
