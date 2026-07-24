import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import pc_system.model_matching_audit as audit_module
from pc_system.model_matching_audit import (
    append_operation_event,
    complete_operation,
    fail_operation,
    load_operation,
    record_denied_operation,
    read_operation_events,
    start_operation,
    verify_operation_chain,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


PRINCIPAL = Principal("alice", frozenset({"expert"}), "configured_token")


def mutation_failure_audits(project_root, target_operation_id):
    operations_root = (
        project_root / "reports" / "model_matching_operations"
    )
    audits = []
    for operation_root in operations_root.iterdir():
        if operation_root.name == target_operation_id:
            continue
        operation = load_operation(project_root, operation_root.name)
        if operation["operation_type"] == "audit.mutation_failure":
            audits.append(
                (
                    operation,
                    read_operation_events(project_root, operation_root.name),
                )
            )
    return audits


def test_operation_events_are_ordered_and_hash_chained(tmp_path):
    operation, replayed = start_operation(
        tmp_path,
        operation_id="op-001",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is False
    append_operation_event(
        tmp_path, "op-001", "model_asset.validated", {"model_id": "pump-a"}
    )
    complete_operation(tmp_path, "op-001", {"model_id": "pump-a"})
    events = read_operation_events(tmp_path, "op-001")
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert events[1]["previous_event_hash"] == events[0]["event_hash"]
    assert verify_operation_chain(events) is True
    assert load_operation(tmp_path, "op-001")["status"] == "completed"


def test_same_idempotency_request_replays_and_is_audited(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    count_before = len(read_operation_events(tmp_path, "op-001"))
    operation, replayed = start_operation(
        tmp_path, operation_id="op-002", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-002", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is True
    assert operation["operation_id"] == "op-001"
    events = read_operation_events(tmp_path, "op-001")
    assert len(events) == count_before + 1
    assert events[-1]["event_type"] == "operation.replayed"
    assert events[-1]["details"]["requested_operation_id"] == "op-002"


def test_same_operation_with_changed_payload_is_rejected(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path, operation_id="op-002", operation_type="model_asset.create",
            principal=PRINCIPAL, request_id="request-002", idempotency_key="idem-001",
            request_payload={"model_id": "pump-b"},
        )
    assert exc_info.value.code == "idempotency_conflict"


def test_tampered_event_breaks_verification(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    events = read_operation_events(tmp_path, "op-001")
    events[0]["details"]["request_id"] = "changed"
    assert verify_operation_chain(events) is False


def test_denied_request_records_system_audit_without_raw_token(tmp_path):
    operation_id = record_denied_operation(
        tmp_path, request_id="request-denied-001", route="POST /model-library/models",
        token="secret-token", reason="permission_denied",
    )
    operation = load_operation(tmp_path, operation_id)
    events = read_operation_events(tmp_path, operation_id)
    serialized = json.dumps({"operation": operation, "events": events})
    assert operation["status"] == "failed"
    assert events[-1]["details"]["code"] == "permission_denied"
    assert "secret-token" not in serialized


def test_failed_operation_records_stable_error_and_becomes_immutable(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    failed = fail_operation(
        tmp_path, "op-001", "invalid_model_geometry", "Mesh is empty."
    )
    assert failed["status"] == "failed"
    assert failed["error"] == {
        "code": "invalid_model_geometry",
        "message": "Mesh is empty.",
    }
    with pytest.raises(ModelMatchingError) as exc_info:
        complete_operation(tmp_path, "op-001", {"model_id": "pump-a"})
    assert exc_info.value.code == "operation_immutable"


def test_concurrent_append_returns_operation_busy_without_partial_event(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    events_before = read_operation_events(tmp_path, "op-001")
    lock_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-001"
        / ".write.lock"
    )
    lock_path.touch()
    with pytest.raises(ModelMatchingError) as exc_info:
        append_operation_event(tmp_path, "op-001", "model_asset.validated", {})
    assert exc_info.value.code == "operation_busy"
    assert read_operation_events(tmp_path, "op-001") == events_before
    audits = mutation_failure_audits(tmp_path, "op-001")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "operation_busy"
        and event["details"]["target_operation_id"] == "op-001"
        for event in audits[0][1]
    )


def test_concurrent_idempotency_replay_follows_started_event(tmp_path, monkeypatch):
    index_claimed = Event()
    release_claimant = Event()
    replay_entered = Event()
    replay_finished = Event()
    original_claim = audit_module._claim_idempotency_index
    original_replay = audit_module._replay_or_reject

    def delayed_claim(path, payload):
        original_claim(path, payload)
        index_claimed.set()
        assert release_claimant.wait(timeout=2)

    def observed_replay(*args, **kwargs):
        replay_entered.set()
        try:
            return original_replay(*args, **kwargs)
        finally:
            replay_finished.set()

    monkeypatch.setattr(audit_module, "_claim_idempotency_index", delayed_claim)
    monkeypatch.setattr(audit_module, "_replay_or_reject", observed_replay)
    arguments = {
        "operation_type": "model_asset.create",
        "principal": PRINCIPAL,
        "idempotency_key": "idem-001",
        "request_payload": {"model_id": "pump-a"},
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        claimant = executor.submit(
            start_operation,
            tmp_path,
            operation_id="op-001",
            request_id="request-001",
            **arguments,
        )
        assert index_claimed.wait(timeout=2)
        replay = executor.submit(
            start_operation,
            tmp_path,
            operation_id="op-002",
            request_id="request-002",
            **arguments,
        )
        assert replay_entered.wait(timeout=2)
        replay_finished.wait(timeout=0.2)
        release_claimant.set()
        claimant.result(timeout=2)
        replayed_operation, replayed = replay.result(timeout=2)
    assert replayed is True
    assert replayed_operation["operation_id"] == "op-001"
    assert [
        event["event_type"] for event in read_operation_events(tmp_path, "op-001")
    ] == ["operation.started", "operation.replayed"]


def test_immutable_transition_is_recorded_as_failed_mutation(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    complete_operation(tmp_path, "op-001", {"model_id": "pump-a"})
    with pytest.raises(ModelMatchingError) as exc_info:
        fail_operation(tmp_path, "op-001", "late_failure", "Too late.")
    assert exc_info.value.code == "operation_immutable"
    audits = mutation_failure_audits(tmp_path, "op-001")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "operation_immutable"
        and event["details"]["attempted_mutation"] == "operation.failed"
        for event in audits[0][1]
    )


def test_started_event_failure_is_audited_and_replays_deterministically(
    tmp_path, monkeypatch
):
    original_append = audit_module._append_operation_event_locked
    failed_once = {"value": False}

    def interrupted_append(
        project_root, operation_id, event_type, details, *args, **kwargs
    ):
        if event_type == "operation.started" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated started-event failure")
        return original_append(
            project_root, operation_id, event_type, details, *args, **kwargs
        )

    monkeypatch.setattr(
        audit_module, "_append_operation_event_locked", interrupted_append
    )
    arguments = {
        "operation_type": "model_asset.create",
        "principal": PRINCIPAL,
        "idempotency_key": "idem-001",
        "request_payload": {"model_id": "pump-a"},
    }
    with pytest.raises(OSError, match="simulated started-event failure"):
        start_operation(
            tmp_path,
            operation_id="op-001",
            request_id="request-001",
            **arguments,
        )
    failed = load_operation(tmp_path, "op-001")
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "operation_start_failed"
    assert [
        event["event_type"] for event in read_operation_events(tmp_path, "op-001")
    ] == ["operation.start_failed"]

    replayed_operation, replayed = start_operation(
        tmp_path,
        operation_id="op-002",
        request_id="request-002",
        **arguments,
    )
    assert replayed is True
    assert replayed_operation["status"] == "failed"
    assert read_operation_events(tmp_path, "op-001")[-1]["event_type"] == (
        "operation.replayed"
    )


@pytest.mark.parametrize("terminal_event", ["operation.completed", "operation.failed"])
def test_terminal_projection_failure_is_reconciled_without_duplicate_events(
    tmp_path, monkeypatch, terminal_event
):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    original_write_json = audit_module.write_json
    failed_once = {"value": False}
    terminal_status = terminal_event.removeprefix("operation.")

    def interrupted_write_json(payload, path):
        if payload.get("status") == terminal_status and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated terminal projection failure")
        return original_write_json(payload, path)

    monkeypatch.setattr(audit_module, "write_json", interrupted_write_json)
    if terminal_event == "operation.completed":
        transition = lambda: complete_operation(
            tmp_path, "op-001", {"model_id": "pump-a"}
        )
        opposite = lambda: fail_operation(
            tmp_path, "op-001", "late_failure", "Too late."
        )
    else:
        transition = lambda: fail_operation(
            tmp_path, "op-001", "invalid_model_geometry", "Mesh is empty."
        )
        opposite = lambda: complete_operation(
            tmp_path, "op-001", {"model_id": "pump-a"}
        )

    with pytest.raises(OSError, match="simulated terminal projection failure"):
        transition()
    assert load_operation(tmp_path, "op-001")["status"] == terminal_status
    terminal_events_before = [
        event
        for event in read_operation_events(tmp_path, "op-001")
        if event["event_type"] == terminal_event
    ]
    assert len(terminal_events_before) == 1
    for rejected_transition in (transition, opposite):
        with pytest.raises(ModelMatchingError) as exc_info:
            rejected_transition()
        assert exc_info.value.code == "operation_immutable"
    terminal_events_after = [
        event
        for event in read_operation_events(tmp_path, "op-001")
        if event["event_type"] == terminal_event
    ]
    assert terminal_events_after == terminal_events_before


def test_losing_claim_cleanup_requires_unstarted_unlocked_operation(tmp_path):
    operation, _ = start_operation(
        tmp_path, operation_id="op-loser", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    operation_root = (
        tmp_path / "reports" / "model_matching_operations" / "op-loser"
    )
    (operation_root / "events.jsonl").unlink()
    lock_path = operation_root / ".write.lock"
    lock_path.touch()
    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module._discard_unstarted_operation(
            tmp_path, "op-loser", operation["request_fingerprint"]
        )
    assert exc_info.value.code == "operation_busy"
    assert operation_root.is_dir()

    lock_path.unlink()
    append_operation_event(
        tmp_path,
        "op-loser",
        "operation.started",
        {"request_id": "request-001"},
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module._discard_unstarted_operation(
            tmp_path, "op-loser", operation["request_fingerprint"]
        )
    assert exc_info.value.code == "operation_immutable"
    assert operation_root.is_dir()


def test_busy_losing_claim_cleanup_is_audited_by_start_operation(
    tmp_path, monkeypatch
):
    original_claim = audit_module._claim_idempotency_index

    def lose_claim_while_locked(path, payload):
        if payload["operation_id"] != "op-loser":
            return original_claim(path, payload)
        original_claim(
            path,
            {
                "operation_id": "op-winner",
                "request_fingerprint": payload["request_fingerprint"],
            },
        )
        operation_root = (
            tmp_path / "reports" / "model_matching_operations" / "op-loser"
        )
        (operation_root / ".write.lock").touch()
        raise FileExistsError(path)

    monkeypatch.setattr(
        audit_module, "_claim_idempotency_index", lose_claim_while_locked
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-loser",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-001",
            idempotency_key="idem-001",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "operation_busy"
    audits = mutation_failure_audits(tmp_path, "op-loser")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "operation_busy"
        and event["details"]["attempted_mutation"] == "operation.started"
        for event in audits[0][1]
    )


def test_replay_and_conflict_use_attempting_principal_as_event_identity(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    bob = Principal("bob", frozenset({"operator"}), "configured_token")
    start_operation(
        tmp_path, operation_id="op-002", operation_type="model_asset.create",
        principal=bob, request_id="request-002", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    replay = read_operation_events(tmp_path, "op-001")[-1]
    assert replay["event_type"] == "operation.replayed"
    assert replay["actor_id"] == "bob"
    assert replay["roles"] == ["operator"]
    assert replay["principal_source"] == "configured_token"
    assert replay["details"]["actor_id"] == "bob"
    assert replay["details"]["roles"] == ["operator"]
    assert replay["details"]["principal_source"] == "configured_token"

    carol = Principal("carol", frozenset({"expert"}), "configured_token")
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path, operation_id="op-003", operation_type="model_asset.create",
            principal=carol, request_id="request-003", idempotency_key="idem-001",
            request_payload={"model_id": "pump-b"},
        )
    assert exc_info.value.code == "idempotency_conflict"
    conflict = read_operation_events(tmp_path, "op-001")[-1]
    assert conflict["event_type"] == "operation.idempotency_conflict"
    assert conflict["actor_id"] == "carol"
    assert conflict["roles"] == ["expert"]
    assert conflict["principal_source"] == "configured_token"
    assert conflict["details"]["actor_id"] == "carol"
    assert conflict["details"]["roles"] == ["expert"]
    assert conflict["details"]["principal_source"] == "configured_token"


@pytest.mark.parametrize(
    "failure_boundary",
    [
        "operation.started",
        "security.permission_denied",
        "operation.failed",
        "failed_projection",
    ],
)
def test_denied_audit_recovers_each_write_boundary(
    tmp_path, monkeypatch, failure_boundary
):
    original_append = audit_module._append_operation_event_locked
    original_write_json = audit_module.write_json
    failed_once = {"value": False}

    def interrupted_append(
        project_root, operation_id, event_type, details, *args, **kwargs
    ):
        if event_type == failure_boundary and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError(f"simulated {failure_boundary} failure")
        return original_append(
            project_root, operation_id, event_type, details, *args, **kwargs
        )

    def interrupted_write_json(payload, path):
        if (
            failure_boundary == "failed_projection"
            and payload.get("operation_type") == "security.permission_denied"
            and payload.get("status") == "failed"
            and not failed_once["value"]
        ):
            failed_once["value"] = True
            raise OSError("simulated failed_projection failure")
        return original_write_json(payload, path)

    monkeypatch.setattr(
        audit_module, "_append_operation_event_locked", interrupted_append
    )
    monkeypatch.setattr(audit_module, "write_json", interrupted_write_json)
    operation_id = record_denied_operation(
        tmp_path,
        request_id="request-denied-001",
        route="POST /model-library/models",
        token="secret-token",
        reason="permission_denied",
    )
    operation = load_operation(tmp_path, operation_id)
    events = read_operation_events(tmp_path, operation_id)
    assert operation["status"] == "failed"
    assert operation["error"]["code"] == "permission_denied"
    assert sum(
        event["event_type"] == "security.permission_denied" for event in events
    ) == 1
    assert sum(event["event_type"] == "operation.failed" for event in events) == 1
    assert verify_operation_chain(events) is True
    assert "secret-token" not in json.dumps(
        {"operation": operation, "events": events}
    )
