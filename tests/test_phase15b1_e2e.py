from pathlib import Path

import pc_system.commands.phase15 as phase15_commands
from pc_system.cli import main
from pc_system.model_import import import_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import read_operation_events, verify_operation_chain
from pc_system.model_matching_identity import Principal
from pc_system.model_release import load_current_model_release, release_model_version
from pc_system.model_sampling import list_sampled_representations


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def _reader(_path):
    return {
        "vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
        "faces": [[0, 1, 2]],
    }


def _version_bytes(root, version_id):
    version = root / "models" / "pump-a" / "versions" / version_id
    return {
        path.relative_to(version).as_posix(): path.read_bytes()
        for path in version.rglob("*")
        if path.is_file()
    }


def test_import_release_sample_and_rollback_is_fully_auditable(
    tmp_path, monkeypatch, capsys
):
    create_model_asset(
        tmp_path,
        model_id="pump-a",
        display_name="Pump A",
        category_id="pump",
        manufacturer="Acme",
        model_number="A-100",
        keywords=["centrifugal"],
        tags=["pump"],
        principal=EXPERT,
        operation_id="op-e2e-asset",
        request_id="req-e2e-asset",
        idempotency_key="idem-e2e-asset",
    )
    for version_id, supersedes in (("v1", None), ("v2", "v1")):
        import_model_version(
            tmp_path,
            model_id="pump-a",
            version_id=version_id,
            source_path=FIXTURE,
            declared_unit="mm",
            license_name="internal",
            provenance={"supplier": "Acme"},
            supersedes_version_id=supersedes,
            principal=EXPERT,
            operation_id=f"op-e2e-import-{version_id}",
            request_id=f"req-e2e-import-{version_id}",
            idempotency_key=f"idem-e2e-import-{version_id}",
            mesh_reader=_reader,
        )
    release_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        release_id="release-e2e-001",
        action="activate",
        expected_current_release_id=None,
        rollback_of_release_id=None,
        reason="Initial production release",
        principal=EXPERT,
        operation_id="op-e2e-release-001",
        request_id="req-e2e-release-001",
        idempotency_key="idem-e2e-release-001",
    )
    release_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v2",
        release_id="release-e2e-002",
        action="activate",
        expected_current_release_id="release-e2e-001",
        rollback_of_release_id=None,
        reason="Upgrade production model",
        principal=EXPERT,
        operation_id="op-e2e-release-002",
        request_id="req-e2e-release-002",
        idempotency_key="idem-e2e-release-002",
    )
    before = _version_bytes(tmp_path, "v2")
    monkeypatch.setattr(phase15_commands, "trimesh_mesh_reader", _reader)

    assert main([
        "sample-model-version",
        "--project-root", str(tmp_path),
        "--model-id", "pump-a",
        "--version-id", "v2",
        "--point-count", "16",
        "--random-seed", "11",
        "--actor", "alice",
        "--operation-id", "op-e2e-sample-v2",
        "--request-id", "req-e2e-sample-v2",
        "--idempotency-key", "idem-e2e-sample-v2",
    ]) == 0
    assert Path(capsys.readouterr().out.strip()).is_file()
    assert _version_bytes(tmp_path, "v2") == before

    release_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        release_id="release-e2e-003",
        action="rollback",
        expected_current_release_id="release-e2e-002",
        rollback_of_release_id="release-e2e-001",
        reason="Rollback after production validation",
        principal=EXPERT,
        operation_id="op-e2e-release-003",
        request_id="req-e2e-release-003",
        idempotency_key="idem-e2e-release-003",
    )

    assert load_current_model_release(tmp_path, "pump-a")["version_id"] == "v1"
    representations = list_sampled_representations(tmp_path, "pump-a", "v2")
    assert len(representations) == 1
    assert representations[0]["point_count"] == 16
    for operation_id in (
        "op-e2e-import-v1",
        "op-e2e-import-v2",
        "op-e2e-release-001",
        "op-e2e-release-002",
        "op-e2e-sample-v2",
        "op-e2e-release-003",
    ):
        assert verify_operation_chain(read_operation_events(tmp_path, operation_id))
