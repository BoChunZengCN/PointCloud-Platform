import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import pc_system.api as api_module
from pc_system.api import create_app
from pc_system.cli import main
from pc_system.model_import import import_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import load_operation
from pc_system.model_matching_identity import Principal
from pc_system.model_release import release_model_version


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
EXPERT_HEADERS = {"X-Actor-ID": "alice", "X-Actor-Roles": "expert"}
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
        operation_id="op-asset-release-api",
        request_id="req-asset-release-api",
        idempotency_key="idem-asset-release-api",
    )
    for version_id, supersedes in (("v1", None), ("v2", "v1")):
        import_model_version(
            project_root,
            model_id="pump-a",
            version_id=version_id,
            source_path=FIXTURE,
            declared_unit="mm",
            license_name="internal",
            provenance={"supplier": "Acme"},
            supersedes_version_id=supersedes,
            principal=EXPERT,
            operation_id=f"op-import-release-{version_id}",
            request_id=f"req-import-release-{version_id}",
            idempotency_key=f"idem-import-release-{version_id}",
            mesh_reader=_fake_reader,
        )


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


def _release_payload(**overrides):
    payload = {
        "version_id": "v1",
        "release_id": "release-001",
        "action": "activate",
        "expected_current_release_id": None,
        "rollback_of_release_id": None,
        "reason": "Production release",
        "operation_id": "op-release-001",
        "request_id": "req-release-001",
        "idempotency_key": "idem-release-001",
    }
    payload.update(overrides)
    return payload


def test_release_cli_creates_audited_rollback_and_lists_history(
    tmp_path, capsys
):
    _create_versions(tmp_path)
    _release(tmp_path, 1)
    _release(
        tmp_path,
        2,
        version_id="v2",
        expected_current_release_id="release-001",
    )

    exit_code = main([
        "release-model-version", "--project-root", str(tmp_path),
        "--model-id", "pump-a", "--version-id", "v1",
        "--release-id", "release-003", "--action", "rollback",
        "--expected-current-release-id", "release-002",
        "--rollback-of-release-id", "release-001",
        "--reason", "Regression in v2", "--actor", "alice",
        "--operation-id", "op-release-003", "--request-id", "req-release-003",
        "--idempotency-key", "idem-release-003",
    ])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["release_id"] == "release-003"
    assert load_operation(tmp_path, "op-release-003")["status"] == "completed"
    assert main([
        "list-model-releases", "--project-root", str(tmp_path),
        "--model-id", "pump-a",
    ]) == 0
    history = json.loads(capsys.readouterr().out)
    assert [item["release_id"] for item in history] == [
        "release-001", "release-002", "release-003"
    ]


def test_production_release_api_uses_configured_principal_and_public_history(
    tmp_path,
):
    _create_versions(tmp_path)
    client = TestClient(create_app(
        tmp_path,
        api_key="legacy-key",
        run_mode="production",
        principal_bindings={
            "expert-token": {"actor_id": "trusted-expert", "roles": ["expert"]}
        },
    ))

    response = client.post(
        "/model-library/models/pump-a/releases",
        headers={"X-API-Key": "expert-token", "X-Actor-ID": "spoofed"},
        json=_release_payload(),
    )

    assert response.status_code == 201
    assert response.json()["actor_id"] == "trusted-expert"
    model = client.get("/model-library/models/pump-a").json()
    assert model["current_release"]["release_id"] == "release-001"
    assert model["release_history"] == [response.json()]


def test_release_api_authenticates_before_reading_body(tmp_path, monkeypatch):
    async def forbidden_stream(_request):
        raise AssertionError("Unauthorized release route read request stream.")
        yield b""

    monkeypatch.setattr(api_module.Request, "stream", forbidden_stream)
    client = TestClient(create_app(
        tmp_path,
        api_key="legacy-key",
        run_mode="production",
        principal_bindings={},
    ))

    response = client.post(
        "/model-library/models/pump-a/releases",
        headers={"X-API-Key": "unknown", "Content-Type": "application/json"},
        content=b"{not-json",
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"


def test_release_api_rejects_non_string_optional_identifier(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))

    response = client.post(
        "/model-library/models/pump-a/releases",
        headers=EXPERT_HEADERS,
        json=_release_payload(expected_current_release_id=1),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request_body"


def test_release_api_maps_stale_head_to_conflict(tmp_path):
    _create_versions(tmp_path)
    client = TestClient(create_app(tmp_path, run_mode="development"))
    assert client.post(
        "/model-library/models/pump-a/releases",
        headers=EXPERT_HEADERS,
        json=_release_payload(),
    ).status_code == 201

    response = client.post(
        "/model-library/models/pump-a/releases",
        headers=EXPERT_HEADERS,
        json=_release_payload(
            version_id="v2",
            release_id="release-002",
            operation_id="op-release-002",
            request_id="req-release-002",
            idempotency_key="idem-release-002",
        ),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_model_release"
