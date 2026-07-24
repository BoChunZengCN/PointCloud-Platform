import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

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
    with audit_module._operation_write_lock(tmp_path, "op-001"):
        with pytest.raises(ModelMatchingError) as exc_info:
            append_operation_event(
                tmp_path, "op-001", "model_asset.validated", {}
            )
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
        if payload["operation_id"] == "op-001":
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
        assert replay_finished.wait(timeout=2)
        with pytest.raises(ModelMatchingError) as exc_info:
            replay.result(timeout=2)
        assert exc_info.value.code == "operation_busy"
        release_claimant.set()
        claimant.result(timeout=2)
        replayed_operation, replayed = start_operation(
            tmp_path,
            operation_id="op-002",
            request_id="request-002",
            **arguments,
        )
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
    with audit_module._operation_write_lock(tmp_path, "op-loser"):
        with pytest.raises(ModelMatchingError) as exc_info:
            audit_module._discard_unstarted_operation(
                tmp_path, "op-loser", operation["request_fingerprint"]
            )
    assert exc_info.value.code == "operation_busy"
    assert operation_root.is_dir()

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
    original_discard = audit_module._discard_unstarted_operation

    def busy_discard(*args, **kwargs):
        raise ModelMatchingError(
            "operation_busy", "Operation is currently being updated."
        )

    monkeypatch.setattr(
        audit_module, "_discard_unstarted_operation", busy_discard
    )

    def lose_claim(path, payload):
        if payload["operation_id"] != "op-loser":
            return original_claim(path, payload)
        original_claim(
            path,
            {
                **payload,
                "operation_id": "op-winner",
                "initializer_owner_token": "winner-token",
            },
        )
        raise FileExistsError(path)

    monkeypatch.setattr(audit_module, "_claim_idempotency_index", lose_claim)
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
    monkeypatch.setattr(
        audit_module, "_discard_unstarted_operation", original_discard
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


def test_existing_operation_id_uses_stable_audited_error(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    operation_before = load_operation(tmp_path, "op-001")
    events_before = read_operation_events(tmp_path, "op-001")
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path, operation_id="op-001", operation_type="model_asset.create",
            principal=PRINCIPAL, request_id="request-002",
            idempotency_key="idem-002",
            request_payload={"model_id": "pump-b"},
        )
    assert exc_info.value.code == "operation_exists"
    assert load_operation(tmp_path, "op-001") == operation_before
    assert read_operation_events(tmp_path, "op-001") == events_before
    audits = mutation_failure_audits(tmp_path, "op-001")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "operation_exists"
        for event in audits[0][1]
    )


def test_initial_projection_failure_uses_stable_audited_error(
    tmp_path, monkeypatch
):
    original_write_json = audit_module.write_json
    failed_once = {"value": False}

    def interrupted_write_json(payload, path):
        if payload.get("operation_id") == "op-001" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated initial projection failure")
        return original_write_json(payload, path)

    monkeypatch.setattr(audit_module, "write_json", interrupted_write_json)
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path, operation_id="op-001", operation_type="model_asset.create",
            principal=PRINCIPAL, request_id="request-001",
            idempotency_key="idem-001",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "operation_persistence_failed"
    target_root = (
        tmp_path / "reports" / "model_matching_operations" / "op-001"
    )
    assert not target_root.exists()
    audits = mutation_failure_audits(tmp_path, "op-001")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "operation_persistence_failed"
        for event in audits[0][1]
    )


def test_restart_reconciles_claim_without_initial_event(tmp_path, monkeypatch):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-001"
        / "events.jsonl"
    )
    events_path.unlink()
    monotonic_values = iter([0.0, 3.0])
    monkeypatch.setattr(
        audit_module.time,
        "monotonic",
        lambda: next(monotonic_values, 3.0),
    )
    monkeypatch.setattr(audit_module.time, "sleep", lambda _seconds: None)

    operation, replayed = start_operation(
        tmp_path, operation_id="op-002", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-002", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is True
    assert operation["status"] == "failed"
    assert operation["error"]["code"] == "operation_start_interrupted"
    events = read_operation_events(tmp_path, "op-001")
    assert [event["event_type"] for event in events] == [
        "operation.start_failed",
        "operation.replayed",
    ]
    assert verify_operation_chain(events) is True


def _denied_recovery_paths(project_root):
    recovery_root = (
        project_root / "reports" / "model_matching_denied_recovery"
    )
    return sorted(recovery_root.glob("*.json")) if recovery_root.exists() else []


def _assert_recovered_denial(project_root, operation_id):
    operation = load_operation(project_root, operation_id)
    events = read_operation_events(project_root, operation_id)
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


def test_denied_restart_recovers_absent_initial_projection(tmp_path, monkeypatch):
    original_write_json = audit_module.write_json

    def unavailable_initial_projection(payload, path):
        if (
            payload.get("operation_type") == "security.permission_denied"
            and payload.get("status") == "running"
        ):
            raise OSError("simulated unavailable initial projection")
        return original_write_json(payload, path)

    monkeypatch.setattr(
        audit_module, "write_json", unavailable_initial_projection
    )
    with pytest.raises(Exception, match="projection"):
        record_denied_operation(
            tmp_path,
            request_id="request-denied-001",
            route="POST /model-library/models",
            token="secret-token",
            reason="permission_denied",
        )
    recovery_paths = _denied_recovery_paths(tmp_path)
    assert len(recovery_paths) == 1
    serialized_recovery = recovery_paths[0].read_text(encoding="utf-8")
    assert "secret-token" not in serialized_recovery

    monkeypatch.setattr(audit_module, "write_json", original_write_json)
    recovered = audit_module.recover_denied_operations(tmp_path)
    assert len(recovered) == 1
    _assert_recovered_denial(tmp_path, recovered[0])
    assert _denied_recovery_paths(tmp_path) == []


@pytest.mark.parametrize(
    "failure_boundary",
    [
        "security.permission_denied",
        "operation.failed",
        "failed_projection",
    ],
)
def test_denied_restart_recovers_partial_workflow(
    tmp_path, monkeypatch, failure_boundary
):
    original_append = audit_module._append_operation_event_locked
    original_write_json = audit_module.write_json

    def unavailable_event(
        project_root, operation_id, event_type, details, *args, **kwargs
    ):
        if event_type == failure_boundary:
            raise OSError(f"simulated persistent {failure_boundary} failure")
        return original_append(
            project_root, operation_id, event_type, details, *args, **kwargs
        )

    def unavailable_projection(payload, path):
        if (
            failure_boundary == "failed_projection"
            and payload.get("operation_type") == "security.permission_denied"
            and payload.get("status") == "failed"
        ):
            raise OSError("simulated persistent failed_projection failure")
        return original_write_json(payload, path)

    monkeypatch.setattr(
        audit_module, "_append_operation_event_locked", unavailable_event
    )
    monkeypatch.setattr(audit_module, "write_json", unavailable_projection)
    with pytest.raises(Exception, match="simulated persistent"):
        record_denied_operation(
            tmp_path,
            request_id="request-denied-001",
            route="POST /model-library/models",
            token="secret-token",
            reason="permission_denied",
        )
    recovery_paths = _denied_recovery_paths(tmp_path)
    assert len(recovery_paths) == 1
    operation_id = recovery_paths[0].stem

    monkeypatch.setattr(
        audit_module, "_append_operation_event_locked", original_append
    )
    monkeypatch.setattr(audit_module, "write_json", original_write_json)
    assert audit_module.recover_denied_operations(tmp_path) == [operation_id]
    _assert_recovered_denial(tmp_path, operation_id)
    assert _denied_recovery_paths(tmp_path) == []


def test_tampered_terminal_event_cannot_repair_projection(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    complete_operation(tmp_path, "op-001", {"model_id": "pump-a"})
    operation_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-001"
        / "operation.json"
    )
    stale_projection = json.loads(operation_path.read_text(encoding="utf-8"))
    stale_projection.update(
        {"status": "running", "completed_at": None, "result": None}
    )
    operation_path.write_text(
        json.dumps(stale_projection, indent=2), encoding="utf-8"
    )
    events_path = operation_path.with_name("events.jsonl")
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    events[-1]["details"]["result"]["model_id"] = "tampered"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )

    projection_before = operation_path.read_text(encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_operation(tmp_path, "op-001")
    assert exc_info.value.code == "audit_integrity_error"
    assert operation_path.read_text(encoding="utf-8") == projection_before


def test_lock_owner_does_not_unlink_new_owner_after_directory_aba(tmp_path):
    root = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "operation"
    )
    discarded = tmp_path / "discarded"
    root.mkdir(parents=True)

    with audit_module._operation_write_lock(tmp_path, "operation"):
        os.replace(root, discarded)
        root.mkdir()
        with pytest.raises(ModelMatchingError) as exc_info:
            with audit_module._operation_write_lock(tmp_path, "operation"):
                pass
        assert exc_info.value.code == "operation_busy"

    with audit_module._operation_write_lock(tmp_path, "operation"):
        pass
    assert (
        tmp_path
        / "reports"
        / "model_matching_locks"
        / "operation.lock"
    ).is_file()


def test_live_delayed_initializer_is_never_terminalized_by_timeout(
    tmp_path, monkeypatch
):
    index_claimed = Event()
    release_initializer = Event()
    original_claim = audit_module._claim_idempotency_index

    def delayed_claim(path, payload):
        original_claim(path, payload)
        if payload["operation_id"] == "op-live":
            index_claimed.set()
            assert release_initializer.wait(timeout=2)

    monkeypatch.setattr(audit_module, "_claim_idempotency_index", delayed_claim)
    monotonic_values = iter([0.0, 3.0, 3.0])
    monkeypatch.setattr(
        audit_module.time,
        "monotonic",
        lambda: next(monotonic_values, 3.0),
    )
    arguments = {
        "operation_type": "model_asset.create",
        "principal": PRINCIPAL,
        "idempotency_key": "idem-live",
        "request_payload": {"model_id": "pump-a"},
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        initializer = executor.submit(
            start_operation,
            tmp_path,
            operation_id="op-live",
            request_id="request-live-1",
            **arguments,
        )
        assert index_claimed.wait(timeout=2)
        replayer = executor.submit(
            start_operation,
            tmp_path,
            operation_id="op-replay",
            request_id="request-live-2",
            **arguments,
        )
        try:
            with pytest.raises(ModelMatchingError) as exc_info:
                replayer.result(timeout=2)
            assert exc_info.value.code == "operation_busy"
        finally:
            release_initializer.set()
        initializer.result(timeout=2)

    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-live")
    ] == ["operation.started"]


def test_abandoned_initializer_reclaims_stale_lock_without_waiting(
    tmp_path, monkeypatch
):
    start_operation(
        tmp_path,
        operation_id="op-abandoned",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-abandoned",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-abandoned"
        / "events.jsonl"
    )
    events_path.unlink()
    lock_path = (
        tmp_path
        / "reports"
        / "model_matching_locks"
        / "op-abandoned.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text('{"owner_token":"stale', encoding="utf-8")
    monkeypatch.setattr(
        audit_module.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("abandoned recovery must not wait on stale metadata")
        ),
    )

    operation, replayed = start_operation(
        tmp_path,
        operation_id="op-replay",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-002",
        idempotency_key="idem-abandoned",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is True
    assert operation["error"]["code"] == "operation_start_interrupted"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-abandoned")
    ] == ["operation.start_failed", "operation.replayed"]


@pytest.mark.parametrize("failure_boundary", ["write", "fsync"])
def test_lock_initialization_failure_is_reclaimable_and_audited(
    tmp_path, monkeypatch, failure_boundary
):
    capability_check = getattr(
        audit_module, "_require_audit_storage_capabilities", None
    )
    if capability_check is not None:
        capability_check(tmp_path)
    original_write = audit_module.os.write
    original_fsync = audit_module.os.fsync
    failed_once = {"value": False}
    fsync_calls = {"value": 0}

    def interrupted_write(descriptor, payload):
        if not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated lock metadata write failure")
        return original_write(descriptor, payload)

    def interrupted_fsync(descriptor):
        fsync_calls["value"] += 1
        if fsync_calls["value"] == 2 and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated lock metadata fsync failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(
        audit_module.os,
        "write" if failure_boundary == "write" else "fsync",
        interrupted_write if failure_boundary == "write" else interrupted_fsync,
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-lock-fault",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-001",
            idempotency_key="idem-lock-fault",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "audit_persistence_error"
    assert not (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-lock-fault"
    ).exists()
    audits = mutation_failure_audits(tmp_path, "op-lock-fault")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "audit_persistence_error"
        for event in audits[0][1]
    )

    operation, replayed = start_operation(
        tmp_path,
        operation_id="op-lock-fault",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-002",
        idempotency_key="idem-lock-fault",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is False
    assert operation["status"] == "running"


@pytest.mark.parametrize(
    "attempted_mutation",
    ["model_asset.validated", "operation.completed", "operation.failed"],
)
def test_integrity_rejections_are_separately_audited(
    tmp_path, attempted_mutation
):
    start_operation(
        tmp_path,
        operation_id="op-corrupt",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-corrupt",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-corrupt"
        / "events.jsonl"
    )
    serialized_before = events_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in serialized_before.splitlines()]
    events[0]["details"]["request_id"] = "tampered"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    corrupt_before = events_path.read_text(encoding="utf-8")

    if attempted_mutation == "operation.completed":
        mutation = lambda: complete_operation(
            tmp_path, "op-corrupt", {"model_id": "pump-a"}
        )
    elif attempted_mutation == "operation.failed":
        mutation = lambda: fail_operation(
            tmp_path, "op-corrupt", "late_failure", "Too late."
        )
    else:
        mutation = lambda: append_operation_event(
            tmp_path, "op-corrupt", attempted_mutation, {}
        )
    with pytest.raises(ModelMatchingError) as exc_info:
        mutation()
    assert exc_info.value.code == "audit_integrity_error"
    assert events_path.read_text(encoding="utf-8") == corrupt_before
    audits = mutation_failure_audits(tmp_path, "op-corrupt")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "audit_integrity_error"
        and event["details"]["attempted_mutation"] == attempted_mutation
        for event in audits[0][1]
    )


def test_started_after_terminal_is_rejected_and_audited(tmp_path):
    start_operation(
        tmp_path,
        operation_id="op-terminal",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-terminal",
        request_payload={"model_id": "pump-a"},
    )
    complete_operation(tmp_path, "op-terminal", {"model_id": "pump-a"})
    events_before = read_operation_events(tmp_path, "op-terminal")
    with pytest.raises(ModelMatchingError) as exc_info:
        append_operation_event(
            tmp_path,
            "op-terminal",
            "operation.started",
            {"request_id": "late"},
        )
    assert exc_info.value.code == "operation_immutable"
    assert read_operation_events(tmp_path, "op-terminal") == events_before
    audits = mutation_failure_audits(tmp_path, "op-terminal")
    assert len(audits) == 1


def test_hash_valid_contradictory_lifecycle_fails_integrity(tmp_path):
    start_operation(
        tmp_path,
        operation_id="op-lifecycle",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-lifecycle",
        request_payload={"model_id": "pump-a"},
    )
    fail_operation(tmp_path, "op-lifecycle", "failed", "Failed.")
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-lifecycle"
        / "events.jsonl"
    )
    events = read_operation_events(tmp_path, "op-lifecycle")
    contradictory = {
        **events[0],
        "event_id": "contradictory-start",
        "sequence": len(events) + 1,
        "timestamp": audit_module.utc_now(),
        "previous_event_hash": events[-1]["event_hash"],
    }
    contradictory["event_hash"] = audit_module._event_hash(contradictory)
    events.append(contradictory)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    assert audit_module.verify_operation_chain(events) is False
    with pytest.raises(ModelMatchingError) as exc_info:
        load_operation(tmp_path, "op-lifecycle")
    assert exc_info.value.code == "audit_integrity_error"


def test_idempotency_claim_io_failure_cleans_and_audits(
    tmp_path, monkeypatch
):
    original_claim = audit_module._claim_idempotency_index
    failed_once = {"value": False}

    def interrupted_claim(path, payload):
        if payload.get("operation_id") == "op-index-fault" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated index publication failure")
        return original_claim(path, payload)

    monkeypatch.setattr(
        audit_module, "_claim_idempotency_index", interrupted_claim
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-index-fault",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-001",
            idempotency_key="idem-index-fault",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "audit_persistence_error"
    assert not (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-index-fault"
    ).exists()
    assert not audit_module._idempotency_path(
        tmp_path, "idem-index-fault"
    ).exists()
    audits = mutation_failure_audits(tmp_path, "op-index-fault")
    assert len(audits) == 1


def test_misdirected_index_is_audited_without_terminalizing_target(
    tmp_path, monkeypatch
):
    op_a, _ = start_operation(
        tmp_path,
        operation_id="op-a",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-a",
        idempotency_key="idem-a",
        request_payload={"model_id": "pump-a"},
    )
    op_b, _ = start_operation(
        tmp_path,
        operation_id="op-b",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-b",
        idempotency_key="idem-b",
        request_payload={"model_id": "pump-b"},
    )
    index_path = audit_module._idempotency_path(tmp_path, "idem-a")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["operation_id"] = "op-b"
    index["request_fingerprint"] = op_a["request_fingerprint"]
    index_path.write_text(json.dumps(index), encoding="utf-8")
    events_b_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-b"
        / "events.jsonl"
    )
    events_b_path.unlink()
    monotonic_values = iter([0.0, 3.0])
    monkeypatch.setattr(
        audit_module.time,
        "monotonic",
        lambda: next(monotonic_values, 3.0),
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-retry",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-retry",
            idempotency_key="idem-a",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "audit_integrity_error"
    assert read_operation_events(tmp_path, "op-b") == []
    assert load_operation(tmp_path, "op-b")["status"] == "running"
    audits = mutation_failure_audits(tmp_path, "op-retry")
    assert len(audits) == 1
    assert op_b["request_fingerprint"] != op_a["request_fingerprint"]


def test_concurrent_denied_recovery_claims_marker_once(
    tmp_path, monkeypatch
):
    original_recover = audit_module._recover_denied_entry

    def leave_marker(*_args, **_kwargs):
        raise OSError("leave marker for restart")

    monkeypatch.setattr(audit_module, "_recover_denied_entry", leave_marker)
    with pytest.raises(OSError, match="leave marker"):
        record_denied_operation(
            tmp_path,
            request_id="request-denied-001",
            route="POST /model-library/models",
            token="secret-token",
            reason="permission_denied",
        )
    marker = _denied_recovery_paths(tmp_path)[0]
    operation_id = marker.stem
    first_entered = Event()
    release_first = Event()
    calls_lock = Lock()
    calls = {"value": 0}

    def delayed_recover(*args, **kwargs):
        with calls_lock:
            calls["value"] += 1
            call_number = calls["value"]
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        return original_recover(*args, **kwargs)

    monkeypatch.setattr(audit_module, "_recover_denied_entry", delayed_recover)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(audit_module.recover_denied_operations, tmp_path)
        assert first_entered.wait(timeout=2)
        second = executor.submit(audit_module.recover_denied_operations, tmp_path)
        assert second.result(timeout=2) == []
        release_first.set()
        assert first.result(timeout=2) == [operation_id]
    assert calls["value"] == 1
    _assert_recovered_denial(tmp_path, operation_id)
    assert _denied_recovery_paths(tmp_path) == []


def test_corrupt_denied_marker_is_reported_without_blocking_later_marker(
    tmp_path, monkeypatch
):
    recovery_root = (
        tmp_path / "reports" / "model_matching_denied_recovery"
    )
    recovery_root.mkdir(parents=True)
    corrupt = recovery_root / "000-corrupt.json"
    corrupt.write_text('{"operation_id":', encoding="utf-8")
    original_recover = audit_module._recover_denied_entry
    monkeypatch.setattr(
        audit_module,
        "_recover_denied_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("leave valid marker")
        ),
    )
    with pytest.raises(OSError, match="leave valid marker"):
        record_denied_operation(
            tmp_path,
            request_id="request-denied-001",
            route="POST /model-library/models",
            token="secret-token",
            reason="permission_denied",
        )
    valid_marker = next(
        path for path in _denied_recovery_paths(tmp_path) if path != corrupt
    )
    monkeypatch.setattr(
        audit_module, "_recover_denied_entry", original_recover
    )

    recovered = audit_module.recover_denied_operations(tmp_path)
    assert recovered == [valid_marker.stem]
    _assert_recovered_denial(tmp_path, valid_marker.stem)
    assert corrupt.exists()
    error_reports = list((recovery_root / "errors").glob("*.json"))
    assert len(error_reports) == 1
    report = json.loads(error_reports[0].read_text(encoding="utf-8"))
    assert report["code"] == "audit_integrity_error"
    audits_before = mutation_failure_audits(tmp_path, "000-corrupt")
    assert len(audits_before) == 1

    assert audit_module.recover_denied_operations(tmp_path) == []
    assert len(mutation_failure_audits(tmp_path, "000-corrupt")) == 1


def test_idempotency_publish_winner_race_never_overwrites(tmp_path):
    path = audit_module._idempotency_path(tmp_path, "idem-race")
    payloads = [
        {"operation_id": "op-a", "request_fingerprint": "a"},
        {"operation_id": "op-b", "request_fingerprint": "b"},
    ]

    def claim(payload):
        try:
            audit_module._claim_idempotency_index(path, payload)
        except FileExistsError:
            return "lost"
        return "won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(claim, payloads))
    assert sorted(outcomes) == ["lost", "won"]
    assert json.loads(path.read_text(encoding="utf-8")) in payloads


def test_partial_temp_leftover_is_invisible_before_publication(tmp_path):
    path = audit_module._idempotency_path(tmp_path, "idem-temp")
    path.parent.mkdir(parents=True)
    leftover = path.parent / f".{path.name}.tmp-crashed"
    leftover.write_text('{"operation_id":', encoding="utf-8")
    assert not path.exists()
    payload = {"operation_id": "op-complete", "request_fingerprint": "complete"}
    audit_module._claim_idempotency_index(path, payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert leftover.read_text(encoding="utf-8") == '{"operation_id":'


def test_crash_after_publish_leaves_complete_destination_visible(tmp_path):
    path = audit_module._idempotency_path(tmp_path, "idem-linked")
    path.parent.mkdir(parents=True)
    leftover = path.parent / f".{path.name}.tmp-crashed"
    payload = {"operation_id": "op-complete", "request_fingerprint": "complete"}
    leftover.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.link(leftover, path)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    with pytest.raises(FileExistsError):
        audit_module._claim_idempotency_index(
            path,
            {"operation_id": "op-other", "request_fingerprint": "other"},
        )
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert leftover.exists()


def test_temp_cleanup_failure_does_not_mask_published_index(
    tmp_path, monkeypatch
):
    path = audit_module._idempotency_path(tmp_path, "idem-cleanup")
    payload = {"operation_id": "op-complete", "request_fingerprint": "complete"}
    original_unlink = audit_module.Path.unlink

    def interrupted_unlink(candidate, *args, **kwargs):
        if candidate.parent == path.parent and candidate.name.startswith(".tmp-"):
            raise OSError("simulated post-publication cleanup interruption")
        return original_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(audit_module.Path, "unlink", interrupted_unlink)
    audit_module._claim_idempotency_index(path, payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert list(path.parent.glob(".tmp-*"))


def test_unsupported_hardlink_fails_closed_without_partial_index(
    tmp_path, monkeypatch
):
    audit_module._require_audit_storage_capabilities(tmp_path)
    original_link = audit_module.os.link

    def unsupported_link(*_args, **_kwargs):
        raise OSError("hard links unsupported")

    monkeypatch.setattr(audit_module.os, "link", unsupported_link)
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-no-link",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-001",
            idempotency_key="idem-no-link",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "audit_persistence_error"
    assert not audit_module._idempotency_path(tmp_path, "idem-no-link").exists()
    assert not list(
        (
            tmp_path / "reports" / "model_matching_idempotency"
        ).glob(".tmp-*")
    )
    monkeypatch.setattr(audit_module.os, "link", original_link)


def test_capability_probe_failure_is_not_cached_and_is_stable(
    tmp_path, monkeypatch
):
    original_probe = audit_module._probe_audit_storage_capabilities
    probe_calls = {"value": 0}

    def fail_once(project_root):
        probe_calls["value"] += 1
        if probe_calls["value"] == 1:
            raise OSError("simulated capability failure")
        return original_probe(project_root)

    monkeypatch.setattr(
        audit_module, "_probe_audit_storage_capabilities", fail_once
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-probe",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-001",
            idempotency_key="idem-probe",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "audit_persistence_error"
    assert probe_calls["value"] >= 2
    audits = mutation_failure_audits(tmp_path, "op-probe")
    assert len(audits) == 1
