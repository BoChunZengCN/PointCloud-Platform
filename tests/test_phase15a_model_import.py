import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pc_system.model_import import (
    fingerprint_file,
    import_model_version,
    list_model_versions,
    load_model_version,
)
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import load_operation, read_operation_events
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def create_asset(project):
    create_model_asset(
        project,
        model_id="pump-a",
        display_name="Pump A",
        category_id="pump",
        manufacturer="Acme",
        model_number="A-100",
        keywords=["centrifugal"],
        tags=["pump"],
        principal=EXPERT,
        operation_id="op-asset-001",
        request_id="request-asset-001",
        idempotency_key="idem-asset-001",
    )


def fake_reader(_path):
    return {
        "vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
        "faces": [[0, 1, 2]],
    }


def test_import_publishes_immutable_version_and_source_copy(tmp_path):
    create_asset(tmp_path)
    version = import_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={"supplier": "Acme"},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=fake_reader,
    )
    root = tmp_path / "models" / "pump-a" / "versions" / "v1"
    assert version["status"] == "imported"
    assert version["source_format"] == "obj"
    assert version["coordinate_unit"] == "m"
    assert (root / version["artifacts"]["source"]).read_bytes() == FIXTURE.read_bytes()
    assert [item["version_id"] for item in list_model_versions(tmp_path, "pump-a")] == [
        "v1"
    ]


def test_same_idempotent_import_returns_existing_version(tmp_path):
    create_asset(tmp_path)
    arguments = dict(
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={"supplier": "Acme"},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=fake_reader,
    )
    first = import_model_version(tmp_path, **arguments)
    second = import_model_version(tmp_path, **arguments)
    assert second == first


def test_idempotent_replay_rejects_changed_source_bytes(tmp_path):
    create_asset(tmp_path)
    source = tmp_path / "input.obj"
    source.write_bytes(FIXTURE.read_bytes())
    arguments = dict(
        model_id="pump-a",
        version_id="v1",
        source_path=source,
        declared_unit="mm",
        license_name="internal",
        provenance={},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=fake_reader,
    )
    first = import_model_version(tmp_path, **arguments)
    source.write_text(
        "v 0 0 0\nv 2 0 0\nv 0 2 0\nf 1 2 3\n", encoding="utf-8"
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **arguments)
    assert exc_info.value.code == "idempotency_conflict"
    persisted = load_model_version(tmp_path, "pump-a", "v1")
    assert persisted["source_fingerprint"] == first["source_fingerprint"]


def test_existing_version_cannot_be_overwritten(tmp_path):
    create_asset(tmp_path)
    import_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=fake_reader,
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path,
            model_id="pump-a",
            version_id="v1",
            source_path=FIXTURE,
            declared_unit="m",
            license_name="internal",
            provenance={},
            principal=EXPERT,
            operation_id="op-import-002",
            request_id="request-import-002",
            idempotency_key="idem-import-002",
            mesh_reader=fake_reader,
        )
    assert exc_info.value.code == "model_version_exists"


def test_new_version_can_explicitly_supersede_existing_version(tmp_path):
    create_asset(tmp_path)
    common = dict(
        model_id="pump-a",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={},
        principal=EXPERT,
        mesh_reader=fake_reader,
    )
    import_model_version(
        tmp_path,
        version_id="v1",
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        **common,
    )
    version = import_model_version(
        tmp_path,
        version_id="v2",
        supersedes_version_id="v1",
        operation_id="op-import-002",
        request_id="request-import-002",
        idempotency_key="idem-import-002",
        **common,
    )
    assert version["supersedes_version_id"] == "v1"


def test_failed_inspection_leaves_no_final_version(tmp_path):
    create_asset(tmp_path)

    def failing_reader(_path):
        raise ModelMatchingError("invalid_model_geometry", "broken mesh")

    with pytest.raises(ModelMatchingError):
        import_model_version(
            tmp_path,
            model_id="pump-a",
            version_id="v1",
            source_path=FIXTURE,
            declared_unit="mm",
            license_name="internal",
            provenance={},
            principal=EXPERT,
            operation_id="op-import-001",
            request_id="request-import-001",
            idempotency_key="idem-import-001",
            mesh_reader=failing_reader,
        )
    assert not (tmp_path / "models" / "pump-a" / "versions" / "v1").exists()
    assert (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-import-001"
        / "operation.json"
    ).is_file()


def test_replay_recovers_version_published_before_audit_completion(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_append = model_import_module.append_operation_event
    failed_once = {"value": False}

    def interrupted_append(project_root, operation_id, event_type, details):
        if event_type == "model_version.published" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated audit interruption")
        return original_append(project_root, operation_id, event_type, details)

    monkeypatch.setattr(
        model_import_module, "append_operation_event", interrupted_append
    )
    arguments = dict(
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=fake_reader,
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **arguments)
    assert exc_info.value.code == "publication_recovery_required"
    assert (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / "v1"
        / "model_manifest.json"
    ).is_file()
    monkeypatch.setattr(
        model_import_module, "append_operation_event", original_append
    )
    recovered = import_model_version(tmp_path, **arguments)
    assert recovered["version_id"] == "v1"
    assert load_operation(tmp_path, "op-import-001")["status"] == "completed"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.prepared") == 1
    assert event_types.count("model_version.published") == 1
    assert event_types.count("operation.completed") == 1


def test_concurrent_replay_before_source_fingerprint_is_operation_busy(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    captured = threading.Event()
    release = threading.Event()
    original_capture = model_import_module._capture_source

    def delayed_capture(source, destination, **kwargs):
        fingerprint = original_capture(source, destination, **kwargs)
        captured.set()
        assert release.wait(timeout=5)
        return fingerprint

    monkeypatch.setattr(
        model_import_module, "_capture_source", delayed_capture
    )
    arguments = dict(
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=fake_reader,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(import_model_version, tmp_path, **arguments)
        assert captured.wait(timeout=5)
        try:
            with pytest.raises(ModelMatchingError) as exc_info:
                import_model_version(tmp_path, **arguments)
            assert exc_info.value.code == "operation_busy"
        finally:
            release.set()
        assert first.result(timeout=5)["version_id"] == "v1"


def test_import_never_deletes_preexisting_unowned_staging(tmp_path):
    create_asset(tmp_path)
    staging = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / ".p15-model-op-import-001"
    )
    staging.mkdir(parents=True)
    (staging / ".operation-owner.json").write_text(
        json.dumps(
            {"schema_version": "1.0", "operation_id": "op-import-001"}
        ),
        encoding="utf-8",
    )
    sentinel = staging / "preexisting.txt"
    sentinel.write_text("must survive", encoding="utf-8")

    with pytest.raises(ModelMatchingError):
        import_model_version(
            tmp_path,
            model_id="pump-a",
            version_id="v1",
            source_path=FIXTURE,
            declared_unit="mm",
            license_name="internal",
            provenance={},
            principal=EXPERT,
            operation_id="op-import-001",
            request_id="request-import-001",
            idempotency_key="idem-import-001",
            mesh_reader=fake_reader,
        )

    assert sentinel.read_text(encoding="utf-8") == "must survive"


def test_ambiguous_rename_never_deletes_same_name_replacement(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    replacement = {}
    original_rename = Path.rename

    def rename_then_replace(path, target):
        result = original_rename(path, target)
        if path.name == ".p15-model-op-import-001":
            path.mkdir()
            (path / ".operation-owner.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "operation_id": "op-import-001",
                    }
                ),
                encoding="utf-8",
            )
            sentinel = path / "replacement.txt"
            sentinel.write_text("replacement", encoding="utf-8")
            replacement["sentinel"] = sentinel
            raise OSError("rename acknowledgement interrupted")
        return result

    monkeypatch.setattr(model_import_module.Path, "rename", rename_then_replace)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path,
            model_id="pump-a",
            version_id="v1",
            source_path=FIXTURE,
            declared_unit="mm",
            license_name="internal",
            provenance={},
            principal=EXPERT,
            operation_id="op-import-001",
            request_id="request-import-001",
            idempotency_key="idem-import-001",
            mesh_reader=fake_reader,
        )

    assert exc_info.value.code == "publication_recovery_required"
    assert replacement["sentinel"].read_text(encoding="utf-8") == "replacement"
    assert (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / "v1"
        / ".operation-owner.json"
    ).is_file()
    assert load_operation(tmp_path, "op-import-001")["status"] == "running"

    recovered = import_model_version(tmp_path, **import_arguments())

    assert recovered["version_id"] == "v1"
    assert replacement["sentinel"].read_text(encoding="utf-8") == "replacement"
    assert load_operation(tmp_path, "op-import-001")["status"] == "completed"


def test_replay_recovers_prepared_staging_after_rename_throws_before_move(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_rename = Path.rename
    interrupted = False

    def interrupt_before_move(path, target):
        nonlocal interrupted
        if path.name == ".p15-model-op-import-001" and not interrupted:
            interrupted = True
            raise OSError("rename failed before move")
        return original_rename(path, target)

    monkeypatch.setattr(model_import_module.Path, "rename", interrupt_before_move)
    arguments = import_arguments()
    with pytest.raises(ModelMatchingError) as first_error:
        import_model_version(tmp_path, **arguments)
    assert first_error.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-import-001")["status"] == "running"

    recovered = import_model_version(tmp_path, **arguments)

    assert recovered["version_id"] == "v1"
    assert load_operation(tmp_path, "op-import-001")["status"] == "completed"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.prepared") == 1
    assert event_types.count("model_version.published") == 1
    assert event_types.count("operation.completed") == 1


@pytest.mark.parametrize("source_change", ["deleted", "changed"])
def test_final_recovery_precedes_replay_source_check(
    tmp_path, monkeypatch, source_change
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    source = tmp_path / "input.obj"
    source.write_bytes(FIXTURE.read_bytes())
    original_rename = Path.rename
    interrupted = False

    def interrupt_after_move(path, target):
        nonlocal interrupted
        result = original_rename(path, target)
        if path.name == ".p15-model-op-import-001" and not interrupted:
            interrupted = True
            raise OSError("rename acknowledgement interrupted")
        return result

    monkeypatch.setattr(model_import_module.Path, "rename", interrupt_after_move)
    arguments = import_arguments(source_path=source)
    with pytest.raises(ModelMatchingError) as first_error:
        import_model_version(tmp_path, **arguments)
    assert first_error.value.code == "publication_recovery_required"
    if source_change == "deleted":
        source.unlink()
    else:
        source.write_text(
            "v 0 0 0\nv 2 0 0\nv 0 2 0\nf 1 2 3\n", encoding="utf-8"
        )

    with pytest.raises(ModelMatchingError) as replay_error:
        import_model_version(tmp_path, **arguments)

    assert replay_error.value.code == "idempotency_conflict"
    assert load_operation(tmp_path, "op-import-001")["status"] == "completed"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.published") == 1
    assert event_types.count("operation.completed") == 1
    audit_roots = list(
        (tmp_path / "reports" / "model_matching_operations").glob("audit-import-*")
    )
    assert len(audit_roots) == 1
    replay_audit = load_operation(tmp_path, audit_roots[0].name)
    assert replay_audit["status"] == "failed"
    assert replay_audit["error"]["code"] == "idempotency_conflict"


def test_unprovable_running_publication_becomes_terminal_recovery_required(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_rename = Path.rename
    interrupted = False

    def interrupt_before_move(path, target):
        nonlocal interrupted
        if path.name == ".p15-model-op-import-001" and not interrupted:
            interrupted = True
            raise OSError("rename failed before move")
        return original_rename(path, target)

    monkeypatch.setattr(model_import_module.Path, "rename", interrupt_before_move)
    arguments = import_arguments()
    with pytest.raises(ModelMatchingError):
        import_model_version(tmp_path, **arguments)
    staging = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / ".p15-model-op-import-001"
    )
    displaced = staging.with_name("foreign-staging")
    original_rename(staging, displaced)

    with pytest.raises(ModelMatchingError) as replay_error:
        import_model_version(tmp_path, **arguments)

    assert replay_error.value.code == "publication_recovery_required"
    assert displaced.is_dir()
    operation = load_operation(tmp_path, "op-import-001")
    assert operation["status"] == "failed"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.recovery_required") == 1
    assert event_types.count("operation.failed") == 1

    with pytest.raises(ModelMatchingError) as repeated_error:
        import_model_version(tmp_path, **arguments)
    assert repeated_error.value.code == "publication_recovery_required"
    repeated_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert repeated_types.count("model_version.recovery_required") == 1
    assert repeated_types.count("operation.failed") == 1


def test_failed_replay_checks_existing_source_evidence_before_old_error(tmp_path):
    create_asset(tmp_path)
    source = tmp_path / "input.obj"
    source.write_bytes(FIXTURE.read_bytes())

    def failing_reader(_path):
        raise ModelMatchingError("invalid_model_geometry", "broken mesh")

    arguments = dict(
        model_id="pump-a",
        version_id="v1",
        source_path=source,
        declared_unit="mm",
        license_name="internal",
        provenance={},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=failing_reader,
    )
    with pytest.raises(ModelMatchingError) as first_error:
        import_model_version(tmp_path, **arguments)
    assert first_error.value.code == "invalid_model_geometry"
    source.write_text(
        "v 0 0 0\nv 2 0 0\nv 0 2 0\nf 1 2 3\n", encoding="utf-8"
    )

    with pytest.raises(ModelMatchingError) as replay_error:
        import_model_version(tmp_path, **arguments)

    assert replay_error.value.code == "idempotency_conflict"


def test_failed_replay_without_source_evidence_returns_original_error(tmp_path):
    source = tmp_path / "input.obj"
    source.write_bytes(FIXTURE.read_bytes())
    arguments = import_arguments(source_path=source)

    with pytest.raises(ModelMatchingError) as first_error:
        import_model_version(tmp_path, **arguments)
    assert first_error.value.code == "model_not_found"
    source.unlink()

    with pytest.raises(ModelMatchingError) as replay_error:
        import_model_version(tmp_path, **arguments)

    assert replay_error.value.code == "model_not_found"


def test_failure_double_busy_preserves_operation_busy(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)

    def failing_reader(_path):
        raise ModelMatchingError("invalid_model_geometry", "broken mesh")

    def busy(*_args, **_kwargs):
        raise ModelMatchingError("operation_busy", "operation is busy")

    monkeypatch.setattr(model_import_module, "fail_operation", busy)
    monkeypatch.setattr(model_import_module, "load_operation", busy)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path,
            model_id="pump-a",
            version_id="v1",
            source_path=FIXTURE,
            declared_unit="mm",
            license_name="internal",
            provenance={},
            principal=EXPERT,
            operation_id="op-import-001",
            request_id="request-import-001",
            idempotency_key="idem-import-001",
            mesh_reader=failing_reader,
        )

    assert exc_info.value.code == "operation_busy"


def test_completion_double_busy_preserves_operation_busy(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_append = model_import_module.append_operation_event
    interrupted = False

    def interrupt_publish(project_root, operation_id, event_type, details):
        nonlocal interrupted
        if event_type == "model_version.published" and not interrupted:
            interrupted = True
            raise OSError("publish audit interrupted")
        return original_append(project_root, operation_id, event_type, details)

    monkeypatch.setattr(model_import_module, "append_operation_event", interrupt_publish)
    arguments = dict(
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={},
        principal=EXPERT,
        operation_id="op-import-001",
        request_id="request-import-001",
        idempotency_key="idem-import-001",
        mesh_reader=fake_reader,
    )
    with pytest.raises(ModelMatchingError):
        import_model_version(tmp_path, **arguments)
    manifest = load_model_version(tmp_path, "pump-a", "v1")

    def busy(*_args, **_kwargs):
        raise ModelMatchingError("operation_busy", "operation is busy")

    monkeypatch.setattr(model_import_module, "ensure_operation_event", lambda *_a, **_k: {})
    monkeypatch.setattr(model_import_module, "complete_operation", busy)
    monkeypatch.setattr(model_import_module, "load_operation", busy)

    with pytest.raises(ModelMatchingError) as exc_info:
        model_import_module._complete_recovery(
            tmp_path, "op-import-001", manifest
        )

    assert exc_info.value.code == "operation_busy"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.prepared") == 1
    assert event_types.count("model_version.published") == 0
    assert event_types.count("operation.completed") == 0


def import_arguments(**overrides):
    arguments = {
        "model_id": "pump-a",
        "version_id": "v1",
        "source_path": FIXTURE,
        "declared_unit": "mm",
        "license_name": "internal",
        "provenance": {},
        "principal": EXPERT,
        "operation_id": "op-import-001",
        "request_id": "request-import-001",
        "idempotency_key": "idem-import-001",
        "mesh_reader": fake_reader,
    }
    arguments.update(overrides)
    return arguments


def test_prepared_audit_interruption_rolls_back_and_fails_operation(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_append = model_import_module.append_operation_event

    def interrupt_prepared(project_root, operation_id, event_type, details):
        if event_type == "model_version.prepared":
            raise OSError("prepared audit interrupted")
        return original_append(project_root, operation_id, event_type, details)

    monkeypatch.setattr(model_import_module, "append_operation_event", interrupt_prepared)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments())

    assert exc_info.value.code == "audit_persistence_error"
    assert not (
        tmp_path / "models" / "pump-a" / "versions" / "v1"
    ).exists()
    assert load_operation(tmp_path, "op-import-001")["status"] == "failed"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.prepared") == 0
    assert event_types.count("model_version.published") == 0
    assert event_types.count("operation.completed") == 0
    assert event_types.count("operation.failed") == 1


def test_completed_audit_interruption_recovers_without_duplicate_events(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_complete = model_import_module.complete_operation
    interrupted = False

    def interrupt_complete(*args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise OSError("completion audit interrupted")
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(model_import_module, "complete_operation", interrupt_complete)
    arguments = import_arguments()

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **arguments)
    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-import-001")["status"] == "running"

    recovered = import_model_version(tmp_path, **arguments)

    assert recovered["version_id"] == "v1"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.prepared") == 1
    assert event_types.count("model_version.published") == 1
    assert event_types.count("operation.completed") == 1


def test_double_recovery_never_duplicates_terminal_events(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_append = model_import_module.append_operation_event
    interrupted = False

    def interrupt_publish(project_root, operation_id, event_type, details):
        nonlocal interrupted
        if event_type == "model_version.published" and not interrupted:
            interrupted = True
            raise OSError("publication audit interrupted")
        return original_append(project_root, operation_id, event_type, details)

    monkeypatch.setattr(model_import_module, "append_operation_event", interrupt_publish)
    arguments = import_arguments()
    with pytest.raises(ModelMatchingError):
        import_model_version(tmp_path, **arguments)
    monkeypatch.setattr(model_import_module, "append_operation_event", original_append)

    def recover(_index):
        try:
            return "ok", import_model_version(tmp_path, **arguments)
        except ModelMatchingError as exc:
            return "error", exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(recover, range(2)))

    assert any(status == "ok" for status, _ in outcomes)
    assert all(
        status == "ok" or value.code == "operation_busy"
        for status, value in outcomes
    )
    assert load_operation(tmp_path, "op-import-001")["status"] == "completed"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.prepared") == 1
    assert event_types.count("model_version.published") == 1
    assert event_types.count("operation.completed") == 1


def test_failure_terminal_acknowledgement_preserves_business_error(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_fail = model_import_module.fail_operation

    def fail_then_interrupt(*args, **kwargs):
        original_fail(*args, **kwargs)
        raise OSError("failure acknowledgement interrupted")

    def failing_reader(_path):
        raise ModelMatchingError("invalid_model_geometry", "broken mesh")

    monkeypatch.setattr(model_import_module, "fail_operation", fail_then_interrupt)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path, **import_arguments(mesh_reader=failing_reader)
        )

    assert exc_info.value.code == "invalid_model_geometry"
    assert load_operation(tmp_path, "op-import-001")["status"] == "failed"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("operation.failed") == 1


def test_completion_terminal_acknowledgement_is_coordinated_as_success(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    original_complete = model_import_module.complete_operation

    def complete_then_interrupt(*args, **kwargs):
        original_complete(*args, **kwargs)
        raise OSError("completion acknowledgement interrupted")

    monkeypatch.setattr(
        model_import_module, "complete_operation", complete_then_interrupt
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments())
    assert exc_info.value.code == "publication_recovery_required"

    recovered = import_model_version(tmp_path, **import_arguments())

    assert recovered["version_id"] == "v1"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("operation.completed") == 1


@pytest.mark.parametrize("damage", ["manifest", "source", "audit"])
def test_load_and_list_fail_closed_on_version_corruption(tmp_path, damage):
    create_asset(tmp_path)
    import_model_version(tmp_path, **import_arguments())
    root = tmp_path / "models" / "pump-a" / "versions" / "v1"
    if damage == "manifest":
        manifest_path = root / "model_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_path"] = "../outside.obj"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif damage == "source":
        (root / "source" / "model.obj").write_bytes(b"changed")
    else:
        events_path = (
            tmp_path
            / "reports"
            / "model_matching_operations"
            / "op-import-001"
            / "events.jsonl"
        )
        events = events_path.read_text(encoding="utf-8")
        events_path.write_text(events.replace("pump-a", "pump-b", 1), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as load_error:
        load_model_version(tmp_path, "pump-a", "v1")
    with pytest.raises(ModelMatchingError) as list_error:
        list_model_versions(tmp_path, "pump-a")

    assert load_error.value.code == "model_version_integrity_error"
    assert list_error.value.code == "model_version_integrity_error"


def test_dynamic_source_path_is_not_coerced_and_failure_is_audited(tmp_path):
    create_asset(tmp_path)

    class DynamicPath:
        calls = 0

        def __fspath__(self):
            self.calls += 1
            raise AssertionError("must not coerce")

        def __str__(self):
            self.calls += 1
            raise AssertionError("must not coerce")

    source = DynamicPath()

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path, **import_arguments(source_path=source)
        )

    assert exc_info.value.code == "invalid_model_version"
    assert source.calls == 0
    assert load_operation(tmp_path, "op-import-001")["status"] == "failed"


def test_load_and_list_reject_symlinked_source_artifact(tmp_path):
    create_asset(tmp_path)
    import_model_version(tmp_path, **import_arguments())
    source = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / "v1"
        / "source"
        / "model.obj"
    )
    source.unlink()
    try:
        source.symlink_to(FIXTURE)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ModelMatchingError) as load_error:
        load_model_version(tmp_path, "pump-a", "v1")
    with pytest.raises(ModelMatchingError) as list_error:
        list_model_versions(tmp_path, "pump-a")

    assert load_error.value.code == "model_version_integrity_error"
    assert list_error.value.code == "model_version_integrity_error"


def test_load_and_list_reject_symlinked_models_ancestor(tmp_path):
    real_project = tmp_path / "real"
    create_asset(real_project)
    import_model_version(real_project, **import_arguments())
    linked_project = tmp_path / "linked"
    linked_project.mkdir()
    models_link = linked_project / "models"
    try:
        models_link.symlink_to(real_project / "models", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")

    with pytest.raises(ModelMatchingError) as load_error:
        load_model_version(linked_project, "pump-a", "v1")
    with pytest.raises(ModelMatchingError) as list_error:
        list_model_versions(linked_project, "pump-a")

    assert load_error.value.code == "model_version_integrity_error"
    assert list_error.value.code == "model_version_integrity_error"


def test_public_path_helpers_return_stable_model_errors(tmp_path):
    with pytest.raises(ModelMatchingError) as type_error:
        fingerprint_file(object())
    with pytest.raises(ModelMatchingError) as missing_error:
        fingerprint_file(tmp_path / "missing.obj")
    with pytest.raises(ModelMatchingError) as root_error:
        list_model_versions("bad\0root", "pump-a")

    assert type_error.value.code == "invalid_model_path"
    assert missing_error.value.code == "model_file_error"
    assert root_error.value.code == "invalid_project_root"


def test_fingerprint_file_does_not_catch_base_exception(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    source = tmp_path / "source.obj"
    source.write_bytes(b"source")

    def interrupt(_path):
        raise KeyboardInterrupt()

    monkeypatch.setattr(model_import_module, "_fingerprint_path", interrupt)

    with pytest.raises(KeyboardInterrupt):
        fingerprint_file(source)


def test_project_root_does_not_catch_base_exception(monkeypatch):
    import pc_system.model_import as model_import_module

    def interrupt(_value, _error_code):
        raise KeyboardInterrupt()

    monkeypatch.setattr(model_import_module, "_exact_path", interrupt)

    with pytest.raises(KeyboardInterrupt):
        list_model_versions("project", "pump-a")


def test_reservation_lock_is_persistent_and_reusable(tmp_path):
    import pc_system.model_import as model_import_module

    model_root = tmp_path / "models" / "pump-a"
    first = model_import_module._reserve_version(
        model_root, "v1", "op-import-001"
    )
    with pytest.raises(ModelMatchingError) as busy:
        model_import_module._reserve_version(model_root, "v1", "op-import-002")
    assert busy.value.code == "operation_busy"

    model_import_module._release_reservation(first)

    assert first.path.is_file()
    second = model_import_module._reserve_version(
        model_root, "v1", "op-import-002"
    )
    model_import_module._release_reservation(second)
    assert first.path.is_file()


def test_reservation_identity_loss_fails_closed_without_path_mutation(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    model_root = tmp_path / "models" / "pump-a"
    reservation = model_import_module._reserve_version(
        model_root, "v1", "op-import-001"
    )
    monkeypatch.setattr(
        model_import_module, "_reservation_path_matches_handle", lambda _item: False
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        model_import_module._release_reservation(reservation)

    assert exc_info.value.code == "model_version_reservation_integrity_error"
    assert reservation.path.is_file()


def test_owned_staging_is_deferred_without_move_or_recursive_delete(tmp_path):
    create_asset(tmp_path)

    def failing_reader(_path):
        raise ModelMatchingError("invalid_model_geometry", "broken mesh")

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path, **import_arguments(mesh_reader=failing_reader)
        )

    assert exc_info.value.code == "invalid_model_geometry"
    staging = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / ".p15-model-op-import-001"
    )
    assert staging.is_dir()
    assert (staging / ".operation-owner.json").is_file()
    assert not list(staging.parent.glob(".p15-cleanup-*"))


def test_unconfirmed_cleanup_is_preserved_and_audited(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)

    def failing_reader(_path):
        raise ModelMatchingError("invalid_model_geometry", "broken mesh")

    monkeypatch.setattr(
        model_import_module, "_staging_matches_owner", lambda _owned: False
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path, **import_arguments(mesh_reader=failing_reader)
        )

    assert exc_info.value.code == "model_version_cleanup_required"
    staging = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / ".p15-model-op-import-001"
    )
    assert staging.is_dir()
    operation = load_operation(tmp_path, "op-import-001")
    assert operation["status"] == "failed"
    assert operation["error"]["code"] == "model_version_cleanup_required"
    event_types = [
        event["event_type"]
        for event in read_operation_events(tmp_path, "op-import-001")
    ]
    assert event_types.count("model_version.cleanup_deferred") == 1
    assert event_types.count("operation.failed") == 1


def test_streaming_source_limit_bounds_retained_bytes(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    source = tmp_path / "large.obj"
    source.write_bytes(b"v 0 0 0\n" * 4)
    monkeypatch.setattr(model_import_module, "MAX_MODEL_SOURCE_BYTES", 12, raising=False)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments(source_path=source))

    assert exc_info.value.code == "model_source_too_large"
    staged_source = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / ".p15-model-op-import-001"
        / "source"
        / "model.obj"
    )
    assert staged_source.stat().st_size <= 12
    operation = load_operation(tmp_path, "op-import-001")
    assert operation["status"] == "failed"
    assert operation["error"]["code"] == "model_source_too_large"


def test_retained_staging_count_quota_blocks_new_directory(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    retained = (
        tmp_path / "models" / "pump-a" / "versions" / ".p15-model-old"
    )
    retained.mkdir(parents=True)
    (retained / "old.bin").write_bytes(b"old")
    monkeypatch.setattr(model_import_module, "MAX_RETAINED_STAGING_DIRS", 1, raising=False)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments())

    assert exc_info.value.code == "model_staging_quota_exceeded"
    assert not (
        retained.parent / ".p15-model-op-import-001"
    ).exists()
    assert load_operation(tmp_path, "op-import-001")["error"]["code"] == (
        "model_staging_quota_exceeded"
    )


def test_retained_staging_byte_quota_bounds_partial_capture(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    retained = (
        tmp_path / "models" / "pump-a" / "versions" / ".p15-model-old"
    )
    retained.mkdir(parents=True)
    (retained / "old.bin").write_bytes(b"12345678")
    source = tmp_path / "input.obj"
    source.write_bytes(b"v 0 0 0\n" * 10)
    monkeypatch.setattr(model_import_module, "MAX_MODEL_SOURCE_BYTES", 1000, raising=False)
    monkeypatch.setattr(model_import_module, "MAX_RETAINED_STAGING_BYTES", 140, raising=False)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments(source_path=source))

    assert exc_info.value.code == "model_staging_quota_exceeded"
    new_source = (
        retained.parent
        / ".p15-model-op-import-001"
        / "source"
        / "model.obj"
    )
    retained_bytes = sum(
        path.stat().st_size
        for staging in retained.parent.glob(".p15-model-*")
        for path in staging.rglob("*")
        if path.is_file()
    )
    assert retained_bytes <= 140
    assert new_source.stat().st_size < source.stat().st_size


def test_staging_quota_scan_rejects_symlink_without_following(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    retained = (
        tmp_path / "models" / "pump-a" / "versions" / ".p15-model-old"
    )
    retained.mkdir(parents=True)
    try:
        (retained / "linked.obj").symlink_to(FIXTURE)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    monkeypatch.setattr(model_import_module, "MAX_RETAINED_STAGING_DIRS", 4, raising=False)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments())

    assert exc_info.value.code == "model_staging_quota_exceeded"
    assert load_operation(tmp_path, "op-import-001")["status"] == "failed"


def test_quota_lock_serializes_different_versions_at_count_limit(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    entered_capture = threading.Event()
    second_capture = threading.Event()
    release_capture = threading.Event()
    original_capture = model_import_module._capture_source

    def blocked_capture(source, destination, *args, **kwargs):
        fingerprint = original_capture(source, destination, *args, **kwargs)
        if destination.parts[-3] == ".p15-model-op-import-001":
            entered_capture.set()
            assert release_capture.wait(timeout=5)
        elif destination.parts[-3] == ".p15-model-op-import-002":
            second_capture.set()
        return fingerprint

    monkeypatch.setattr(model_import_module, "_capture_source", blocked_capture)
    monkeypatch.setattr(model_import_module, "MAX_RETAINED_STAGING_DIRS", 1, raising=False)

    first_arguments = import_arguments()
    second_arguments = import_arguments(
        version_id="v2",
        operation_id="op-import-002",
        request_id="request-import-002",
        idempotency_key="idem-import-002",
    )

    def run(arguments):
        try:
            return "ok", import_model_version(tmp_path, **arguments)
        except ModelMatchingError as exc:
            return "error", exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run, first_arguments)
        assert entered_capture.wait(timeout=5)
        second = executor.submit(run, second_arguments)
        assert not second_capture.wait(timeout=0.2)
        versions_root = tmp_path / "models" / "pump-a" / "versions"
        assert len(list(versions_root.glob(".p15-model-*"))) == 1
        release_capture.set()
        outcomes = [first.result(timeout=5), second.result(timeout=5)]

    assert sum(status == "ok" for status, _ in outcomes) in {1, 2}
    errors = [value for status, value in outcomes if status == "error"]
    assert all(error.code == "model_staging_quota_exceeded" for error in errors)
    finals = tmp_path / "models" / "pump-a" / "versions"
    assert sum(path.name in {"v1", "v2"} for path in finals.iterdir()) in {1, 2}


def test_reservation_metadata_write_retries_partial_writes(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    original_write = model_import_module.os.write

    def partial_write(descriptor, payload):
        size = 1 if len(payload) == 1 else max(1, len(payload) // 2)
        return original_write(descriptor, payload[:size])

    monkeypatch.setattr(model_import_module.os, "write", partial_write)
    reservation = model_import_module._reserve_version(
        tmp_path / "models" / "pump-a", "v1", "op-import-001"
    )
    model_import_module._release_reservation(reservation)

    metadata = json.loads(reservation.path.read_text(encoding="ascii"))
    assert metadata["operation_id"] == "op-import-001"
    assert len(metadata["owner_token"]) == 32


def test_capture_rejects_mocked_source_reparse_without_reading_target(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    source = tmp_path / "source.obj"
    source.write_bytes(b"must-not-be-captured")
    original_lstat = model_import_module.Path.lstat

    class ReparseInfo:
        def __init__(self, info):
            self._info = info
            self.st_file_attributes = model_import_module._REPARSE_POINT
            self.st_mode = (
                info.st_mode
                if model_import_module._REPARSE_POINT
                else model_import_module.stat.S_IFLNK
            )

        def __getattr__(self, name):
            return getattr(self._info, name)

    def mark_source_reparse(path):
        info = original_lstat(path)
        if path == source:
            return ReparseInfo(info)
        return info

    monkeypatch.setattr(model_import_module.Path, "lstat", mark_source_reparse)

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments(source_path=source))

    assert exc_info.value.code == "model_source_read_error"
    staged_source = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / ".p15-model-op-import-001"
        / "source"
        / "model.obj"
    )
    assert not staged_source.exists()
    operation = load_operation(tmp_path, "op-import-001")
    assert operation["status"] == "failed"
    assert operation["error"]["code"] == "model_source_read_error"


def test_capture_fails_closed_when_source_path_changes_after_fd_read(
    tmp_path, monkeypatch
):
    import pc_system.model_import as model_import_module

    create_asset(tmp_path)
    source = tmp_path / "source.obj"
    original_bytes = FIXTURE.read_bytes()
    replacement_bytes = b"v 9 9 9\nv 8 8 8\nv 7 7 7\nf 1 2 3\n"
    source.write_bytes(original_bytes)
    displaced = tmp_path / "source-original.obj"
    original_require = model_import_module._require_same_regular_path
    replaced = False

    def replace_before_identity_check(path, identity):
        nonlocal replaced
        if path == source and not replaced:
            replaced = True
            source.rename(displaced)
            source.write_bytes(replacement_bytes)
        return original_require(path, identity)

    monkeypatch.setattr(
        model_import_module,
        "_require_same_regular_path",
        replace_before_identity_check,
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **import_arguments(source_path=source))

    assert exc_info.value.code == "model_source_read_error"
    staged_source = (
        tmp_path
        / "models"
        / "pump-a"
        / "versions"
        / ".p15-model-op-import-001"
        / "source"
        / "model.obj"
    )
    assert staged_source.read_bytes() == original_bytes
    assert replacement_bytes not in staged_source.read_bytes()
    assert source.read_bytes() == replacement_bytes
    assert load_operation(tmp_path, "op-import-001")["status"] == "failed"


def test_capture_source_does_not_catch_base_exception(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module

    def interrupt(_path):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        model_import_module, "_regular_file_descriptor", interrupt
    )

    with pytest.raises(KeyboardInterrupt):
        model_import_module._capture_source(
            FIXTURE, tmp_path / "staging" / "model.obj"
        )
