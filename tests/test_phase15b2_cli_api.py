import json

from fastapi.testclient import TestClient

import pc_system.api as api_module
import pc_system.commands.phase15 as phase15_commands
from pc_system.api import create_app
from pc_system.cli import main
from phase15b2_support import FEATURE_V1, MAPPING_V1, SCORING_V1


def _audit_args(prefix: str) -> list[str]:
    return [
        "--actor", "alice",
        "--operation-id", f"op-{prefix}",
        "--request-id", f"req-{prefix}",
        "--idempotency-key", f"idem-{prefix}",
    ]


def _production_client(tmp_path, monkeypatch, **services):
    for name, service in services.items():
        monkeypatch.setattr(api_module, name, service)
    return TestClient(
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings={
                "expert-token": {
                    "actor_id": "alice",
                    "roles": ["expert"],
                },
                "auditor-token": {
                    "actor_id": "auditor",
                    "roles": ["auditor"],
                },
            },
        )
    )


def test_config_cli_loads_bounded_json_and_prints_verified_result(
    tmp_path, monkeypatch, capsys
):
    paths = []
    for name, value in (
        ("feature", FEATURE_V1),
        ("scoring", SCORING_V1),
        ("mapping", MAPPING_V1),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)

    captured = {}

    def publish(_root, **kwargs):
        captured.update(kwargs)
        return {"config_id": kwargs["config_id"], "status": "ready"}

    monkeypatch.setattr(phase15_commands, "publish_retrieval_config", publish)

    result = main([
        "create-model-retrieval-config",
        "--project-root", str(tmp_path),
        "--config-id", "retrieval-v1",
        "--feature", str(paths[0]),
        "--scoring", str(paths[1]),
        "--category-mapping", str(paths[2]),
        *_audit_args("config"),
    ])

    assert result == 0
    assert captured["feature"] == FEATURE_V1
    assert captured["principal"].actor_id == "alice"
    assert json.loads(capsys.readouterr().out) == {
        "config_id": "retrieval-v1",
        "status": "ready",
    }


def test_retrieve_cli_rejects_invalid_top_k_before_domain_call(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        phase15_commands,
        "retrieve_model_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("domain service must not be called")
        ),
    )

    result = main([
        "retrieve-model-candidates",
        "--project-root", str(tmp_path),
        "--retrieval-run-id", "retrieval-001",
        "--source-kind", "correction_release",
        "--asset-id", "scan-a",
        "--source-id", "release-001",
        "--instance-id", "pump-001",
        "--top-k", "51",
        *_audit_args("retrieval"),
    ])

    assert result == 2
    assert capsys.readouterr().err.startswith("invalid_retrieval_input:")


def test_retrieve_cli_rejects_mixed_production_and_challenger_selection(
    tmp_path, capsys
):
    result = main([
        "retrieve-model-candidates",
        "--project-root", str(tmp_path),
        "--retrieval-run-id", "retrieval-001",
        "--source-kind", "correction_release",
        "--asset-id", "scan-a",
        "--source-id", "release-001",
        "--instance-id", "pump-001",
        "--index-release-id", "index-release-001",
        "--index-id", "challenger-001",
        "--top-k", "5",
        *_audit_args("retrieval-mixed"),
    ])

    assert result == 2
    assert capsys.readouterr().err.startswith("invalid_retrieval_input:")


def test_cli_lists_indexes_releases_and_reads_retrieval(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        phase15_commands,
        "list_model_feature_indexes",
        lambda _root: [{"index_id": "index-001"}],
    )
    monkeypatch.setattr(
        phase15_commands,
        "list_model_feature_index_releases",
        lambda _root: [{"release_id": "index-release-001"}],
    )
    monkeypatch.setattr(
        phase15_commands,
        "load_model_retrieval",
        lambda _root, **kwargs: {"retrieval_run_id": kwargs["retrieval_run_id"]},
    )

    assert main([
        "list-model-feature-indexes", "--project-root", str(tmp_path)
    ]) == 0
    assert json.loads(capsys.readouterr().out)[0]["index_id"] == "index-001"
    assert main([
        "list-model-feature-index-releases", "--project-root", str(tmp_path)
    ]) == 0
    assert json.loads(capsys.readouterr().out)[0]["release_id"] == "index-release-001"
    assert main([
        "show-model-retrieval", "--project-root", str(tmp_path),
        "--asset-id", "scan-a", "--source-id", "release-001",
        "--instance-id", "pump-001", "--retrieval-run-id", "retrieval-001",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["retrieval_run_id"] == "retrieval-001"


def test_cli_builds_releases_and_runs_retrieval(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        phase15_commands,
        "build_model_feature_index",
        lambda _root, **kwargs: {"index_id": kwargs["index_id"]},
    )
    monkeypatch.setattr(
        phase15_commands,
        "release_model_feature_index",
        lambda _root, **kwargs: {"release_id": kwargs["release_id"]},
    )
    monkeypatch.setattr(
        phase15_commands,
        "retrieve_model_candidates",
        lambda _root, **kwargs: {
            "retrieval_run_id": kwargs["retrieval_run_id"],
            "index_id": kwargs["index_id"],
        },
    )

    assert main([
        "build-model-feature-index", "--project-root", str(tmp_path),
        "--index-id", "challenger-001", "--index-mode", "challenger",
        "--config-id", "retrieval-v1", *_audit_args("index"),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["index_id"] == "challenger-001"
    assert main([
        "release-model-feature-index", "--project-root", str(tmp_path),
        "--index-id", "challenger-001", "--release-id", "index-release-001",
        "--action", "activate", "--reason", "production",
        *_audit_args("index-release"),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["release_id"] == "index-release-001"
    assert main([
        "retrieve-model-candidates", "--project-root", str(tmp_path),
        "--retrieval-run-id", "retrieval-001",
        "--source-kind", "correction_release", "--asset-id", "scan-a",
        "--source-id", "release-001", "--instance-id", "pump-001",
        "--index-id", "challenger-001", "--top-k", "5",
        "--keyword", "pump", "--hint-source", "human",
        *_audit_args("retrieval-success"),
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "index_id": "challenger-001",
        "retrieval_run_id": "retrieval-001",
    }


def test_retrieval_api_uses_bearer_bound_principal_and_ignores_body_actor(
    tmp_path, monkeypatch
):
    captured = {}

    def retrieve(_root, **kwargs):
        captured.update(kwargs)
        return {
            "retrieval_run_id": kwargs["retrieval_run_id"],
            "actor_id": kwargs["principal"].actor_id,
        }

    client = _production_client(
        tmp_path, monkeypatch, retrieve_model_candidates=retrieve
    )
    response = client.post(
        "/model-matching/retrievals",
        headers={"Authorization": "Bearer expert-token"},
        json={
            "retrieval_run_id": "retrieval-001",
            "source_kind": "correction_release",
            "asset_id": "scan-a",
            "source_id": "release-001",
            "instance_id": "pump-001",
            "index_release_id": None,
            "index_id": None,
            "top_k": 5,
            "keywords": ["pump"],
            "tags": [],
            "manufacturer": None,
            "model_number": None,
            "hint_source": "human",
            "actor": "forged",
            "operation_id": "op-retrieval-001",
            "request_id": "req-retrieval-001",
            "idempotency_key": "idem-retrieval-001",
        },
    )

    assert response.status_code == 201
    assert response.json()["actor_id"] == "alice"
    assert captured["principal"].source == "configured_token"


def test_retrieval_api_authenticates_before_reading_body(
    tmp_path, monkeypatch
):
    async def forbidden_stream(_request):
        raise AssertionError("unauthorized route read request stream")
        yield b""

    monkeypatch.setattr(api_module.Request, "stream", forbidden_stream)
    client = _production_client(tmp_path, monkeypatch)

    response = client.post(
        "/model-matching/retrievals",
        headers={"Authorization": "Bearer unknown"},
        content=b"{not-json",
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"


def test_phase15b2_api_exposes_config_index_release_and_audited_reads(
    tmp_path, monkeypatch
):
    client = _production_client(
        tmp_path,
        monkeypatch,
        publish_retrieval_config=lambda _root, **kwargs: {
            "config_id": kwargs["config_id"]
        },
        list_retrieval_configs=lambda _root: [{"config_id": "retrieval-v1"}],
        build_model_feature_index=lambda _root, **kwargs: {
            "index_id": kwargs["index_id"]
        },
        list_model_feature_indexes=lambda _root: [{"index_id": "index-001"}],
        release_model_feature_index=lambda _root, **kwargs: {
            "release_id": kwargs["release_id"]
        },
        list_model_feature_index_releases=lambda _root: [
            {"release_id": "index-release-001"}
        ],
        load_model_retrieval=lambda _root, **kwargs: {
            "retrieval_run_id": kwargs["retrieval_run_id"]
        },
    )
    expert = {"X-API-Key": "expert-token"}
    auditor = {"X-API-Key": "auditor-token"}

    config_response = client.post(
        "/model-matching/retrieval-configs",
        headers=expert,
        json={
            "config_id": "retrieval-v1",
            "feature": FEATURE_V1,
            "scoring": SCORING_V1,
            "category_mapping": MAPPING_V1,
            "operation_id": "op-config-001",
            "request_id": "req-config-001",
            "idempotency_key": "idem-config-001",
        },
    )
    index_response = client.post(
        "/model-matching/feature-indexes",
        headers=expert,
        json={
            "index_id": "index-001",
            "index_mode": "production",
            "config_id": "retrieval-v1",
            "historical_releases": None,
            "operation_id": "op-index-001",
            "request_id": "req-index-001",
            "idempotency_key": "idem-index-001",
        },
    )
    release_response = client.post(
        "/model-matching/feature-index-releases",
        headers=expert,
        json={
            "index_id": "index-001",
            "release_id": "index-release-001",
            "action": "activate",
            "expected_current_release_id": None,
            "rollback_of_release_id": None,
            "reason": "production",
            "operation_id": "op-index-release-001",
            "request_id": "req-index-release-001",
            "idempotency_key": "idem-index-release-001",
        },
    )

    assert config_response.status_code == 201
    assert index_response.status_code == 201
    assert release_response.status_code == 201
    assert client.get(
        "/model-matching/retrieval-configs", headers=auditor
    ).status_code == 200
    assert client.get(
        "/model-matching/feature-indexes", headers=auditor
    ).status_code == 200
    assert client.get(
        "/model-matching/feature-index-releases", headers=auditor
    ).status_code == 200
    retrieval = client.get(
        "/model-matching/retrievals/scan-a/release-001/pump-001/retrieval-001",
        headers=auditor,
    )
    assert retrieval.status_code == 200
    assert retrieval.json()["retrieval_run_id"] == "retrieval-001"


def test_phase15b2_api_rejects_unknown_fields_and_maps_domain_errors(
    tmp_path, monkeypatch
):
    from pc_system.model_matching_errors import ModelMatchingError

    def reject(_root, **_kwargs):
        raise ModelMatchingError(
            "model_index_release_not_found", "Production release is absent."
        )

    client = _production_client(
        tmp_path, monkeypatch, retrieve_model_candidates=reject
    )
    payload = {
        "retrieval_run_id": "retrieval-001",
        "source_kind": "correction_release",
        "asset_id": "scan-a",
        "source_id": "release-001",
        "instance_id": "pump-001",
        "index_release_id": None,
        "index_id": None,
        "top_k": 5,
        "keywords": [],
        "tags": [],
        "manufacturer": None,
        "model_number": None,
        "hint_source": None,
        "operation_id": "op-retrieval-001",
        "request_id": "req-retrieval-001",
        "idempotency_key": "idem-retrieval-001",
    }

    unknown = client.post(
        "/model-matching/retrievals",
        headers={"X-API-Key": "expert-token"},
        json={**payload, "unexpected": True},
    )
    mapped = client.post(
        "/model-matching/retrievals",
        headers={"X-API-Key": "expert-token"},
        json=payload,
    )

    assert unknown.status_code == 400
    assert unknown.json()["detail"]["code"] == "invalid_request_body"
    assert mapped.status_code == 404
    assert mapped.json()["detail"]["code"] == "model_index_release_not_found"
