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
