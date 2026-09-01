import json

from fastapi.testclient import TestClient

import pc_system.api as api_module
import pc_system.commands.phase15 as phase15_commands
from pc_system.api import create_app
from pc_system.cli import main
from pc_system.model_matching_errors import ModelMatchingError
from phase15c_support import REGISTRATION_V1


def _client(tmp_path, monkeypatch, **services):
    for name, service in services.items():
        monkeypatch.setattr(api_module, name, service)
    return TestClient(
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings={
                "expert-token": {"actor_id": "alice", "roles": ["expert"]},
                "auditor-token": {"actor_id": "auditor", "roles": ["auditor"]},
            },
            registration_engine_resolver=lambda name: name,
        )
    )


def test_api_exposes_registration_config_and_run_routes(tmp_path, monkeypatch):
    captured = {}

    def publish(_root, **kwargs):
        captured["config"] = kwargs
        return {"config_id": kwargs["config_id"], "status": "ready"}

    def register(_root, **kwargs):
        captured["registration"] = kwargs
        return {"registration_id": kwargs["registration_id"], "gate_status": "passed"}

    client = _client(
        tmp_path,
        monkeypatch,
        publish_registration_config=publish,
        list_registration_configs=lambda _root: [{"config_id": "registration-v1"}],
        register_model_candidate=register,
        load_model_registration=lambda _root, **kwargs: {
            "registration_id": kwargs["registration_id"],
            "gate_status": "passed",
        },
    )
    expert = {"Authorization": "Bearer expert-token"}
    auditor = {"Authorization": "Bearer auditor-token"}

    config_response = client.post(
        "/model-matching/registration-configs",
        json={
            "config_id": "registration-v1",
            "config": REGISTRATION_V1,
            "operation_id": "op-config",
            "request_id": "req-config",
            "idempotency_key": "idem-config",
        },
        headers=expert,
    )
    run_response = client.post(
        "/model-matching/registrations",
        json={
            "registration_id": "registration-api-1",
            "asset_id": "scan-a",
            "source_id": "release-a",
            "instance_id": "object-a",
            "retrieval_run_id": "retrieval-a",
            "candidate_rank": 1,
            "config_id": "registration-v1",
            "operation_id": "op-run",
            "request_id": "req-run",
            "idempotency_key": "idem-run",
        },
        headers=expert,
    )

    assert config_response.status_code == 201
    assert client.get("/model-matching/registration-configs", headers=auditor).status_code == 200
    assert run_response.status_code == 200
    assert captured["registration"]["engine_resolver"]("open3d") == "open3d"
    assert client.get(
        "/model-matching/registrations/scan-a/release-a/object-a/registration-api-1",
        headers=auditor,
    ).status_code == 200


def test_registration_api_error_mapping(tmp_path, monkeypatch):
    def fail(code):
        def service(*args, **kwargs):
            raise ModelMatchingError(code, code)

        return service

    expected = {
        "registration_input_incomplete": 400,
        "object_fingerprint_stale": 409,
        "artifact_integrity_failed": 409,
        "registration_engine_unavailable": 503,
        "registration_engine_failed": 503,
        "model_registration_not_found": 404,
    }
    for code, status in expected.items():
        client = _client(
            tmp_path,
            monkeypatch,
            load_model_registration=fail(code),
        )
        response = client.get(
            "/model-matching/registrations/a/b/c/d",
            headers={"Authorization": "Bearer auditor-token"},
        )
        assert response.status_code == status


def test_registration_cli_contracts(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "registration.json"
    config_path.write_text(json.dumps(REGISTRATION_V1), encoding="utf-8")
    monkeypatch.setattr(
        phase15_commands,
        "publish_registration_config",
        lambda _root, **kwargs: {"config_id": kwargs["config_id"]},
    )
    monkeypatch.setattr(
        phase15_commands,
        "register_model_candidate",
        lambda _root, **kwargs: {
            "registration_id": kwargs["registration_id"],
            "gate_status": "review_required",
        },
    )

    assert main(
        [
            "publish-model-registration-config",
            "--project-root", str(tmp_path),
            "--config-id", "registration-v1",
            "--config", str(config_path),
            "--actor", "alice",
            "--operation-id", "op-config",
            "--request-id", "req-config",
            "--idempotency-key", "idem-config",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["config_id"] == "registration-v1"
    assert main(
        [
            "register-model-candidate",
            "--project-root", str(tmp_path),
            "--registration-id", "registration-1",
            "--asset-id", "scan-a",
            "--source-id", "release-a",
            "--instance-id", "object-a",
            "--retrieval-run-id", "retrieval-a",
            "--candidate-rank", "1",
            "--config-id", "registration-v1",
            "--actor", "alice",
            "--operation-id", "op-run",
            "--request-id", "req-run",
            "--idempotency-key", "idem-run",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["gate_status"] == "review_required"
