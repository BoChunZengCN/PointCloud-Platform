import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pc_system.model_library as library_module
from pc_system.model_library import (
    create_model_asset,
    list_model_assets,
    load_model_asset,
    model_asset_path,
    model_version_dir,
)
from pc_system.model_matching_audit import (
    append_operation_event,
    load_operation,
    read_operation_events,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")


class _StringValue:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value


class _ExplodingString:
    def __str__(self):
        raise RuntimeError("string conversion exploded")


class _BytesPathLike:
    def __fspath__(self):
        return b"Pump Bytes"

    def __str__(self):
        return "Pump Bytes"


class _ExplodingList(list):
    def __init__(self, values):
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        raise RuntimeError("collection iteration exploded")


class _AliasedPathLike:
    def __init__(self, display_name):
        self.display_name = display_name

    def __fspath__(self):
        return "shared-path"

    def __str__(self):
        return self.display_name


class _AliasedList(list):
    def __init__(self, display_name):
        super().__init__(["shared-item"])
        self.display_name = display_name

    def __str__(self):
        return self.display_name


class _StatefulDisplayName:
    def __init__(self, first, second):
        self.values = (first, second)
        self.calls = 0

    def __str__(self):
        value = self.values[min(self.calls, 1)]
        self.calls += 1
        return value


class _ExplodingClassDisplayName:
    @property
    def __class__(self):
        raise RuntimeError("class access exploded")

    def __str__(self):
        return "Pump A"


def create_pump(project, **overrides):
    arguments = {
        "model_id": "pump-a",
        "display_name": "Pump A",
        "category_id": "pump",
        "manufacturer": "Acme",
        "model_number": "A-100",
        "keywords": ["centrifugal", "Process Pump", "centrifugal"],
        "tags": ["pump", "motor-coupled", "Pump"],
        "principal": EXPERT,
        "operation_id": "op-model-001",
        "request_id": "request-model-001",
        "idempotency_key": "idem-model-001",
    }
    arguments.update(overrides)
    return create_model_asset(project, **arguments)


def _failure_code(project: Path, operation_id: str) -> str:
    operation = load_operation(project, operation_id)
    assert operation["status"] == "failed"
    events = read_operation_events(project, operation_id)
    assert events[-1]["event_type"] == "operation.failed"
    return events[-1]["details"]["code"]


def _replay_failure_audits(project: Path) -> list[tuple[dict, list[dict]]]:
    operations_root = (
        project / "reports" / "model_matching_operations"
    )
    if not operations_root.exists():
        return []
    audits = []
    for candidate in operations_root.iterdir():
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        operation = load_operation(project, candidate.name)
        if operation["operation_type"] == "model_asset.replay_failure":
            audits.append(
                (
                    operation,
                    read_operation_events(project, candidate.name),
                )
            )
    return audits


def _assert_replay_failure_audit(
    project: Path,
    *,
    principal: Principal,
    original_operation_id: str,
    requested_operation_id: str,
    failure_code: str,
) -> None:
    audits = _replay_failure_audits(project)
    assert len(audits) == 1
    operation, events = audits[0]
    assert operation["status"] == "failed"
    assert operation["actor_id"] == principal.actor_id
    assert operation["roles"] == sorted(principal.roles)
    assert operation["principal_source"] == principal.source
    assert operation["error"]["code"] == failure_code
    assert [event["event_type"] for event in events] == [
        "operation.started",
        "model_asset.replay_failed",
        "operation.failed",
    ]
    details = events[1]["details"]
    assert details == {
        "attempt_id": operation["operation_id"],
        "attempted_mutation": "model_asset.create",
        "failure_code": failure_code,
        "original_operation_id": original_operation_id,
        "requested_operation_id": requested_operation_id,
    }
    assert events[1]["actor_id"] == principal.actor_id
    assert events[1]["roles"] == sorted(principal.roles)
    assert events[1]["principal_source"] == principal.source


def test_model_create_consumes_one_frozen_stateful_text(tmp_path):
    display_name = _StatefulDisplayName("Pump A", "Pump B")

    created = create_pump(tmp_path, display_name=display_name)

    assert created["display_name"] == "Pump A"
    assert display_name.calls == 1


def test_same_snapshot_cannot_hide_different_business_value(tmp_path):
    first = _StatefulDisplayName("shared", "Pump A")
    second = _StatefulDisplayName("shared", "Pump B")
    create_pump(tmp_path, display_name=first)

    replayed = create_pump(
        tmp_path,
        display_name=second,
        operation_id="op-model-replay",
        request_id="request-model-replay",
    )

    assert replayed["display_name"] == "shared"
    assert first.calls == 1
    assert second.calls == 1


def test_exploding_class_property_does_not_escape_before_audit(tmp_path):
    created = create_pump(
        tmp_path, display_name=_ExplodingClassDisplayName()
    )

    assert created["display_name"] == "Pump A"
    assert load_operation(tmp_path, "op-model-001")["status"] == "completed"


def test_create_load_and_list_model_asset_deterministically(tmp_path):
    created = create_pump(tmp_path)

    assert created == {
        "schema_version": "1.0",
        "model_id": "pump-a",
        "display_name": "Pump A",
        "category_id": "pump",
        "manufacturer": "Acme",
        "model_number": "A-100",
        "keywords": ["centrifugal", "process pump"],
        "tags": ["motor-coupled", "pump"],
        "lifecycle_status": "active",
        "created_by": "alice",
        "operation_id": "op-model-001",
        "created_at": created["created_at"],
    }
    assert created["created_at"].endswith("+00:00")
    assert load_model_asset(tmp_path, "pump-a") == created
    assert list_model_assets(tmp_path) == [created]

    operation = load_operation(tmp_path, "op-model-001")
    assert operation["status"] == "completed"
    assert operation["result"] == {
        "model_id": "pump-a",
        "artifact_path": "models/pump-a/model_asset.json",
    }
    events = read_operation_events(tmp_path, "op-model-001")
    assert [event["event_type"] for event in events] == [
        "operation.started",
        "model_asset.created",
        "operation.completed",
    ]
    assert len(events[1]["details"]["manifest_fingerprint"]) == 64


def test_manifest_creation_time_matches_hashed_started_event(tmp_path):
    created = create_pump(tmp_path)
    events = read_operation_events(tmp_path, "op-model-001")
    started = [
        event
        for event in events
        if event["event_type"] == "operation.started"
    ]

    assert len(started) == 1
    assert events[0] == started[0]
    assert created["created_at"] == started[0]["timestamp"]


def test_paths_validate_identifiers(tmp_path):
    assert model_asset_path(tmp_path, "pump-a") == (
        tmp_path / "models" / "pump-a" / "model_asset.json"
    )
    assert model_version_dir(tmp_path, "pump-a", "v1") == (
        tmp_path / "models" / "pump-a" / "versions" / "v1"
    )
    with pytest.raises(ValueError):
        model_asset_path(tmp_path, "../pump")
    with pytest.raises(ValueError):
        model_version_dir(tmp_path, "pump-a", "../v1")


def test_duplicate_model_identity_is_immutable_and_audited(tmp_path):
    original = create_pump(tmp_path)

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            display_name="Different",
            operation_id="op-model-002",
            request_id="request-model-002",
            idempotency_key="idem-model-002",
        )

    assert exc_info.value.code == "model_exists"
    assert load_model_asset(tmp_path, "pump-a") == original
    assert _failure_code(tmp_path, "op-model-002") == "model_exists"


def test_model_creation_requires_expert_role_after_audit_start(tmp_path):
    operator = Principal("bob", frozenset({"operator"}), "configured_token")

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(tmp_path, principal=operator)

    assert exc_info.value.code == "permission_denied"
    assert _failure_code(tmp_path, "op-model-001") == "permission_denied"
    assert not model_asset_path(tmp_path, "pump-a").exists()


@pytest.mark.parametrize(
    ("overrides", "message_fragment"),
    [
        ({"model_id": "../pump"}, "model_id"),
        ({"category_id": "../pump"}, "category_id"),
        ({"display_name": "   "}, "display_name"),
        ({"keywords": ["x" * 129]}, "keywords"),
        ({"tags": ["x" * 129]}, "tags"),
    ],
)
def test_business_validation_failure_is_stable_and_audited(
    tmp_path, overrides, message_fragment
):
    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(tmp_path, **overrides)

    assert exc_info.value.code == "invalid_model_asset"
    assert message_fragment in str(exc_info.value)
    assert _failure_code(tmp_path, "op-model-001") == "invalid_model_asset"


def test_invalid_path_forming_operation_id_fails_before_audit_start(tmp_path):
    with pytest.raises(ValueError):
        create_pump(tmp_path, operation_id="../operation")

    assert not (
        tmp_path / "reports" / "model_matching_operations"
    ).exists()


def test_path_display_name_is_json_safe_and_replays_deterministically(
    tmp_path,
):
    display_name = tmp_path / "Pump A"
    created = create_pump(tmp_path, display_name=display_name)

    assert created["display_name"] == str(display_name)
    replayed = create_pump(
        tmp_path,
        display_name=display_name,
        operation_id="op-model-replay",
        request_id="request-model-replay",
    )
    assert replayed == created


def test_json_unsafe_invalid_collection_is_stably_audited(tmp_path):
    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(tmp_path, keywords={"centrifugal", "pump"})

    assert exc_info.value.code == "invalid_model_asset"
    assert _failure_code(tmp_path, "op-model-001") == "invalid_model_asset"


def test_snapshot_does_not_leak_unusual_object_exceptions(tmp_path):
    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(tmp_path, display_name=_ExplodingString())

    assert exc_info.value.code == "invalid_model_asset"
    assert _failure_code(tmp_path, "op-model-001") == "invalid_model_asset"


def test_snapshot_handles_bytes_pathlike_without_raw_json_error(tmp_path):
    created = create_pump(tmp_path, display_name=_BytesPathLike())

    assert created["display_name"] == "Pump Bytes"


def test_snapshot_handles_unusual_collection_without_pre_audit_error(
    tmp_path,
):
    keywords = _ExplodingList(["centrifugal"])

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            keywords=keywords,
        )

    assert exc_info.value.code == "invalid_model_asset"
    assert _failure_code(tmp_path, "op-model-001") == "invalid_model_asset"
    assert keywords.iterations == 0


def test_snapshot_handles_non_utf8_text_inside_audited_failure(tmp_path):
    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(tmp_path, display_name="\ud800")

    assert exc_info.value.code == "invalid_model_asset"
    assert _failure_code(tmp_path, "op-model-001") == "invalid_model_asset"


def test_distinct_valid_normalized_requests_have_distinct_fingerprints(
    tmp_path,
):
    create_pump(tmp_path, display_name=_StringValue("Pump A"))

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            display_name=_StringValue("Pump B"),
            operation_id="op-model-changed",
            request_id="request-model-changed",
        )

    assert exc_info.value.code == "idempotency_conflict"


def test_same_fspath_with_distinct_business_strings_conflicts(tmp_path):
    create_pump(
        tmp_path,
        display_name=_AliasedPathLike("Pump Path A"),
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            display_name=_AliasedPathLike("Pump Path B"),
            operation_id="op-model-changed",
            request_id="request-model-changed",
        )

    assert exc_info.value.code == "idempotency_conflict"


def test_same_container_structure_with_distinct_business_strings_conflicts(
    tmp_path,
):
    create_pump(
        tmp_path,
        display_name=_AliasedList("Pump Container A"),
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            display_name=_AliasedList("Pump Container B"),
            operation_id="op-model-changed",
            request_id="request-model-changed",
        )

    assert exc_info.value.code == "idempotency_conflict"


def test_surrogate_type_metadata_is_ascii_safe_through_public_create(
    tmp_path,
):
    dynamic_type = type(
        "UnsafeType",
        (),
        {"__str__": lambda self: "Dynamic Pump"},
    )
    dynamic_type.__qualname__ = "Unsafe\ud800Type"
    dynamic_type.__module__ = "unsafe\ud800module"

    created = create_pump(tmp_path, display_name=dynamic_type())

    assert created["display_name"] == "Dynamic Pump"
    assert load_operation(tmp_path, "op-model-001")["status"] == "completed"


def test_same_completed_idempotent_request_returns_published_asset(tmp_path):
    created = create_pump(tmp_path)

    replayed = create_pump(
        tmp_path,
        operation_id="op-model-replay",
        request_id="request-model-replay",
    )

    assert replayed == created
    events = read_operation_events(tmp_path, "op-model-001")
    assert events[-1]["event_type"] == "operation.replayed"
    assert events[-1]["details"]["requested_operation_id"] == "op-model-replay"


def test_completed_replay_rechecks_chain_after_start_operation(
    tmp_path, monkeypatch
):
    create_pump(tmp_path)
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "events.jsonl"
    )
    original_require = library_module.require_any_role
    tampered = False

    def tamper_after_start(principal, allowed):
        nonlocal tampered
        original_require(principal, allowed)
        if tampered:
            return
        tampered = True
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
        ]
        events[0]["details"]["request_id"] = "tampered"
        events_path.write_text(
            "\n".join(
                json.dumps(event, sort_keys=True) for event in events
            )
            + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        library_module, "require_any_role", tamper_after_start
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            operation_id="op-model-replay",
            request_id="request-model-replay",
        )

    assert exc_info.value.code == "audit_integrity_error"


def test_idempotent_replay_still_requires_expert_role(tmp_path):
    create_pump(tmp_path)
    operator = Principal("bob", frozenset({"operator"}), "configured_token")
    original_projection = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "operation.json"
    ).read_bytes()

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            principal=operator,
            operation_id="op-model-replay",
            request_id="request-model-replay",
        )

    assert exc_info.value.code == "permission_denied"
    assert load_operation(tmp_path, "op-model-001")["status"] == "completed"
    assert (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "operation.json"
    ).read_bytes() == original_projection
    events = read_operation_events(tmp_path, "op-model-001")
    assert events[-1]["event_type"] == "operation.replayed"
    assert events[-1]["actor_id"] == "bob"
    _assert_replay_failure_audit(
        tmp_path,
        principal=operator,
        original_operation_id="op-model-001",
        requested_operation_id="op-model-replay",
        failure_code="permission_denied",
    )


@pytest.mark.parametrize(
    ("damage", "expected_code"),
    [
        ("missing", "model_not_found"),
        ("corrupt", "model_asset_integrity_error"),
    ],
)
def test_completed_replay_read_failure_has_independent_correlated_audit(
    tmp_path, damage, expected_code
):
    create_pump(tmp_path)
    original_projection_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "operation.json"
    )
    original_projection = original_projection_path.read_bytes()
    asset_path = model_asset_path(tmp_path, "pump-a")
    if damage == "missing":
        asset_path.unlink()
    else:
        asset_path.write_text("{", encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            operation_id="op-model-replay",
            request_id="request-model-replay",
        )

    assert exc_info.value.code == expected_code
    assert original_projection_path.read_bytes() == original_projection
    original_events = read_operation_events(tmp_path, "op-model-001")
    assert original_events[-1]["event_type"] == "operation.replayed"
    _assert_replay_failure_audit(
        tmp_path,
        principal=EXPERT,
        original_operation_id="op-model-001",
        requested_operation_id="op-model-replay",
        failure_code=expected_code,
    )


def test_replay_failure_audit_failure_fails_closed(
    tmp_path, monkeypatch
):
    create_pump(tmp_path)
    model_asset_path(tmp_path, "pump-a").unlink()
    original_start = library_module.start_operation

    def fail_independent_audit(*args, **kwargs):
        if kwargs["operation_type"] == "model_asset.replay_failure":
            raise OSError("simulated replay failure audit interruption")
        return original_start(*args, **kwargs)

    monkeypatch.setattr(
        library_module, "start_operation", fail_independent_audit
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            operation_id="op-model-replay",
            request_id="request-model-replay",
        )

    assert exc_info.value.code == "audit_persistence_error"
    assert _replay_failure_audits(tmp_path) == []


@pytest.mark.parametrize(
    "failure_point",
    ["append", "terminal_write", "terminal_ack"],
)
def test_replay_failure_audit_interruption_fails_closed(
    tmp_path, monkeypatch, failure_point
):
    create_pump(tmp_path)
    model_asset_path(tmp_path, "pump-a").unlink()
    original_projection_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "operation.json"
    )
    original_projection = original_projection_path.read_bytes()

    if failure_point == "append":
        original_append = library_module.append_operation_event

        def interrupt_append(*args, **kwargs):
            event_type = args[2]
            if event_type == "model_asset.replay_failed":
                raise OSError("simulated replay audit append interruption")
            return original_append(*args, **kwargs)

        monkeypatch.setattr(
            library_module,
            "append_operation_event",
            interrupt_append,
        )
    else:
        original_fail = library_module.fail_operation

        def interrupt_terminal(*args, **kwargs):
            operation_id = args[1]
            code = args[2]
            if (
                operation_id.startswith("audit-replay-")
                and code != "audit_persistence_error"
            ):
                if failure_point == "terminal_write":
                    raise OSError(
                        "simulated replay audit terminal interruption"
                    )
                result = original_fail(*args, **kwargs)
                raise OSError(
                    "simulated replay audit acknowledgement interruption"
                )
            return original_fail(*args, **kwargs)

        monkeypatch.setattr(
            library_module, "fail_operation", interrupt_terminal
        )

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(
            tmp_path,
            operation_id="op-model-replay",
            request_id="request-model-replay",
        )

    assert exc_info.value.code == "audit_persistence_error"
    assert original_projection_path.read_bytes() == original_projection
    original_events = read_operation_events(tmp_path, "op-model-001")
    assert original_events[-1]["event_type"] == "operation.replayed"


def test_list_sorts_assets_and_ignores_temporary_directories(tmp_path):
    create_pump(
        tmp_path,
        model_id="z-pump",
        operation_id="op-model-z",
        request_id="request-model-z",
        idempotency_key="idem-model-z",
    )
    create_pump(
        tmp_path,
        model_id="a-pump",
        operation_id="op-model-a",
        request_id="request-model-a",
        idempotency_key="idem-model-a",
    )
    temporary = tmp_path / "models" / ".p15-model-interrupted"
    temporary.mkdir(parents=True)
    (temporary / "model_asset.json").write_text(
        json.dumps({"model_id": "should-not-appear"}), encoding="utf-8"
    )

    assert [item["model_id"] for item in list_model_assets(tmp_path)] == [
        "a-pump",
        "z-pump",
    ]


def test_missing_and_corrupt_assets_have_stable_read_errors(tmp_path):
    with pytest.raises(ModelMatchingError) as missing:
        load_model_asset(tmp_path, "missing")
    assert missing.value.code == "model_not_found"

    corrupt_path = model_asset_path(tmp_path, "corrupt")
    corrupt_path.parent.mkdir(parents=True)
    corrupt_path.write_text("{", encoding="utf-8")
    with pytest.raises(ModelMatchingError) as corrupt:
        load_model_asset(tmp_path, "corrupt")
    assert corrupt.value.code == "model_asset_integrity_error"


def test_list_normalizes_directory_io_failure(tmp_path, monkeypatch):
    models_root = tmp_path / "models"
    models_root.mkdir()
    original_iterdir = Path.iterdir

    def denied_iterdir(path):
        if path == models_root:
            raise PermissionError("simulated catalog read denial")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", denied_iterdir)

    with pytest.raises(ModelMatchingError) as exc_info:
        list_model_assets(tmp_path)

    assert exc_info.value.code == "model_asset_integrity_error"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "  "),
        ("created_by", ""),
        ("created_by", "../mallory"),
        ("created_at", "not-a-timestamp"),
        ("created_at", "2026-07-25T12:00:00+08:00"),
        ("keywords", ["pump", 1]),
        ("tags", "pump"),
    ],
)
def test_manifest_requires_nonempty_identity_and_utc_timestamp(
    tmp_path, field, value
):
    create_pump(tmp_path)
    path = model_asset_path(tmp_path, "pump-a")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        load_model_asset(tmp_path, "pump-a")

    assert exc_info.value.code == "model_asset_integrity_error"


def test_concurrent_create_has_one_winner_and_never_overwrites(
    tmp_path, monkeypatch
):
    barrier = threading.Barrier(2)
    original_publish = library_module._publish_model_asset

    def synchronized_publish(path, manifest):
        barrier.wait(timeout=5)
        return original_publish(path, manifest)

    monkeypatch.setattr(
        library_module, "_publish_model_asset", synchronized_publish
    )

    def attempt(suffix):
        try:
            return (
                "created",
                create_pump(
                    tmp_path,
                    display_name=f"Pump {suffix}",
                    operation_id=f"op-race-{suffix}",
                    request_id=f"request-race-{suffix}",
                    idempotency_key=f"idem-race-{suffix}",
                ),
            )
        except ModelMatchingError as exc:
            return ("failed", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["a", "b"]))

    created = [value for status, value in results if status == "created"]
    failed = [value for status, value in results if status == "failed"]
    assert len(created) == 1
    assert len(failed) == 1
    assert failed[0].code == "model_exists"
    assert load_model_asset(tmp_path, "pump-a") == created[0]

    completed_operations = [
        operation_id
        for operation_id in ("op-race-a", "op-race-b")
        if load_operation(tmp_path, operation_id)["status"] == "completed"
    ]
    failed_operations = [
        operation_id
        for operation_id in ("op-race-a", "op-race-b")
        if load_operation(tmp_path, operation_id)["status"] == "failed"
    ]
    assert len(completed_operations) == 1
    assert len(failed_operations) == 1
    assert _failure_code(tmp_path, failed_operations[0]) == "model_exists"


@pytest.mark.parametrize("failure_point", ["append", "complete"])
def test_published_asset_survives_audit_failure_without_becoming_overwritable(
    tmp_path, monkeypatch, failure_point
):
    if failure_point == "append":
        monkeypatch.setattr(
            library_module,
            "append_operation_event",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("simulated append interruption")
            ),
        )
    else:
        original_complete = library_module.complete_operation

        def complete_then_interrupt(*args, **kwargs):
            original_complete(*args, **kwargs)
            raise OSError("simulated completion acknowledgement interruption")

        monkeypatch.setattr(
            library_module, "complete_operation", complete_then_interrupt
        )

    with pytest.raises(ModelMatchingError) as exc_info:
        create_pump(tmp_path)

    assert exc_info.value.code == "audit_persistence_error"
    published = load_model_asset(tmp_path, "pump-a")
    original_bytes = model_asset_path(tmp_path, "pump-a").read_bytes()

    with pytest.raises(ModelMatchingError) as duplicate:
        create_pump(
            tmp_path,
            display_name="Overwrite attempt",
            operation_id="op-model-overwrite",
            request_id="request-model-overwrite",
            idempotency_key="idem-model-overwrite",
        )
    assert duplicate.value.code == "model_exists"
    assert load_model_asset(tmp_path, "pump-a") == published
    assert model_asset_path(tmp_path, "pump-a").read_bytes() == original_bytes


def _interrupt_model_finalization_once(monkeypatch, failure_point):
    tripped = False
    if failure_point.startswith("append"):
        original = library_module.append_operation_event

        def interrupt(*args, **kwargs):
            nonlocal tripped
            if args[2] == "model_asset.created" and not tripped:
                tripped = True
                if failure_point == "append_after_write":
                    original(*args, **kwargs)
                raise OSError("simulated model created audit interruption")
            return original(*args, **kwargs)

        monkeypatch.setattr(
            library_module, "append_operation_event", interrupt
        )
    else:
        original = library_module.complete_operation

        def interrupt(*args, **kwargs):
            nonlocal tripped
            if args[1] == "op-model-001" and not tripped:
                tripped = True
                if failure_point == "complete_after_write":
                    original(*args, **kwargs)
                raise OSError("simulated model completion interruption")
            return original(*args, **kwargs)

        monkeypatch.setattr(library_module, "complete_operation", interrupt)


def test_failure_audit_interruption_overrides_business_error_and_retry_finishes(
    tmp_path, monkeypatch
):
    original_fail = library_module.fail_operation
    interrupted = False

    def interrupt_once(*args, **kwargs):
        nonlocal interrupted
        if args[1] == "op-model-001" and not interrupted:
            interrupted = True
            raise OSError("simulated failure audit interruption")
        return original_fail(*args, **kwargs)

    monkeypatch.setattr(library_module, "fail_operation", interrupt_once)

    with pytest.raises(ModelMatchingError) as first_error:
        create_pump(tmp_path, display_name="   ")

    assert first_error.value.code == "audit_persistence_error"
    assert load_operation(tmp_path, "op-model-001")["status"] == "running"

    with pytest.raises(ModelMatchingError) as retry_error:
        create_pump(
            tmp_path,
            display_name="   ",
            operation_id="op-model-retry",
            request_id="request-model-retry",
        )

    assert retry_error.value.code == "invalid_model_asset"
    assert _failure_code(
        tmp_path, "op-model-001"
    ) == "invalid_model_asset"
    assert not model_asset_path(tmp_path, "pump-a").exists()


@pytest.mark.parametrize(
    ("failure_point", "status_after_first"),
    [
        ("append_before_write", "running"),
        ("append_after_write", "running"),
        ("complete_before_write", "running"),
        ("complete_after_write", "completed"),
    ],
)
def test_published_asset_recovers_each_audit_finalization_boundary(
    tmp_path, monkeypatch, failure_point, status_after_first
):
    _interrupt_model_finalization_once(monkeypatch, failure_point)

    with pytest.raises(ModelMatchingError) as first_error:
        create_pump(tmp_path)

    assert first_error.value.code == "audit_persistence_error"
    assert (
        load_operation(tmp_path, "op-model-001")["status"]
        == status_after_first
    )
    asset_path = model_asset_path(tmp_path, "pump-a")
    published_bytes = asset_path.read_bytes()

    recovered = create_pump(
        tmp_path,
        operation_id="op-model-recovery",
        request_id="request-model-recovery",
    )

    assert recovered == load_model_asset(tmp_path, "pump-a")
    assert asset_path.read_bytes() == published_bytes
    operation = load_operation(tmp_path, "op-model-001")
    assert operation["status"] == "completed"
    assert operation["result"]["model_id"] == "pump-a"
    events = read_operation_events(tmp_path, "op-model-001")
    event_types = [event["event_type"] for event in events]
    assert event_types.count("model_asset.created") == 1
    assert event_types.count("operation.completed") == 1
    assert "operation.failed" not in event_types


def test_unconfirmed_asset_publication_preserves_running_operation_for_retry(
    tmp_path, monkeypatch
):
    original_fsync_directory = library_module._fsync_directory
    interrupted = False

    def interrupt_after_link(path):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise OSError("simulated asset directory fsync interruption")
        return original_fsync_directory(path)

    monkeypatch.setattr(
        library_module, "_fsync_directory", interrupt_after_link
    )

    with pytest.raises(ModelMatchingError) as first_error:
        create_pump(tmp_path)

    assert first_error.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-model-001")["status"] == "running"
    asset_path = model_asset_path(tmp_path, "pump-a")
    published_bytes = asset_path.read_bytes()

    recovered = create_pump(
        tmp_path,
        operation_id="op-model-recovery",
        request_id="request-model-recovery",
    )

    assert recovered == load_model_asset(tmp_path, "pump-a")
    assert asset_path.read_bytes() == published_bytes
    assert load_operation(tmp_path, "op-model-001")["status"] == "completed"
    events = read_operation_events(tmp_path, "op-model-001")
    event_types = [event["event_type"] for event in events]
    assert event_types.count("model_asset.created") == 1
    assert event_types.count("operation.completed") == 1
    assert "operation.failed" not in event_types


def test_manifest_requires_valid_canonical_operation_id(tmp_path):
    created = create_pump(tmp_path)
    assert created["operation_id"] == "op-model-001"
    path = model_asset_path(tmp_path, "pump-a")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["operation_id"] = "../operation"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        load_model_asset(tmp_path, "pump-a")

    assert exc_info.value.code == "model_asset_integrity_error"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_id", "op-other"),
        ("display_name", "Pump Tampered"),
        ("created_by", "mallory"),
    ],
)
def test_running_recovery_rejects_published_manifest_mismatch(
    tmp_path, monkeypatch, field, value
):
    _interrupt_model_finalization_once(
        monkeypatch, "append_before_write"
    )
    with pytest.raises(ModelMatchingError) as first_error:
        create_pump(tmp_path)
    assert first_error.value.code == "audit_persistence_error"

    path = model_asset_path(tmp_path, "pump-a")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    tampered_bytes = path.read_bytes()

    with pytest.raises(ModelMatchingError) as retry_error:
        create_pump(
            tmp_path,
            operation_id="op-model-recovery",
            request_id="request-model-recovery",
        )

    assert retry_error.value.code == "audit_integrity_error"
    assert load_operation(tmp_path, "op-model-001")["status"] == "running"
    assert path.read_bytes() == tampered_bytes


def test_running_recovery_rejects_noncanonical_created_at(
    tmp_path, monkeypatch
):
    _interrupt_model_finalization_once(
        monkeypatch, "append_before_write"
    )
    with pytest.raises(ModelMatchingError) as first_error:
        create_pump(tmp_path)
    assert first_error.value.code == "audit_persistence_error"

    path = model_asset_path(tmp_path, "pump-a")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["created_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    tampered_bytes = path.read_bytes()

    with pytest.raises(ModelMatchingError) as retry_error:
        create_pump(
            tmp_path,
            operation_id="op-model-recovery",
            request_id="request-model-recovery",
        )

    assert retry_error.value.code == "audit_integrity_error"
    assert load_operation(tmp_path, "op-model-001")["status"] == "running"
    assert path.read_bytes() == tampered_bytes


def test_running_recovery_rejects_matching_projection_and_manifest_time_tamper(
    tmp_path, monkeypatch
):
    _interrupt_model_finalization_once(
        monkeypatch, "append_before_write"
    )
    with pytest.raises(ModelMatchingError) as first_error:
        create_pump(tmp_path)
    assert first_error.value.code == "audit_persistence_error"

    forged_time = "2000-01-01T00:00:00+00:00"
    operation_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "operation.json"
    )
    operation = json.loads(operation_path.read_text(encoding="utf-8"))
    operation["started_at"] = forged_time
    operation_path.write_text(json.dumps(operation), encoding="utf-8")

    asset_path = model_asset_path(tmp_path, "pump-a")
    manifest = json.loads(asset_path.read_text(encoding="utf-8"))
    manifest["created_at"] = forged_time
    asset_path.write_text(json.dumps(manifest), encoding="utf-8")
    tampered_bytes = asset_path.read_bytes()

    with pytest.raises(ModelMatchingError) as retry_error:
        create_pump(
            tmp_path,
            operation_id="op-model-recovery",
            request_id="request-model-recovery",
        )

    assert retry_error.value.code == "audit_integrity_error"
    assert load_operation(tmp_path, "op-model-001")["status"] == "running"
    assert asset_path.read_bytes() == tampered_bytes


def test_running_recovery_rejects_conflicting_created_event(
    tmp_path, monkeypatch
):
    _interrupt_model_finalization_once(
        monkeypatch, "append_before_write"
    )
    with pytest.raises(ModelMatchingError):
        create_pump(tmp_path)
    append_operation_event(
        tmp_path,
        "op-model-001",
        "model_asset.created",
        {
            "model_id": "pump-a",
            "manifest_fingerprint": "0" * 64,
        },
    )

    with pytest.raises(ModelMatchingError) as retry_error:
        create_pump(
            tmp_path,
            operation_id="op-model-recovery",
            request_id="request-model-recovery",
        )

    assert retry_error.value.code == "audit_integrity_error"
    assert load_operation(tmp_path, "op-model-001")["status"] == "running"


def test_concurrent_completed_replay_with_matching_result_succeeds(
    tmp_path, monkeypatch
):
    created = create_pump(tmp_path)
    asset_path = model_asset_path(tmp_path, "pump-a")
    published_bytes = asset_path.read_bytes()
    authorization_barrier = threading.Barrier(2)
    original_require = library_module.require_any_role

    def synchronized_authorization(principal, allowed):
        original_require(principal, allowed)
        authorization_barrier.wait(timeout=5)

    monkeypatch.setattr(
        library_module, "require_any_role", synchronized_authorization
    )

    def replay(suffix):
        try:
            return (
                "created",
                create_pump(
                    tmp_path,
                    operation_id=f"op-replay-{suffix}",
                    request_id=f"request-replay-{suffix}",
                ),
            )
        except ModelMatchingError as exc:
            return ("failed", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(replay, ["a", "b"]))

    assert [status for status, _ in results] == ["created", "created"]
    assert all(value == created for _, value in results)
    assert asset_path.read_bytes() == published_bytes
    events = read_operation_events(tmp_path, "op-model-001")
    event_types = [event["event_type"] for event in events]
    assert event_types.count("model_asset.created") == 1
    assert event_types.count("operation.completed") == 1


def test_concurrent_published_recovery_never_overwrites_and_completes(
    tmp_path, monkeypatch
):
    _interrupt_model_finalization_once(
        monkeypatch, "append_before_write"
    )
    with pytest.raises(ModelMatchingError):
        create_pump(tmp_path)
    asset_path = model_asset_path(tmp_path, "pump-a")
    published_bytes = asset_path.read_bytes()

    authorization_barrier = threading.Barrier(2)
    original_require = library_module.require_any_role

    def synchronized_authorization(principal, allowed):
        original_require(principal, allowed)
        authorization_barrier.wait(timeout=5)

    monkeypatch.setattr(
        library_module, "require_any_role", synchronized_authorization
    )

    def recover(suffix):
        try:
            return (
                "created",
                create_pump(
                    tmp_path,
                    operation_id=f"op-recovery-{suffix}",
                    request_id=f"request-recovery-{suffix}",
                ),
            )
        except ModelMatchingError as exc:
            return ("failed", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(recover, ["a", "b"]))

    created = [value for status, value in results if status == "created"]
    failed = [value for status, value in results if status == "failed"]
    assert created
    assert all(error.code == "operation_busy" for error in failed)
    assert asset_path.read_bytes() == published_bytes
    assert load_operation(tmp_path, "op-model-001")["status"] == "completed"
    events = read_operation_events(tmp_path, "op-model-001")
    event_types = [event["event_type"] for event in events]
    assert event_types.count("model_asset.created") == 1
    assert event_types.count("operation.completed") == 1
    assert "operation.failed" not in event_types
