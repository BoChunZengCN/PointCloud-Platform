import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

import pc_system.model_release as release_module
import pc_system.model_matching_audit as audit_module
from pc_system.model_import import import_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import (
    load_operation,
    read_operation_events,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal
from pc_system.model_release import (
    list_model_releases,
    list_version_release_status,
    load_current_model_release,
    release_model_version,
)


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
OPERATOR = Principal("bob", frozenset({"operator"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def _fake_reader(_path):
    return {
        "vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
        "faces": [[0, 1, 2]],
    }


def _create_versions(project_root):
    create_model_asset(
        project_root,
        model_id="pump-a",
        display_name="Pump A",
        category_id="pump",
        manufacturer="Acme",
        model_number="A-100",
        keywords=["centrifugal"],
        tags=["pump"],
        principal=EXPERT,
        operation_id="op-asset-release-fixture",
        request_id="req-asset-release-fixture",
        idempotency_key="idem-asset-release-fixture",
    )
    import_model_version(
        project_root,
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={"supplier": "Acme"},
        principal=EXPERT,
        operation_id="op-import-release-v1",
        request_id="req-import-release-v1",
        idempotency_key="idem-import-release-v1",
        mesh_reader=_fake_reader,
    )
    import_model_version(
        project_root,
        model_id="pump-a",
        version_id="v2",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={"supplier": "Acme"},
        supersedes_version_id="v1",
        principal=EXPERT,
        operation_id="op-import-release-v2",
        request_id="req-import-release-v2",
        idempotency_key="idem-import-release-v2",
        mesh_reader=_fake_reader,
    )


def _snapshot_version_bytes(project_root):
    versions_root = project_root / "models" / "pump-a" / "versions"
    return {
        path.relative_to(versions_root).as_posix(): path.read_bytes()
        for path in sorted(versions_root.rglob("*"))
        if path.is_file()
    }


def _release(project_root, sequence, **overrides):
    values = {
        "model_id": "pump-a",
        "version_id": "v1",
        "release_id": f"release-{sequence:03d}",
        "action": "activate",
        "expected_current_release_id": None,
        "rollback_of_release_id": None,
        "reason": "Production release",
        "principal": EXPERT,
        "operation_id": f"op-release-{sequence:03d}",
        "request_id": f"req-release-{sequence:03d}",
        "idempotency_key": f"idem-release-{sequence:03d}",
    }
    values.update(overrides)
    return release_model_version(project_root, **values)


def test_activate_upgrade_and_rollback_append_history_without_mutating_versions(
    tmp_path,
):
    _create_versions(tmp_path)
    version_bytes = _snapshot_version_bytes(tmp_path)

    first = _release(tmp_path, 1)
    upgraded = _release(
        tmp_path,
        2,
        version_id="v2",
        expected_current_release_id="release-001",
        reason="Upgrade to v2",
    )
    rolled_back = _release(
        tmp_path,
        3,
        action="rollback",
        expected_current_release_id="release-002",
        rollback_of_release_id="release-001",
        reason="Regression in v2",
    )

    assert first["version_id"] == "v1"
    assert upgraded["version_id"] == "v2"
    assert rolled_back["previous_release_id"] == "release-002"
    assert rolled_back["rollback_of_release_id"] == "release-001"
    assert load_current_model_release(tmp_path, "pump-a") == rolled_back
    assert [
        release["release_id"]
        for release in list_model_releases(tmp_path, "pump-a")
    ] == ["release-001", "release-002", "release-003"]
    assert _snapshot_version_bytes(tmp_path) == version_bytes
    statuses = list_version_release_status(tmp_path, "pump-a")
    assert [
        (item["version_id"], item["is_current"]) for item in statuses
    ] == [("v1", True), ("v2", False)]
    assert statuses[1]["supersedes_version_id"] == "v1"
    assert all(item["manifest_fingerprint"] for item in statuses)
    assert statuses[0]["release_count"] == 2
    assert statuses[0]["latest_release_id"] == "release-003"
    assert statuses[0]["latest_release_action"] == "rollback"
    assert statuses[1]["release_count"] == 1
    assert statuses[1]["latest_release_id"] == "release-002"
    assert statuses[1]["latest_release_action"] == "activate"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-release-003")
    ] == [
        "operation.started",
        "model_release.prepared",
        "model_release.rolled_back",
        "operation.completed",
    ]


def test_stale_expected_release_is_rejected_and_audited(tmp_path):
    _create_versions(tmp_path)
    _release(tmp_path, 1)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(
            tmp_path,
            2,
            version_id="v2",
            expected_current_release_id=None,
        )

    assert exc_info.value.code == "stale_model_release"
    assert read_operation_events(tmp_path, "op-release-002")[-1][
        "event_type"
    ] == "operation.failed"


def test_non_expert_cannot_release_model_version(tmp_path):
    _create_versions(tmp_path)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1, principal=OPERATOR)

    assert exc_info.value.code == "permission_denied"
    assert load_current_model_release(tmp_path, "pump-a") is None


def test_invalid_reason_is_rejected_before_publication(tmp_path):
    _create_versions(tmp_path)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1, reason="   ")

    assert exc_info.value.code == "invalid_model_release"
    assert load_current_model_release(tmp_path, "pump-a") is None


def test_rollback_cannot_target_the_current_release(tmp_path):
    _create_versions(tmp_path)
    _release(tmp_path, 1)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(
            tmp_path,
            2,
            action="rollback",
            expected_current_release_id="release-001",
            rollback_of_release_id="release-001",
        )

    assert exc_info.value.code == "invalid_model_release"
    assert load_current_model_release(tmp_path, "pump-a")["release_id"] == (
        "release-001"
    )


def test_duplicate_release_identity_does_not_replace_history(tmp_path):
    _create_versions(tmp_path)
    first = _release(tmp_path, 1)

    with pytest.raises(ModelMatchingError):
        _release(
            tmp_path,
            2,
            release_id="release-001",
            version_id="v2",
            expected_current_release_id="release-001",
        )

    assert list_model_releases(tmp_path, "pump-a") == [first]


def test_same_release_request_replays_without_duplicate_history(tmp_path):
    _create_versions(tmp_path)

    first = _release(tmp_path, 1)
    replayed = _release(tmp_path, 1)

    assert replayed == first
    assert [item["release_id"] for item in list_model_releases(tmp_path, "pump-a")] == [
        "release-001"
    ]


def test_running_idempotent_replay_lock_timeout_does_not_fail_operation(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    request_payload = {
        "model_id": "pump-a",
        "version_id": "v1",
        "release_id": "release-001",
        "expected_current_release_id": None,
        "rollback_of_release_id": None,
        "action": "activate",
        "reason": "Production release",
    }
    start_operation(
        tmp_path,
        operation_id="op-release-001",
        operation_type="model_release.change",
        principal=EXPERT,
        request_id="req-release-001",
        idempotency_key="idem-release-001",
        request_payload=request_payload,
    )

    @contextmanager
    def busy_lock(*_args, **_kwargs):
        raise ModelMatchingError("operation_busy", "release lock busy")
        yield

    monkeypatch.setattr(release_module, "model_resource_lock", busy_lock)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)

    assert exc_info.value.code == "operation_busy"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"


def test_tampered_current_projection_fails_closed(tmp_path):
    _create_versions(tmp_path)
    _release(tmp_path, 1)
    projection_path = tmp_path / "models" / "pump-a" / "current_release.json"
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection["current_release_id"] = "release-999"
    projection_path.write_text(json.dumps(projection), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        load_current_model_release(tmp_path, "pump-a")

    assert exc_info.value.code == "model_release_integrity_error"


def test_only_one_concurrent_release_advances_expected_head(tmp_path):
    _create_versions(tmp_path)
    _release(tmp_path, 1)

    def attempt(sequence):
        try:
            return _release(
                tmp_path,
                sequence,
                version_id="v2",
                expected_current_release_id="release-001",
            )["release_id"]
        except ModelMatchingError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [2, 3]))

    assert sum(result.startswith("release-") for result in results) == 1
    assert results.count("stale_model_release") == 1
    assert len(list_model_releases(tmp_path, "pump-a")) == 2


def test_retry_recovers_visible_release_before_projection(tmp_path, monkeypatch):
    _create_versions(tmp_path)
    original_write_json = release_module.write_json

    def fail_projection(value, path):
        if path.name == "current_release.json":
            raise OSError("projection unavailable")
        return original_write_json(value, path)

    monkeypatch.setattr(release_module, "write_json", fail_projection)
    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)
    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"

    monkeypatch.setattr(release_module, "write_json", original_write_json)
    recovered = _release(tmp_path, 1)

    assert recovered["release_id"] == "release-001"
    assert load_current_model_release(tmp_path, "pump-a") == recovered
    assert load_operation(tmp_path, "op-release-001")["status"] == "completed"


def test_retry_recovers_owner_visible_after_directory_sync_failure(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_fsync = release_module._fsync_directory
    failed = False

    def fail_owner_directory_sync_once(path):
        nonlocal failed
        if not failed and path.name == "release-001":
            failed = True
            raise OSError("owner directory sync unavailable")
        return original_fsync(path)

    monkeypatch.setattr(
        release_module, "_fsync_directory", fail_owner_directory_sync_once
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)

    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"

    monkeypatch.setattr(release_module, "_fsync_directory", original_fsync)
    recovered = _release(tmp_path, 1)

    assert recovered["release_id"] == "release-001"
    assert load_operation(tmp_path, "op-release-001")["status"] == "completed"


def test_other_request_is_blocked_by_owner_only_candidate(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_fsync = release_module._fsync_directory
    failed = False

    def fail_owner_directory_sync_once(path):
        nonlocal failed
        if not failed and path.name == "release-001":
            failed = True
            raise OSError("owner directory sync unavailable")
        return original_fsync(path)

    monkeypatch.setattr(
        release_module, "_fsync_directory", fail_owner_directory_sync_once
    )
    with pytest.raises(ModelMatchingError):
        _release(tmp_path, 1)
    monkeypatch.setattr(release_module, "_fsync_directory", original_fsync)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 2, version_id="v2")

    assert exc_info.value.code == "publication_recovery_required"
    releases_root = tmp_path / "models" / "pump-a" / "releases"
    assert sorted(path.name for path in releases_root.iterdir()) == [
        "release-001"
    ]


def test_recovery_rejects_release_that_differs_from_canonical_start(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_write_json = release_module.write_json

    def fail_projection(value, path):
        if path.name == "current_release.json":
            raise OSError("projection unavailable")
        return original_write_json(value, path)

    monkeypatch.setattr(release_module, "write_json", fail_projection)
    with pytest.raises(ModelMatchingError):
        _release(tmp_path, 1)
    release_path = (
        tmp_path
        / "models"
        / "pump-a"
        / "releases"
        / "release-001"
        / "release.json"
    )
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["actor_id"] = "mallory"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    monkeypatch.setattr(release_module, "write_json", original_write_json)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)

    assert exc_info.value.code == "model_release_integrity_error"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"


def test_release_visible_with_unreadable_owner_keeps_operation_running(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_write_json = release_module.write_json

    def fail_projection(value, path):
        if path.name == "current_release.json":
            raise OSError("projection unavailable")
        return original_write_json(value, path)

    monkeypatch.setattr(release_module, "write_json", fail_projection)
    with pytest.raises(ModelMatchingError):
        _release(tmp_path, 1)
    owner_path = (
        tmp_path
        / "models"
        / "pump-a"
        / "releases"
        / "release-001"
        / "operation_owner.json"
    )
    owner_path.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(release_module, "write_json", original_write_json)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)

    assert exc_info.value.code == "model_release_integrity_error"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"


def test_release_visible_with_structurally_valid_tampered_owner_keeps_running(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_write_json = release_module.write_json

    def fail_projection(value, path):
        if path.name == "current_release.json":
            raise OSError("projection unavailable")
        return original_write_json(value, path)

    monkeypatch.setattr(release_module, "write_json", fail_projection)
    with pytest.raises(ModelMatchingError):
        _release(tmp_path, 1)
    owner_path = (
        tmp_path
        / "models"
        / "pump-a"
        / "releases"
        / "release-001"
        / "operation_owner.json"
    )
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["request_id"] = "req-tampered"
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    monkeypatch.setattr(release_module, "write_json", original_write_json)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)

    assert exc_info.value.code == "model_release_integrity_error"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"


def test_complete_foreign_evidence_can_fail_colliding_request(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_write_json = release_module.write_json

    def fail_projection(value, path):
        if path.name == "current_release.json":
            raise OSError("projection unavailable")
        return original_write_json(value, path)

    monkeypatch.setattr(release_module, "write_json", fail_projection)
    with pytest.raises(ModelMatchingError):
        _release(tmp_path, 1)
    monkeypatch.setattr(release_module, "write_json", original_write_json)

    with pytest.raises(ModelMatchingError):
        _release(
            tmp_path,
            2,
            release_id="release-001",
            version_id="v2",
        )

    assert load_operation(tmp_path, "op-release-001")["status"] == "running"
    assert load_operation(tmp_path, "op-release-002")["status"] == "failed"
    release_path = (
        tmp_path
        / "models"
        / "pump-a"
        / "releases"
        / "release-001"
        / "release.json"
    )
    assert json.loads(release_path.read_text(encoding="utf-8"))[
        "operation_id"
    ] == "op-release-001"


def test_successor_with_earlier_started_time_remains_graph_head(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    monkeypatch.setattr(
        audit_module, "utc_now", lambda: "2026-08-27T10:00:02+00:00"
    )
    first = _release(tmp_path, 1)
    monkeypatch.setattr(
        audit_module, "utc_now", lambda: "2026-08-27T10:00:01+00:00"
    )
    second = _release(
        tmp_path,
        2,
        version_id="v2",
        expected_current_release_id=first["release_id"],
    )

    assert load_current_model_release(tmp_path, "pump-a") == second
    assert [
        release["release_id"]
        for release in list_model_releases(tmp_path, "pump-a")
    ] == ["release-002", "release-001"]


def test_retry_recovers_projection_before_business_event(tmp_path, monkeypatch):
    _create_versions(tmp_path)
    original_ensure = release_module.ensure_operation_event

    def fail_business_event(project_root, operation_id, event_type, details):
        if event_type == "model_release.published":
            raise ModelMatchingError(
                "audit_persistence_error", "business event unavailable"
            )
        return original_ensure(project_root, operation_id, event_type, details)

    monkeypatch.setattr(
        release_module, "ensure_operation_event", fail_business_event
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)
    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"

    monkeypatch.setattr(release_module, "ensure_operation_event", original_ensure)
    recovered = _release(tmp_path, 1)

    assert load_current_model_release(tmp_path, "pump-a") == recovered
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-release-001")
    ].count("model_release.published") == 1


def test_retry_reclassifies_after_business_event_before_completion(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_complete = release_module.complete_operation

    def fail_completion(*_args, **_kwargs):
        raise ModelMatchingError(
            "audit_persistence_error", "completion unavailable"
        )

    monkeypatch.setattr(release_module, "complete_operation", fail_completion)
    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 1)
    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-release-001")
    ].count("model_release.published") == 1

    original_ensure = release_module.ensure_operation_event

    def reject_duplicate_business_event(
        project_root, operation_id, event_type, details
    ):
        if event_type == "model_release.published":
            raise AssertionError("business event must not be ensured twice")
        return original_ensure(project_root, operation_id, event_type, details)

    monkeypatch.setattr(release_module, "complete_operation", original_complete)
    monkeypatch.setattr(
        release_module, "ensure_operation_event", reject_duplicate_business_event
    )
    recovered = _release(tmp_path, 1)

    assert recovered["release_id"] == "release-001"
    assert load_operation(tmp_path, "op-release-001")["status"] == "completed"


def test_foreign_release_cannot_branch_from_pending_visible_release(
    tmp_path, monkeypatch
):
    _create_versions(tmp_path)
    original_write_json = release_module.write_json

    def fail_projection(value, path):
        if path.name == "current_release.json":
            raise OSError("projection unavailable")
        return original_write_json(value, path)

    monkeypatch.setattr(release_module, "write_json", fail_projection)
    with pytest.raises(ModelMatchingError):
        _release(tmp_path, 1)
    monkeypatch.setattr(release_module, "write_json", original_write_json)

    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 2, version_id="v2")

    assert exc_info.value.code == "publication_recovery_required"
    releases_root = tmp_path / "models" / "pump-a" / "releases"
    assert sorted(path.name for path in releases_root.iterdir()) == [
        "release-001"
    ]


def test_release_record_is_bound_to_verified_audit_event(tmp_path):
    _create_versions(tmp_path)
    _release(tmp_path, 1)
    release_path = (
        tmp_path
        / "models"
        / "pump-a"
        / "releases"
        / "release-001"
        / "release.json"
    )
    value = json.loads(release_path.read_text(encoding="utf-8"))
    value["reason"] = "Tampered but structurally valid"
    release_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as exc_info:
        list_model_releases(tmp_path, "pump-a")

    assert exc_info.value.code == "model_release_integrity_error"


def test_failure_audit_persistence_error_is_not_hidden(tmp_path, monkeypatch):
    _create_versions(tmp_path)
    _release(tmp_path, 1)

    def fail_audit(*_args, **_kwargs):
        raise ModelMatchingError(
            "audit_persistence_error", "failure audit unavailable"
        )

    monkeypatch.setattr(release_module, "fail_operation", fail_audit)
    with pytest.raises(ModelMatchingError) as exc_info:
        _release(tmp_path, 2, expected_current_release_id=None)

    assert exc_info.value.code == "audit_persistence_error"
