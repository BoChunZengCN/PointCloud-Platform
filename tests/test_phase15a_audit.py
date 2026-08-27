import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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


class _UntrustedText(str):
    def encode(self, *args, **kwargs):
        raise RuntimeError("raw-secret-boom")


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


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("request_id", "../raw-request-secret"),
        ("idempotency_key", "../raw-idempotency-secret"),
        ("request_id", _UntrustedText("request-derived")),
        ("idempotency_key", _UntrustedText("idem-derived")),
    ],
    ids=[
        "unsafe-request-path",
        "unsafe-idempotency-path",
        "request-str-subclass",
        "idempotency-str-subclass",
    ],
)
def test_invalid_start_request_identifiers_are_independently_audited(
    tmp_path, field, invalid_value
):
    arguments = {
        "operation_id": "op-invalid-audit-request",
        "operation_type": "model_asset.create",
        "principal": PRINCIPAL,
        "request_id": "request-valid",
        "idempotency_key": "idem-valid",
        "request_payload": {"model_id": "pump-a"},
    }
    arguments[field] = invalid_value

    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(tmp_path, **arguments)

    assert exc_info.value.code == "invalid_audit_request"
    assert invalid_value not in str(exc_info.value)
    audits = mutation_failure_audits(
        tmp_path, "op-invalid-audit-request"
    )
    assert len(audits) == 1
    operation, events = audits[0]
    serialized = json.dumps(
        {"operation": operation, "events": events},
        ensure_ascii=True,
    )
    assert operation["status"] == "failed"
    assert operation["error"]["code"] == "invalid_audit_request"
    assert events[1]["event_type"] == "operation.mutation_rejected"
    assert events[1]["details"] == {
        "target_operation_id": "op-invalid-audit-request",
        "attempted_mutation": "operation.started",
        "code": "invalid_audit_request",
        "message": "Audit request identifiers are invalid.",
    }
    assert invalid_value not in serialized


def test_invalid_start_request_audit_failure_fails_closed(
    tmp_path, monkeypatch
):
    def interrupt_failure_audit(*args, **kwargs):
        raise OSError("simulated invalid request audit interruption")

    monkeypatch.setattr(
        audit_module, "_record_failed_mutation", interrupt_failure_audit
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-invalid-audit-request",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="../raw-request-secret",
            idempotency_key="idem-valid",
            request_payload={"model_id": "pump-a"},
        )

    assert exc_info.value.code == "audit_persistence_error"


def test_tampered_event_breaks_verification(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    events = read_operation_events(tmp_path, "op-001")
    events[0]["details"]["request_id"] = "changed"
    assert verify_operation_chain(events) is False


def test_verified_operation_event_snapshot_rejects_tampered_chain(tmp_path):
    start_operation(
        tmp_path,
        operation_id="op-001",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    complete_operation(tmp_path, "op-001", {"model_id": "pump-a"})
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-001"
        / "events.jsonl"
    )
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    events[0]["details"]["request_id"] = "tampered"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module.read_verified_operation_events(tmp_path, "op-001")

    assert exc_info.value.code == "audit_integrity_error"


def test_verified_operation_event_snapshot_has_bounded_busy_result(
    tmp_path, monkeypatch
):
    start_operation(
        tmp_path,
        operation_id="op-001",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    clock = iter([0.0, 2.0])

    @contextmanager
    def always_busy(*args, **kwargs):
        raise ModelMatchingError(
            "operation_busy", "Operation is currently being updated."
        )
        yield

    monkeypatch.setattr(audit_module, "_operation_write_lock", always_busy)
    monkeypatch.setattr(
        audit_module.time, "monotonic", lambda: next(clock)
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module.read_verified_operation_events(tmp_path, "op-001")

    assert exc_info.value.code == "operation_busy"


@pytest.mark.parametrize(
    "terminal_state", ["running", "completed", "failed"]
)
def test_verified_operation_snapshot_matches_verified_lifecycle(
    tmp_path, terminal_state
):
    read_snapshot = audit_module.read_verified_operation_snapshot
    start_operation(
        tmp_path,
        operation_id="op-snapshot",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-snapshot",
        idempotency_key="idem-snapshot",
        request_payload={"model_id": "pump-a"},
    )
    if terminal_state == "completed":
        complete_operation(
            tmp_path,
            "op-snapshot",
            {"model_id": "pump-a", "metrics": {"faces": 12}},
        )
    elif terminal_state == "failed":
        fail_operation(
            tmp_path,
            "op-snapshot",
            "invalid_model_geometry",
            "Mesh is empty.",
        )

    snapshot = read_snapshot(tmp_path, "op-snapshot")

    assert set(snapshot) == {"operation", "events"}
    operation = snapshot["operation"]
    events = snapshot["events"]
    assert operation["status"] == terminal_state
    assert operation["actor_id"] == events[0]["actor_id"] == "alice"
    assert operation["roles"] == events[0]["roles"] == ["expert"]
    assert operation["principal_source"] == events[0]["principal_source"]
    assert operation["request_id"] == events[0]["details"]["request_id"]
    assert (
        operation["request_fingerprint"]
        == events[0]["details"]["request_fingerprint"]
    )
    if terminal_state == "running":
        assert [event["event_type"] for event in events] == [
            "operation.started"
        ]
        assert operation["completed_at"] is None
        assert operation["result"] is None
        assert operation["error"] is None
    elif terminal_state == "completed":
        terminal = events[-1]
        assert terminal["event_type"] == "operation.completed"
        assert operation["completed_at"] == terminal["timestamp"]
        assert operation["result"] == terminal["details"]["result"] == {
            "model_id": "pump-a",
            "metrics": {"faces": 12},
        }
        assert operation["error"] is None
    else:
        terminal = events[-1]
        assert terminal["event_type"] == "operation.failed"
        assert operation["completed_at"] == terminal["timestamp"]
        assert operation["result"] is None
        assert operation["error"] == terminal["details"] == {
            "code": "invalid_model_geometry",
            "message": "Mesh is empty.",
        }


@pytest.mark.parametrize(
    "tamper_case",
    [
        "actor_id",
        "request_id",
        "completed_at",
        "result",
        "error",
    ],
)
def test_verified_operation_snapshot_rejects_projection_tamper(
    tmp_path, tamper_case
):
    read_snapshot = audit_module.read_verified_operation_snapshot
    start_operation(
        tmp_path,
        operation_id="op-projection-tamper",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-projection-tamper",
        idempotency_key="idem-projection-tamper",
        request_payload={"model_id": "pump-a"},
    )
    if tamper_case == "error":
        fail_operation(
            tmp_path,
            "op-projection-tamper",
            "invalid_model_geometry",
            "Mesh is empty.",
        )
    elif tamper_case in {"completed_at", "result"}:
        complete_operation(
            tmp_path,
            "op-projection-tamper",
            {"model_id": "pump-a"},
        )
    projection_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-projection-tamper"
        / "operation.json"
    )
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    forged_values = {
        "actor_id": "mallory",
        "request_id": "request-forged",
        "completed_at": "2030-01-01T00:00:00+00:00",
        "result": {"model_id": "forged"},
        "error": {"code": "forged", "message": "Forged."},
    }
    projection[tamper_case] = forged_values[tamper_case]
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    tampered_bytes = projection_path.read_bytes()

    with pytest.raises(ModelMatchingError) as exc_info:
        read_snapshot(tmp_path, "op-projection-tamper")

    assert exc_info.value.code == "audit_integrity_error"
    assert projection_path.read_bytes() == tampered_bytes


def test_verified_operation_snapshot_rejects_tampered_events(tmp_path):
    read_snapshot = audit_module.read_verified_operation_snapshot
    start_operation(
        tmp_path,
        operation_id="op-event-tamper",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-event-tamper",
        idempotency_key="idem-event-tamper",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-event-tamper"
        / "events.jsonl"
    )
    event = json.loads(events_path.read_text(encoding="utf-8"))
    event["details"]["request_id"] = "request-forged"
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    tampered_bytes = events_path.read_bytes()

    with pytest.raises(ModelMatchingError) as exc_info:
        read_snapshot(tmp_path, "op-event-tamper")

    assert exc_info.value.code == "audit_integrity_error"
    assert events_path.read_bytes() == tampered_bytes


@pytest.mark.parametrize("ledger_state", ["missing", "empty"])
def test_verified_operation_snapshot_rejects_missing_start_evidence(
    tmp_path, ledger_state
):
    start_operation(
        tmp_path,
        operation_id="op-missing-start",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-missing-start",
        idempotency_key="idem-missing-start",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-missing-start"
        / "events.jsonl"
    )
    if ledger_state == "missing":
        events_path.unlink()
    else:
        events_path.write_text("", encoding="utf-8")

    assert read_operation_events(tmp_path, "op-missing-start") == []
    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module.read_verified_operation_snapshot(
            tmp_path, "op-missing-start"
        )

    assert exc_info.value.code == "audit_integrity_error"


def test_verified_operation_snapshot_rejects_projection_without_initializer(
    tmp_path,
):
    start_operation(
        tmp_path,
        operation_id="op-projection-only",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-projection-only",
        idempotency_key="idem-projection-only",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-projection-only"
        / "events.jsonl"
    )
    events_path.unlink()
    audit_module._operation_lock_path(
        tmp_path, "op-projection-only"
    ).unlink()

    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module.read_verified_operation_snapshot(
            tmp_path, "op-projection-only"
        )

    assert exc_info.value.code == "audit_integrity_error"


def test_verified_operation_snapshot_reports_live_initializer_busy(
    tmp_path, monkeypatch
):
    initializer_locked = Event()
    release_initializer = Event()
    original_append_locked = audit_module._append_operation_event_locked
    original_monotonic = audit_module.time.monotonic

    def pause_initializer_before_start_event(
        project_root,
        operation_id,
        event_type,
        details,
        principal=None,
    ):
        if (
            operation_id == "op-live-initializer"
            and event_type == "operation.started"
        ):
            initializer_locked.set()
            assert release_initializer.wait(timeout=2)
        return original_append_locked(
            project_root,
            operation_id,
            event_type,
            details,
            principal,
        )

    monkeypatch.setattr(
        audit_module,
        "_append_operation_event_locked",
        pause_initializer_before_start_event,
    )

    def initialize_operation():
        return start_operation(
            tmp_path,
            operation_id="op-live-initializer",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-live-initializer",
            idempotency_key="idem-live-initializer",
            request_payload={"model_id": "pump-a"},
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        initializer = executor.submit(initialize_operation)
        assert initializer_locked.wait(timeout=2)
        clock = iter([0.0, 2.0])
        monkeypatch.setattr(
            audit_module.time, "monotonic", lambda: next(clock)
        )
        try:
            with pytest.raises(ModelMatchingError) as exc_info:
                audit_module.read_verified_operation_snapshot(
                    tmp_path, "op-live-initializer"
                )
            assert exc_info.value.code == "operation_busy"
        finally:
            monkeypatch.setattr(
                audit_module.time, "monotonic", original_monotonic
            )
            release_initializer.set()
        operation, replayed = initializer.result(timeout=2)

    assert replayed is False
    assert operation["status"] == "running"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-live-initializer")
    ] == ["operation.started"]


def test_initial_projection_visibility_is_guarded_by_initializer_lock(
    tmp_path, monkeypatch
):
    projection_written = Event()
    release_projection_write = Event()
    original_write_json = audit_module.write_json
    original_monotonic = audit_module.time.monotonic

    def pause_after_initial_projection_write(payload, path):
        result = original_write_json(payload, path)
        if (
            payload.get("operation_id") == "op-projection-window"
            and payload.get("status") == "running"
        ):
            projection_written.set()
            assert release_projection_write.wait(timeout=2)
        return result

    monkeypatch.setattr(
        audit_module, "write_json", pause_after_initial_projection_write
    )

    def initialize_operation():
        return start_operation(
            tmp_path,
            operation_id="op-projection-window",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-projection-window",
            idempotency_key="idem-projection-window",
            request_payload={"model_id": "pump-a"},
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        initializer = executor.submit(initialize_operation)
        assert projection_written.wait(timeout=2)
        clock = iter([0.0, 2.0])
        monkeypatch.setattr(
            audit_module.time, "monotonic", lambda: next(clock)
        )
        try:
            with pytest.raises(ModelMatchingError) as exc_info:
                audit_module.read_verified_operation_snapshot(
                    tmp_path, "op-projection-window"
                )
            assert exc_info.value.code == "operation_busy"
        finally:
            monkeypatch.setattr(
                audit_module.time, "monotonic", original_monotonic
            )
            release_projection_write.set()
        operation, replayed = initializer.result(timeout=2)

    assert replayed is False
    assert operation["status"] == "running"
    snapshot = audit_module.read_verified_operation_snapshot(
        tmp_path, "op-projection-window"
    )
    assert snapshot["operation"]["status"] == "running"
    assert [event["event_type"] for event in snapshot["events"]] == [
        "operation.started"
    ]


def test_verified_operation_snapshot_rejects_terminal_without_start(tmp_path):
    start_operation(
        tmp_path,
        operation_id="op-terminal-only",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-terminal-only",
        idempotency_key="idem-terminal-only",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-terminal-only"
        / "events.jsonl"
    )
    terminal = {
        "schema_version": "1.0",
        "event_id": "forged-terminal",
        "operation_id": "op-terminal-only",
        "sequence": 1,
        "event_type": "operation.completed",
        "timestamp": audit_module.utc_now(),
        "actor_id": "alice",
        "roles": ["expert"],
        "principal_source": "configured_token",
        "previous_event_hash": None,
        "details": {"result": {"model_id": "pump-a"}},
    }
    terminal["event_hash"] = audit_module._event_hash(terminal)
    events_path.write_text(json.dumps(terminal) + "\n", encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module.read_verified_operation_snapshot(
            tmp_path, "op-terminal-only"
        )

    assert exc_info.value.code == "audit_integrity_error"


def test_verified_operation_snapshot_returns_fresh_json_safe_values(tmp_path):
    read_snapshot = audit_module.read_verified_operation_snapshot
    start_operation(
        tmp_path,
        operation_id="op-fresh-snapshot",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-fresh-snapshot",
        idempotency_key="idem-fresh-snapshot",
        request_payload={"model_id": "pump-a"},
    )
    complete_operation(
        tmp_path,
        "op-fresh-snapshot",
        {"model_id": "pump-a", "metrics": {"faces": 12}},
    )

    first = read_snapshot(tmp_path, "op-fresh-snapshot")
    json.dumps(first)
    first["operation"]["result"]["metrics"]["faces"] = -1
    first["events"][0]["details"]["request_id"] = "mutated"
    second = read_snapshot(tmp_path, "op-fresh-snapshot")

    assert second["operation"]["result"]["metrics"]["faces"] == 12
    assert second["events"][0]["details"]["request_id"] == (
        "request-fresh-snapshot"
    )


def test_verified_operation_snapshot_maps_invalid_identifier_stably(tmp_path):
    read_snapshot = audit_module.read_verified_operation_snapshot

    with pytest.raises(ModelMatchingError) as exc_info:
        read_snapshot(tmp_path, "../invalid-operation")

    assert exc_info.value.code == "invalid_audit_request"
    assert "../invalid-operation" not in str(exc_info.value)
    assert not (tmp_path / "reports").exists()


def test_verified_operation_snapshot_preserves_bounded_busy_result(
    tmp_path, monkeypatch
):
    read_snapshot = audit_module.read_verified_operation_snapshot
    start_operation(
        tmp_path,
        operation_id="op-snapshot-busy",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-snapshot-busy",
        idempotency_key="idem-snapshot-busy",
        request_payload={"model_id": "pump-a"},
    )
    clock = iter([0.0, 2.0])

    @contextmanager
    def always_busy(*args, **kwargs):
        raise ModelMatchingError(
            "operation_busy", "Operation is currently being updated."
        )
        yield

    monkeypatch.setattr(audit_module, "_operation_write_lock", always_busy)
    monkeypatch.setattr(
        audit_module.time, "monotonic", lambda: next(clock)
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        read_snapshot(tmp_path, "op-snapshot-busy")

    assert exc_info.value.code == "operation_busy"


def test_verified_operation_snapshot_never_mixes_concurrent_terminal_append(
    tmp_path, monkeypatch
):
    read_snapshot = audit_module.read_verified_operation_snapshot
    start_operation(
        tmp_path,
        operation_id="op-concurrent-snapshot",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-concurrent-snapshot",
        idempotency_key="idem-concurrent-snapshot",
        request_payload={"model_id": "pump-a"},
    )
    event_read_entered = Event()
    allow_event_read = Event()
    first_writer_attempt_done = Event()
    writer_contended = Event()
    retry_writer = Event()
    writer_completed = Event()
    original_read_events_locked = (
        audit_module._read_verified_operation_events_locked
    )

    def pause_before_real_event_read(project_root, operation_id):
        if operation_id == "op-concurrent-snapshot":
            event_read_entered.set()
            assert allow_event_read.wait(timeout=2)
        return original_read_events_locked(project_root, operation_id)

    monkeypatch.setattr(
        audit_module,
        "_read_verified_operation_events_locked",
        pause_before_real_event_read,
    )

    def complete_after_snapshot_unlocks():
        try:
            completed = complete_operation(
                tmp_path,
                "op-concurrent-snapshot",
                {"model_id": "pump-a"},
                _audit_rejection=False,
            )
        except ModelMatchingError as exc:
            assert exc.code == "operation_busy"
            writer_contended.set()
            first_writer_attempt_done.set()
            assert retry_writer.wait(timeout=2)
            completed = complete_operation(
                tmp_path,
                "op-concurrent-snapshot",
                {"model_id": "pump-a"},
                _audit_rejection=False,
            )
        else:
            first_writer_attempt_done.set()
        writer_completed.set()
        return completed

    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(
            read_snapshot, tmp_path, "op-concurrent-snapshot"
        )
        assert event_read_entered.wait(timeout=2)
        writer = executor.submit(complete_after_snapshot_unlocks)
        try:
            assert first_writer_attempt_done.wait(timeout=2)
            assert writer_contended.is_set()
            assert not writer_completed.is_set()
            allow_event_read.set()
            before_terminal = reader.result(timeout=2)
            assert before_terminal["operation"]["status"] == "running"
            assert [
                event["event_type"] for event in before_terminal["events"]
            ] == ["operation.started"]
            retry_writer.set()
            completed = writer.result(timeout=2)
        finally:
            allow_event_read.set()
            retry_writer.set()

    assert writer_completed.is_set()
    assert completed["status"] == "completed"
    after_terminal = read_snapshot(tmp_path, "op-concurrent-snapshot")
    assert after_terminal["operation"]["status"] == "completed"
    assert after_terminal["operation"]["result"] == (
        after_terminal["events"][-1]["details"]["result"]
    )


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
        result = original_claim(path, payload)
        if payload["operation_id"] == "op-001":
            index_claimed.set()
            assert release_claimant.wait(timeout=2)
        return result

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
        release_claimant.set()
        claimant.result(timeout=2)
        replayed_operation, replayed = replay.result(timeout=2)
        assert replay_entered.wait(timeout=2)
        assert replay_finished.wait(timeout=2)
    assert replayed is True
    assert replayed_operation["operation_id"] == "op-001"
    assert [
        event["event_type"] for event in read_operation_events(tmp_path, "op-001")
    ] == ["operation.started", "operation.replayed"]
    assert [
        event["event_type"] for event in read_operation_events(tmp_path, "op-002")
    ] == ["operation.start_failed"]


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
    sensitive_error = "simulated started-event failure secret-token C:\\private"

    def interrupted_append(
        project_root, operation_id, event_type, details, *args, **kwargs
    ):
        if event_type == "operation.started" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError(sensitive_error)
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
    persisted = json.dumps(
        {
            "operation": failed,
            "events": read_operation_events(tmp_path, "op-001"),
        }
    )
    assert "secret-token" not in persisted
    assert "C:\\private" not in persisted
    with pytest.raises(ModelMatchingError) as exc_info:
        audit_module.read_verified_operation_snapshot(tmp_path, "op-001")
    assert exc_info.value.code == "audit_integrity_error"

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


def test_durable_started_event_wins_over_post_append_exception(
    tmp_path, monkeypatch
):
    original_append = audit_module._append_operation_event_locked
    interrupted = {"value": False}

    def append_then_interrupt(
        project_root, operation_id, event_type, details, *args, **kwargs
    ):
        event = original_append(
            project_root, operation_id, event_type, details, *args, **kwargs
        )
        if event_type == "operation.started" and not interrupted["value"]:
            interrupted["value"] = True
            raise OSError("simulated post-append visibility exception")
        return event

    monkeypatch.setattr(
        audit_module, "_append_operation_event_locked", append_then_interrupt
    )
    operation, replayed = start_operation(
        tmp_path,
        operation_id="op-post-append",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-post-append",
        idempotency_key="idem-post-append",
        request_payload={"model_id": "pump-a"},
    )

    assert replayed is False
    assert operation["status"] == "running"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-post-append")
    ] == ["operation.started"]


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


def test_existing_operation_id_mismatch_uses_stable_integrity_error(tmp_path):
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
    assert exc_info.value.code == "audit_integrity_error"
    assert load_operation(tmp_path, "op-001") == operation_before
    assert read_operation_events(tmp_path, "op-001") == events_before
    audits = mutation_failure_audits(tmp_path, "op-001")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "audit_integrity_error"
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
    assert target_root.is_dir()
    assert list(target_root.iterdir()) == []
    audits = mutation_failure_audits(tmp_path, "op-001")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["code"] == "operation_persistence_failed"
        for event in audits[0][1]
    )
    operation, replayed = start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001",
        idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is False
    assert operation["status"] == "running"


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


def test_retry_recovers_matching_projection_before_index_once(
    tmp_path, monkeypatch
):
    original_claim = audit_module._claim_idempotency_index
    interrupted = {"value": False}

    def interrupt_before_index(path, payload):
        if not interrupted["value"]:
            interrupted["value"] = True
            raise KeyboardInterrupt("simulated process interruption")
        return original_claim(path, payload)

    monkeypatch.setattr(
        audit_module, "_claim_idempotency_index", interrupt_before_index
    )
    arguments = {
        "operation_id": "op-projection-recovery",
        "operation_type": "model_asset.create",
        "principal": PRINCIPAL,
        "request_id": "request-projection-recovery",
        "idempotency_key": "idem-projection-recovery",
        "request_payload": {"model_id": "pump-a"},
    }
    with pytest.raises(KeyboardInterrupt, match="process interruption"):
        start_operation(tmp_path, **arguments)

    root = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-projection-recovery"
    )
    assert (root / "operation.json").is_file()
    assert read_operation_events(tmp_path, "op-projection-recovery") == []
    assert not audit_module._idempotency_path(
        tmp_path, "idem-projection-recovery"
    ).exists()

    monkeypatch.setattr(
        audit_module, "_claim_idempotency_index", original_claim
    )
    operation, replayed = start_operation(tmp_path, **arguments)
    assert replayed is False
    assert operation["status"] == "running"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-projection-recovery")
    ] == ["operation.started"]


def test_retry_recovers_own_visible_index_before_started_once(
    tmp_path, monkeypatch
):
    original_append = audit_module._append_operation_event_locked
    interrupted = {"value": False}

    def interrupt_started_event(
        project_root, operation_id, event_type, details, *args, **kwargs
    ):
        if event_type == "operation.started" and not interrupted["value"]:
            interrupted["value"] = True
            raise KeyboardInterrupt("simulated post-index interruption")
        return original_append(
            project_root, operation_id, event_type, details, *args, **kwargs
        )

    monkeypatch.setattr(
        audit_module, "_append_operation_event_locked", interrupt_started_event
    )
    arguments = {
        "operation_id": "op-index-recovery",
        "operation_type": "model_asset.create",
        "principal": PRINCIPAL,
        "request_id": "request-index-recovery",
        "idempotency_key": "idem-index-recovery",
        "request_payload": {"model_id": "pump-a"},
    }
    with pytest.raises(KeyboardInterrupt, match="post-index interruption"):
        audit_module._start_operation(
            tmp_path, **arguments, _recover_start_failure=False
        )

    assert audit_module._idempotency_path(
        tmp_path, "idem-index-recovery"
    ).is_file()
    assert read_operation_events(tmp_path, "op-index-recovery") == []

    monkeypatch.setattr(
        audit_module, "_append_operation_event_locked", original_append
    )
    operation, replayed = start_operation(tmp_path, **arguments)
    assert replayed is False
    assert operation["status"] == "running"
    operation, replayed = start_operation(tmp_path, **arguments)
    assert replayed is True
    lifecycle = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-index-recovery")
        if event["event_type"] in {
            "operation.started",
            "operation.start_failed",
        }
    ]
    assert lifecycle == ["operation.started"]


def test_losing_idempotency_claim_is_terminalized_in_place(tmp_path):
    winner, _ = start_operation(
        tmp_path,
        operation_id="op-race-winner",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-race-winner",
        idempotency_key="idem-race-terminal",
        request_payload={"model_id": "pump-a"},
    )

    replayed_operation, replayed = start_operation(
        tmp_path,
        operation_id="op-race-loser",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-race-loser",
        idempotency_key="idem-race-terminal",
        request_payload={"model_id": "pump-a"},
    )

    assert replayed is True
    assert replayed_operation["operation_id"] == winner["operation_id"]
    loser = load_operation(tmp_path, "op-race-loser")
    assert loser["status"] == "failed"
    assert loser["error"]["code"] == "idempotency_race_lost"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-race-loser")
    ] == ["operation.start_failed"]


def test_index_publication_failure_is_terminalized_in_place(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        audit_module,
        "_claim_idempotency_index",
        lambda _path, _payload: audit_module._PublicationResult(
            "not_published", OSError("simulated publication failure")
        ),
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-publication-failed",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-publication-failed",
            idempotency_key="idem-publication-failed",
            request_payload={"model_id": "pump-a"},
        )

    assert exc_info.value.code == "audit_persistence_error"
    failed = load_operation(tmp_path, "op-publication-failed")
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "audit_persistence_error"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-publication-failed")
    ] == ["operation.start_failed"]


def test_unconfirmed_publication_preserves_running_candidate_for_retry(
    tmp_path, monkeypatch
):
    original_claim = audit_module._claim_idempotency_index
    unconfirmed = {"value": False}

    def publish_then_report_unconfirmed(path, payload):
        result = original_claim(path, payload)
        if not unconfirmed["value"]:
            unconfirmed["value"] = True
            assert result.state == "published_confirmed"
            return audit_module._PublicationResult(
                "published_unconfirmed",
                OSError("simulated durability uncertainty"),
            )
        return result

    monkeypatch.setattr(
        audit_module,
        "_claim_idempotency_index",
        publish_then_report_unconfirmed,
    )
    arguments = {
        "operation_id": "op-publication-unconfirmed",
        "operation_type": "model_asset.create",
        "principal": PRINCIPAL,
        "request_id": "request-publication-unconfirmed",
        "idempotency_key": "idem-publication-unconfirmed",
        "request_payload": {"model_id": "pump-a"},
    }
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(tmp_path, **arguments)
    assert exc_info.value.code == "audit_persistence_error"
    assert load_operation(tmp_path, "op-publication-unconfirmed")[
        "status"
    ] == "running"
    assert read_operation_events(tmp_path, "op-publication-unconfirmed") == []

    operation, replayed = start_operation(tmp_path, **arguments)
    assert replayed is False
    assert operation["status"] == "running"
    assert [
        event["event_type"]
        for event in read_operation_events(
            tmp_path, "op-publication-unconfirmed"
        )
    ] == ["operation.started"]


def test_existing_operation_envelope_mismatch_fails_without_mutation(tmp_path):
    start_operation(
        tmp_path,
        operation_id="op-envelope",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-envelope",
        idempotency_key="idem-envelope",
        request_payload={"model_id": "pump-a"},
    )
    root = (
        tmp_path / "reports" / "model_matching_operations" / "op-envelope"
    )
    projection_before = (root / "operation.json").read_bytes()
    events_before = (root / "events.jsonl").read_bytes()

    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-envelope",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-changed",
            idempotency_key="idem-envelope-changed",
            request_payload={"model_id": "pump-b"},
        )

    assert exc_info.value.code == "audit_integrity_error"
    assert (root / "operation.json").read_bytes() == projection_before
    assert (root / "events.jsonl").read_bytes() == events_before


def test_same_idempotency_key_has_one_started_winner_and_terminal_losers(
    tmp_path,
):
    operation_ids = [f"op-concurrent-{index}" for index in range(4)]

    def begin(operation_id):
        return start_operation(
            tmp_path,
            operation_id=operation_id,
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id=f"request-{operation_id}",
            idempotency_key="idem-concurrent-terminal",
            request_payload={"model_id": "pump-a"},
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(begin, operation_ids))

    winner_ids = {
        operation["operation_id"] for operation, _replayed in results
    }
    assert len(winner_ids) == 1
    started = []
    failed = []
    for operation_id in operation_ids:
        events = read_operation_events(tmp_path, operation_id)
        lifecycle = [
            event for event in events if event["event_type"] in {
                "operation.started",
                "operation.start_failed",
            }
        ]
        assert len(lifecycle) == 1
        if lifecycle[0]["event_type"] == "operation.started":
            started.append(operation_id)
        else:
            assert lifecycle[0]["details"]["code"] == (
                "idempotency_race_lost"
            )
            failed.append(operation_id)
    assert started == list(winner_ids)
    assert sorted(failed) == sorted(set(operation_ids) - winner_ids)


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
        result = original_claim(path, payload)
        if payload["operation_id"] == "op-live":
            index_claimed.set()
            assert release_initializer.wait(timeout=2)
        return result

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
    assert exc_info.value.code == "invalid_audit_event"
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


def test_idempotency_claim_io_failure_terminalizes_in_place_and_audits(
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
    failed_root = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-index-fault"
    )
    assert failed_root.is_dir()
    failed = load_operation(tmp_path, "op-index-fault")
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "audit_persistence_error"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-index-fault")
    ] == ["operation.start_failed"]
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
        result = audit_module._claim_idempotency_index(path, payload)
        return (
            "won"
            if result.state == "published_confirmed"
            else "lost"
        )

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
    result = audit_module._claim_idempotency_index(
        path,
        {"operation_id": "op-other", "request_fingerprint": "other"},
    )
    assert result.state == "not_published"
    assert isinstance(result.error, FileExistsError)
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


def test_no_replace_publication_returns_actual_link_state(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_text('{"complete":true}', encoding="utf-8")

    confirmed = audit_module._publish_no_replace(source, destination)
    assert confirmed.state == "published_confirmed"

    loser = tmp_path / "loser.json"
    loser.write_text('{"complete":false}', encoding="utf-8")
    not_published = audit_module._publish_no_replace(loser, destination)
    assert not_published.state == "not_published"
    assert isinstance(not_published.error, FileExistsError)
    assert destination.read_text(encoding="utf-8") == '{"complete":true}'

    unconfirmed_destination = tmp_path / "unconfirmed.json"
    monkeypatch.setattr(
        audit_module,
        "_fsync_directory",
        lambda _path: (_ for _ in ()).throw(
            OSError("simulated directory durability failure")
        ),
    )
    unconfirmed = audit_module._publish_no_replace(
        loser, unconfirmed_destination
    )
    assert unconfirmed.state == "published_unconfirmed"
    assert isinstance(unconfirmed.error, OSError)
    assert unconfirmed_destination.read_text(encoding="utf-8") == (
        '{"complete":false}'
    )


def test_unconfirmed_publication_preserves_operation_for_recovery(
    tmp_path, monkeypatch
):
    audit_module._require_audit_storage_capabilities(tmp_path)
    original_fsync_directory = audit_module._fsync_directory
    failed_once = {"value": False}

    def interrupted_directory_fsync(path):
        if path.name == "model_matching_idempotency" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated post-link directory fsync failure")
        return original_fsync_directory(path)

    monkeypatch.setattr(
        audit_module, "_fsync_directory", interrupted_directory_fsync
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-unconfirmed",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-001",
            idempotency_key="idem-unconfirmed",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "audit_persistence_error"
    operation_root = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-unconfirmed"
    )
    assert operation_root.is_dir()
    assert read_operation_events(tmp_path, "op-unconfirmed") == []
    assert audit_module._idempotency_path(
        tmp_path, "idem-unconfirmed"
    ).is_file()
    audits = mutation_failure_audits(tmp_path, "op-unconfirmed")
    assert len(audits) == 1

    monkeypatch.setattr(
        audit_module, "_fsync_directory", original_fsync_directory
    )
    recovered, replayed = start_operation(
        tmp_path,
        operation_id="op-retry",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-002",
        idempotency_key="idem-unconfirmed",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is True
    assert recovered["operation_id"] == "op-unconfirmed"
    assert recovered["error"]["code"] == "operation_start_interrupted"


def test_invalid_index_operation_id_is_stable_audited_integrity_error(
    tmp_path
):
    start_operation(
        tmp_path,
        operation_id="op-indexed",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-invalid-id",
        request_payload={"model_id": "pump-a"},
    )
    index_path = audit_module._idempotency_path(
        tmp_path, "idem-invalid-id"
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["operation_id"] = "invalid/operation"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    events_before = read_operation_events(tmp_path, "op-indexed")

    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path,
            operation_id="op-retry",
            operation_type="model_asset.create",
            principal=PRINCIPAL,
            request_id="request-002",
            idempotency_key="idem-invalid-id",
            request_payload={"model_id": "pump-a"},
        )
    assert exc_info.value.code == "audit_integrity_error"
    assert read_operation_events(tmp_path, "op-indexed") == events_before
    audits = mutation_failure_audits(tmp_path, "op-retry")
    assert len(audits) == 1


@pytest.mark.parametrize(
    ("operation_id", "token_fingerprint"),
    [
        ("invalid/operation", "0" * 64),
        ("000-invalid", "not-a-sha256"),
    ],
)
def test_invalid_denied_marker_is_reported_and_does_not_abort_later_marker(
    tmp_path, operation_id, token_fingerprint
):
    recovery_root = (
        tmp_path / "reports" / "model_matching_denied_recovery"
    )
    recovery_root.mkdir(parents=True)
    corrupt_path = recovery_root / "000-invalid.json"
    corrupt_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "operation_id": operation_id,
                "request_id": "request-corrupt",
                "route": "POST /model-library/models",
                "reason": "permission_denied",
                "token_fingerprint": token_fingerprint,
            }
        ),
        encoding="utf-8",
    )
    valid_operation_id = "denied-valid"
    valid_marker = audit_module._denied_recovery_path(
        tmp_path, valid_operation_id
    )
    valid_marker.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "operation_id": valid_operation_id,
                "request_id": "request-valid",
                "route": "POST /model-library/models",
                "reason": "permission_denied",
                "token_fingerprint": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    assert audit_module.recover_denied_operations(tmp_path) == [
        valid_operation_id
    ]
    _assert_recovered_denial(tmp_path, valid_operation_id)
    assert corrupt_path.exists()
    reports = list((recovery_root / "errors").glob("*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text(encoding="utf-8"))["code"] == (
        "audit_integrity_error"
    )
    assert len(mutation_failure_audits(tmp_path, "000-invalid")) == 1


@pytest.mark.parametrize(
    ("malformation", "attempted_mutation"),
    [
        ("invalid_json", "model_asset.validated"),
        ("non_object", "operation.completed"),
        ("invalid_structure", "operation.failed"),
    ],
)
def test_malformed_event_ledger_rejects_and_audits_public_mutation(
    tmp_path, malformation, attempted_mutation
):
    start_operation(
        tmp_path,
        operation_id="op-malformed",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-malformed",
        request_payload={"model_id": "pump-a"},
    )
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-malformed"
        / "events.jsonl"
    )
    if malformation == "invalid_json":
        malformed = '{"event_id":'
    elif malformation == "non_object":
        malformed = "[]"
    else:
        event = read_operation_events(tmp_path, "op-malformed")[0]
        event["details"] = []
        event["event_hash"] = audit_module._event_hash(event)
        malformed = json.dumps(event, sort_keys=True)
    events_path.write_text(malformed + "\n", encoding="utf-8")
    malformed_before = events_path.read_text(encoding="utf-8")

    if attempted_mutation == "operation.completed":
        mutation = lambda: complete_operation(
            tmp_path, "op-malformed", {"model_id": "pump-a"}
        )
    elif attempted_mutation == "operation.failed":
        mutation = lambda: fail_operation(
            tmp_path, "op-malformed", "failed", "Failed."
        )
    else:
        mutation = lambda: append_operation_event(
            tmp_path, "op-malformed", attempted_mutation, {}
        )
    with pytest.raises(ModelMatchingError) as exc_info:
        mutation()
    assert exc_info.value.code == "audit_integrity_error"
    assert events_path.read_text(encoding="utf-8") == malformed_before
    audits = mutation_failure_audits(tmp_path, "op-malformed")
    assert len(audits) == 1
    assert any(
        event["details"]["attempted_mutation"] == attempted_mutation
        and event["details"]["code"] == "audit_integrity_error"
        for event in audits[0][1]
        if event["event_type"] == "operation.mutation_rejected"
    )


@pytest.mark.parametrize(
    "reserved_event",
    [
        "operation.started",
        "operation.start_failed",
        "operation.completed",
        "operation.failed",
    ],
)
def test_generic_append_rejects_reserved_lifecycle_events(
    tmp_path, reserved_event
):
    start_operation(
        tmp_path,
        operation_id="op-reserved",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-reserved",
        request_payload={"model_id": "pump-a"},
    )
    events_before = read_operation_events(tmp_path, "op-reserved")
    with pytest.raises(ModelMatchingError) as exc_info:
        append_operation_event(
            tmp_path, "op-reserved", reserved_event, {}
        )
    assert exc_info.value.code == "invalid_audit_event"
    assert read_operation_events(tmp_path, "op-reserved") == events_before
    audits = mutation_failure_audits(tmp_path, "op-reserved")
    assert len(audits) == 1


@pytest.mark.parametrize("invalid_transition", ["complete", "fail"])
def test_dedicated_lifecycle_api_validates_details_before_append(
    tmp_path, invalid_transition
):
    start_operation(
        tmp_path,
        operation_id="op-invalid-details",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-invalid-details",
        request_payload={"model_id": "pump-a"},
    )
    events_before = read_operation_events(tmp_path, "op-invalid-details")
    if invalid_transition == "complete":
        mutation = lambda: complete_operation(
            tmp_path, "op-invalid-details", ["not", "an", "object"]
        )
    else:
        mutation = lambda: fail_operation(
            tmp_path, "op-invalid-details", "", None
        )
    with pytest.raises(ModelMatchingError) as exc_info:
        mutation()
    assert exc_info.value.code == "invalid_audit_event"
    assert read_operation_events(tmp_path, "op-invalid-details") == events_before
    audits = mutation_failure_audits(tmp_path, "op-invalid-details")
    assert len(audits) == 1


def test_hash_valid_terminal_with_invalid_details_fails_integrity(tmp_path):
    start_operation(
        tmp_path,
        operation_id="op-invalid-terminal",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-invalid-terminal",
        request_payload={"model_id": "pump-a"},
    )
    events = read_operation_events(tmp_path, "op-invalid-terminal")
    terminal = {
        **events[0],
        "event_id": "invalid-terminal",
        "sequence": 2,
        "event_type": "operation.completed",
        "timestamp": audit_module.utc_now(),
        "previous_event_hash": events[0]["event_hash"],
        "details": {},
    }
    terminal["event_hash"] = audit_module._event_hash(terminal)
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-invalid-terminal"
        / "events.jsonl"
    )
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in [events[0], terminal])
        + "\n",
        encoding="utf-8",
    )
    projection_path = events_path.with_name("operation.json")
    projection_before = projection_path.read_text(encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_operation(tmp_path, "op-invalid-terminal")
    assert exc_info.value.code == "audit_integrity_error"
    assert projection_path.read_text(encoding="utf-8") == projection_before


def test_projection_repair_requires_operation_lock_and_revalidates(tmp_path):
    start_operation(
        tmp_path,
        operation_id="op-repair-lock",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-repair-lock",
        request_payload={"model_id": "pump-a"},
    )
    complete_operation(tmp_path, "op-repair-lock", {"model_id": "pump-a"})
    projection_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-repair-lock"
        / "operation.json"
    )
    stale = json.loads(projection_path.read_text(encoding="utf-8"))
    stale.update(
        {"status": "running", "completed_at": None, "result": None}
    )
    projection_path.write_text(json.dumps(stale), encoding="utf-8")
    stale_before = projection_path.read_text(encoding="utf-8")

    with audit_module._operation_write_lock(tmp_path, "op-repair-lock"):
        with pytest.raises(ModelMatchingError) as exc_info:
            load_operation(tmp_path, "op-repair-lock")
        assert exc_info.value.code == "operation_busy"
        assert projection_path.read_text(encoding="utf-8") == stale_before
    assert load_operation(tmp_path, "op-repair-lock")["status"] == "completed"


def test_corrupt_marker_audit_retries_until_durable_without_duplicate(
    tmp_path, monkeypatch
):
    recovery_root = (
        tmp_path / "reports" / "model_matching_denied_recovery"
    )
    recovery_root.mkdir(parents=True)
    corrupt = recovery_root / "000-retry.json"
    corrupt.write_text('{"operation_id":', encoding="utf-8")
    original_record = audit_module._record_failed_mutation
    attempts = {"value": 0}

    def fail_first_audit(*args, **kwargs):
        attempts["value"] += 1
        if attempts["value"] == 1:
            raise OSError("simulated transient failure audit interruption")
        return original_record(*args, **kwargs)

    monkeypatch.setattr(
        audit_module, "_record_failed_mutation", fail_first_audit
    )
    assert audit_module.recover_denied_operations(tmp_path) == []
    monkeypatch.setattr(
        audit_module, "_record_failed_mutation", original_record
    )
    assert audit_module.recover_denied_operations(tmp_path) == []
    audits = mutation_failure_audits(tmp_path, "000-retry")
    assert len(audits) == 1
    assert list((recovery_root / "errors" / "audited").glob("*.json"))

    assert audit_module.recover_denied_operations(tmp_path) == []
    assert len(mutation_failure_audits(tmp_path, "000-retry")) == 1


def test_task2_docs_converge_publication_state_contract():
    root = audit_module.Path(__file__).parents[1]
    plan = (
        root
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-22-phase15a-cad-model-library-audit.md"
    ).read_text(encoding="utf-8")
    design = (
        root
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-07-22-phase15-model-library-retrieval-registration-design.md"
    ).read_text(encoding="utf-8")
    for state in (
        "not_published",
        "published_confirmed",
        "published_unconfirmed",
    ):
        assert state in plan
        assert state in design
    assert (
        "docs/superpowers/specs/"
        "2026-07-22-phase15-model-library-retrieval-registration-design.md"
        in plan
    )
    assert (
        "docs/superpowers/plans/"
        "2026-07-22-phase15a-cad-model-library-audit.md"
        in plan
    )


def _start_binding_test_operation(tmp_path, operation_id):
    start_operation(
        tmp_path,
        operation_id=operation_id,
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id=f"request-{operation_id}",
        idempotency_key=f"idem-{operation_id}",
        request_payload={"model_id": operation_id},
    )


def _operation_artifact_path(tmp_path, operation_id, filename):
    return (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / operation_id
        / filename
    )


def test_read_events_rejects_hash_valid_ledger_transplanted_from_other_operation(
    tmp_path,
):
    _start_binding_test_operation(tmp_path, "op-source")
    _start_binding_test_operation(tmp_path, "op-target")
    source_events = _operation_artifact_path(
        tmp_path, "op-source", "events.jsonl"
    ).read_bytes()
    target_events_path = _operation_artifact_path(
        tmp_path, "op-target", "events.jsonl"
    )
    target_events_path.write_bytes(source_events)

    with pytest.raises(ModelMatchingError) as exc_info:
        read_operation_events(tmp_path, "op-target")

    assert exc_info.value.code == "audit_integrity_error"
    assert target_events_path.read_bytes() == source_events


@pytest.mark.parametrize(
    "attempted_mutation",
    ["model_asset.validated", "operation.completed", "operation.failed"],
)
def test_public_mutation_audits_transplanted_ledger_without_changing_target(
    tmp_path, attempted_mutation
):
    _start_binding_test_operation(tmp_path, "op-source")
    _start_binding_test_operation(tmp_path, "op-target")
    source_events = _operation_artifact_path(
        tmp_path, "op-source", "events.jsonl"
    ).read_bytes()
    target_events_path = _operation_artifact_path(
        tmp_path, "op-target", "events.jsonl"
    )
    target_events_path.write_bytes(source_events)

    if attempted_mutation == "operation.completed":
        mutation = lambda: complete_operation(
            tmp_path, "op-target", {"model_id": "op-target"}
        )
    elif attempted_mutation == "operation.failed":
        mutation = lambda: fail_operation(
            tmp_path, "op-target", "failed", "Failed."
        )
    else:
        mutation = lambda: append_operation_event(
            tmp_path, "op-target", attempted_mutation, {}
        )
    with pytest.raises(ModelMatchingError) as exc_info:
        mutation()

    assert exc_info.value.code == "audit_integrity_error"
    assert target_events_path.read_bytes() == source_events
    audits = mutation_failure_audits(tmp_path, "op-target")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["attempted_mutation"] == attempted_mutation
        and event["details"]["code"] == "audit_integrity_error"
        for event in audits[0][1]
    )


def test_load_rejects_projection_transplanted_from_other_operation(tmp_path):
    _start_binding_test_operation(tmp_path, "op-source")
    _start_binding_test_operation(tmp_path, "op-target")
    source_projection = _operation_artifact_path(
        tmp_path, "op-source", "operation.json"
    ).read_bytes()
    target_projection_path = _operation_artifact_path(
        tmp_path, "op-target", "operation.json"
    )
    target_projection_path.write_bytes(source_projection)

    with pytest.raises(ModelMatchingError) as exc_info:
        load_operation(tmp_path, "op-target")

    assert exc_info.value.code == "audit_integrity_error"
    assert target_projection_path.read_bytes() == source_projection


def test_load_rejects_empty_projection_object(tmp_path):
    _start_binding_test_operation(tmp_path, "op-empty")
    projection_path = _operation_artifact_path(
        tmp_path, "op-empty", "operation.json"
    )
    projection_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        load_operation(tmp_path, "op-empty")

    assert exc_info.value.code == "audit_integrity_error"
    assert projection_path.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("actor_id", "mallory"),
        ("roles", ["operator"]),
        ("principal_source", "development_header"),
        ("request_id", "request-forged"),
        ("request_fingerprint", "0" * 64),
    ],
)
def test_load_rejects_projection_start_envelope_tamper(
    tmp_path, field, forged_value
):
    _start_binding_test_operation(tmp_path, "op-start-envelope")
    projection_path = _operation_artifact_path(
        tmp_path, "op-start-envelope", "operation.json"
    )
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection[field] = forged_value
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    tampered_bytes = projection_path.read_bytes()

    with pytest.raises(ModelMatchingError) as exc_info:
        load_operation(tmp_path, "op-start-envelope")

    assert exc_info.value.code == "audit_integrity_error"
    assert projection_path.read_bytes() == tampered_bytes


@pytest.mark.parametrize(
    "attempted_mutation",
    ["model_asset.validated", "operation.completed", "operation.failed"],
)
def test_public_mutation_audits_empty_projection_without_changing_target(
    tmp_path, attempted_mutation
):
    _start_binding_test_operation(tmp_path, "op-empty")
    projection_path = _operation_artifact_path(
        tmp_path, "op-empty", "operation.json"
    )
    events_path = _operation_artifact_path(
        tmp_path, "op-empty", "events.jsonl"
    )
    projection_path.write_text("{}", encoding="utf-8")
    events_before = events_path.read_bytes()

    if attempted_mutation == "operation.completed":
        mutation = lambda: complete_operation(
            tmp_path, "op-empty", {"model_id": "op-empty"}
        )
    elif attempted_mutation == "operation.failed":
        mutation = lambda: fail_operation(
            tmp_path, "op-empty", "failed", "Failed."
        )
    else:
        mutation = lambda: append_operation_event(
            tmp_path, "op-empty", attempted_mutation, {}
        )
    with pytest.raises(ModelMatchingError) as exc_info:
        mutation()

    assert exc_info.value.code == "audit_integrity_error"
    assert projection_path.read_text(encoding="utf-8") == "{}"
    assert events_path.read_bytes() == events_before
    audits = mutation_failure_audits(tmp_path, "op-empty")
    assert len(audits) == 1
    assert any(
        event["event_type"] == "operation.mutation_rejected"
        and event["details"]["attempted_mutation"] == attempted_mutation
        and event["details"]["code"] == "audit_integrity_error"
        for event in audits[0][1]
    )
