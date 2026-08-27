import json
from pathlib import Path

import pytest

import pc_system.model_import as model_import_module
from pc_system.model_import import import_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import (
    load_operation,
    read_operation_events,
    verify_operation_chain,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def _mesh_reader(_path: Path) -> dict:
    return {
        "vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
        "faces": [[0, 1, 2]],
    }


def _create_asset(project_root: Path) -> None:
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
        operation_id="op-asset-e2e",
        request_id="request-asset-e2e",
        idempotency_key="idem-asset-e2e",
    )


def test_model_library_import_is_fully_auditable(tmp_path):
    _create_asset(tmp_path)

    imported = import_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={"supplier": "Acme"},
        principal=EXPERT,
        operation_id="op-import-e2e",
        request_id="request-import-e2e",
        idempotency_key="idem-import-e2e",
        mesh_reader=_mesh_reader,
    )

    events = read_operation_events(tmp_path, "op-import-e2e")
    assert verify_operation_chain(events) is True
    assert [event["event_type"] for event in events] == [
        "operation.started",
        "model_source.fingerprinted",
        "model_version.prepared",
        "model_version.published",
        "operation.completed",
    ]
    version_root = tmp_path / "models" / "pump-a" / "versions" / "v1"
    manifest = json.loads(
        (version_root / "model_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == imported
    assert manifest["operation_id"] == "op-import-e2e"
    assert len(manifest["source_fingerprint"]) == 64
    assert manifest["artifacts"] == {
        "source": "source/model.obj",
        "source_geometry": "source_geometry.json",
    }
    assert (version_root / "source" / "model.obj").read_bytes() == (
        FIXTURE.read_bytes()
    )


def test_prepublication_write_failure_preserves_asset_and_audit_chain(
    tmp_path, monkeypatch
):
    sibling_names_before = {path.name for path in tmp_path.parent.iterdir()}
    _create_asset(tmp_path)
    asset_path = tmp_path / "models" / "pump-a" / "model_asset.json"
    asset_before = asset_path.read_bytes()
    original_write_json = model_import_module.write_json

    def fail_manifest_write(payload, path):
        if path.name == "model_manifest.json":
            raise OSError("simulated prepublication manifest failure")
        return original_write_json(payload, path)

    monkeypatch.setattr(
        model_import_module, "write_json", fail_manifest_write
    )

    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path,
            model_id="pump-a",
            version_id="v1",
            source_path=FIXTURE,
            declared_unit="mm",
            license_name="internal",
            provenance={"supplier": "Acme"},
            principal=EXPERT,
            operation_id="op-import-failed-e2e",
            request_id="request-import-failed-e2e",
            idempotency_key="idem-import-failed-e2e",
            mesh_reader=_mesh_reader,
        )

    assert exc_info.value.code == "model_version_import_failed"
    assert asset_path.read_bytes() == asset_before
    assert not (
        tmp_path / "models" / "pump-a" / "versions" / "v1"
    ).exists()
    operation = load_operation(tmp_path, "op-import-failed-e2e")
    assert operation["status"] == "failed"
    assert operation["error"] == {
        "code": "model_version_import_failed",
        "message": "Model version import failed.",
    }
    events = read_operation_events(tmp_path, "op-import-failed-e2e")
    assert verify_operation_chain(events) is True
    assert [event["event_type"] for event in events] == [
        "operation.started",
        "model_source.fingerprinted",
        "model_version.cleanup_deferred",
        "operation.failed",
    ]
    assert {path.name for path in tmp_path.iterdir()} == {"models", "reports"}
    assert {path.name for path in tmp_path.parent.iterdir()} == sibling_names_before
