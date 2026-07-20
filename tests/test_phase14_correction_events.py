import pytest

from pc_system.segmentation_correction_events import (
    apply_correction_event,
    materialize_correction,
    read_correction_events,
)
from pc_system.segmentation_corrections import (
    CorrectionError,
    load_correction_baseline,
    load_correction_objects,
    load_correction_points,
)
from phase14_helpers import correction_session


def apply_operation(
    project,
    session,
    operation,
    *,
    request_id,
    expected_revision=None,
    actor="alice",
):
    return apply_correction_event(
        project,
        asset_id="scan",
        session_id="session-001",
        actor=actor,
        expected_revision=(
            session["revision"] if expected_revision is None else expected_revision
        ),
        client_request_id=request_id,
        operation=operation,
    )


def assignment_map(project):
    page = load_correction_points(project, "scan", "session-001")
    return {
        point["source_point_index"]: point["draft"] for point in page["points"]
    }


def test_merge_reassigns_both_objects_to_target(tmp_path):
    session = correction_session(tmp_path)

    updated = apply_operation(
        tmp_path,
        session,
        {
            "type": "merge",
            "instance_ids": ["obj-001", "obj-002"],
            "target_instance_id": "obj-001",
        },
        request_id="request-merge",
    )

    assert updated["revision"] == 1
    assert {item["instance_id"] for item in assignment_map(tmp_path).values()} == {
        "obj-001"
    }
    assert load_correction_objects(tmp_path, "scan", "session-001")["object_count"] == 1


def test_split_moves_exact_selected_subset_to_deterministic_instance(tmp_path):
    session = correction_session(tmp_path)

    updated = apply_operation(
        tmp_path,
        session,
        {
            "type": "split",
            "instance_id": "obj-001",
            "source_point_indices": [1],
        },
        request_id="request-split",
    )
    assignments = assignment_map(tmp_path)

    assert assignments[0]["instance_id"] == "obj-001"
    assert assignments[1]["instance_id"] == "split-0001"
    assert updated["last_event"]["operation"]["new_instance_id"] == "split-0001"


def test_relabel_noise_restore_and_confirm_are_replayable(tmp_path):
    session = correction_session(tmp_path)
    session = apply_operation(
        tmp_path,
        session,
        {"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"},
        request_id="request-relabel",
    )
    session = apply_operation(
        tmp_path,
        session,
        {"type": "mark_noise", "source_point_indices": [1]},
        request_id="request-noise",
    )
    session = apply_operation(
        tmp_path,
        session,
        {
            "type": "restore_from_noise",
            "source_point_indices": [1],
            "target_instance_id": "obj-001",
        },
        request_id="request-restore-noise",
    )
    session = apply_operation(
        tmp_path,
        session,
        {"type": "confirm", "instance_ids": ["obj-001"]},
        request_id="request-confirm",
    )
    baseline = load_correction_baseline(tmp_path, "scan", "session-001")
    replayed = materialize_correction(
        baseline, read_correction_events(tmp_path, "scan", "session-001")
    )

    assert assignment_map(tmp_path)[1] == {
        "instance_id": "obj-001",
        "class_id": "pipe",
        "is_noise": False,
    }
    assert replayed["draft_fingerprint"] == session["draft_fingerprint"]
    assert replayed["confirmed_instance_ids"] == ["obj-001"]


def test_stale_revision_does_not_append_event(tmp_path):
    session = correction_session(tmp_path)
    apply_operation(
        tmp_path,
        session,
        {"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"},
        request_id="request-1",
        expected_revision=0,
    )

    with pytest.raises(CorrectionError) as exc_info:
        apply_operation(
            tmp_path,
            session,
            {"type": "relabel", "instance_ids": ["obj-002"], "class_id": "tank"},
            request_id="request-2",
            expected_revision=0,
        )

    assert exc_info.value.code == "stale_revision"
    assert len(read_correction_events(tmp_path, "scan", "session-001")) == 1


def test_repeated_client_request_is_idempotent(tmp_path):
    session = correction_session(tmp_path)
    operation = {
        "type": "relabel",
        "instance_ids": ["obj-001"],
        "class_id": "pipe",
    }

    first = apply_operation(
        tmp_path, session, operation, request_id="request-1", expected_revision=0
    )
    second = apply_operation(
        tmp_path, session, operation, request_id="request-1", expected_revision=0
    )

    assert second == first
    assert len(read_correction_events(tmp_path, "scan", "session-001")) == 1


def test_foreign_active_editor_cannot_write(tmp_path):
    session = correction_session(tmp_path)

    with pytest.raises(CorrectionError) as exc_info:
        apply_operation(
            tmp_path,
            session,
            {"type": "confirm", "instance_ids": ["obj-001"]},
            request_id="request-bob",
            actor="bob",
        )

    assert exc_info.value.code == "session_locked"


def test_undo_redo_and_full_restore_are_append_only(tmp_path):
    session = correction_session(tmp_path)
    initial_fingerprint = session["draft_fingerprint"]
    changed = apply_operation(
        tmp_path,
        session,
        {"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"},
        request_id="request-change",
    )
    undone = apply_operation(
        tmp_path, changed, {"type": "undo"}, request_id="request-undo"
    )
    redone = apply_operation(
        tmp_path, undone, {"type": "redo"}, request_id="request-redo"
    )
    restored = apply_operation(
        tmp_path,
        redone,
        {"type": "restore", "scope": "all"},
        request_id="request-restore",
    )

    assert undone["draft_fingerprint"] == initial_fingerprint
    assert redone["draft_fingerprint"] == changed["draft_fingerprint"]
    assert restored["draft_fingerprint"] == initial_fingerprint
    assert len(read_correction_events(tmp_path, "scan", "session-001")) == 4


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        (
            {
                "type": "merge",
                "instance_ids": ["obj-001"],
                "target_instance_id": "obj-001",
            },
            "invalid_merge",
        ),
        (
            {
                "type": "split",
                "instance_id": "obj-001",
                "source_point_indices": [0, 1],
            },
            "invalid_split",
        ),
        (
            {"type": "mark_noise", "source_point_indices": [True]},
            "invalid_point_selection",
        ),
        ({"type": "unknown"}, "unsupported_operation"),
    ],
)
def test_invalid_operation_does_not_advance_revision(tmp_path, operation, code):
    session = correction_session(tmp_path)

    with pytest.raises(CorrectionError) as exc_info:
        apply_operation(
            tmp_path, session, operation, request_id=f"request-{code}"
        )

    assert exc_info.value.code == code
    assert read_correction_events(tmp_path, "scan", "session-001") == []
