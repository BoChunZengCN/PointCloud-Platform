import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

import pc_system.api as api_module
import pc_system.model_matching_audit as audit_module
from pc_system.api import create_app
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


EXPERT_HEADERS = {
    "X-Actor-ID": "alice",
    "X-Actor-Roles": "expert",
}


def model_payload(**overrides):
    payload = {
        "model_id": "pump-a",
        "display_name": "Pump A",
        "category_id": "pump",
        "manufacturer": "Acme",
        "model_number": "A-100",
        "keywords": ["centrifugal"],
        "tags": ["pump"],
        "operation_id": "op-model-001",
        "request_id": "request-model-001",
        "idempotency_key": "idem-model-001",
    }
    payload.update(overrides)
    return payload


def import_payload(**overrides):
    payload = {
        "version_id": "v1",
        "staged_source": "imports/models/minimal.obj",
        "declared_unit": "m",
        "license": "internal",
        "provenance": {"supplier": "Acme"},
        "operation_id": "op-import-001",
        "request_id": "request-import-001",
        "idempotency_key": "idem-import-001",
    }
    payload.update(overrides)
    return payload


def test_development_api_create_and_import_flow(tmp_path):
    staged = tmp_path / "imports" / "models" / "minimal.obj"
    staged.parent.mkdir(parents=True)
    staged.write_text(
        "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(tmp_path, run_mode="development"))

    created = client.post(
        "/model-library/models",
        headers=EXPERT_HEADERS,
        json=model_payload(),
    )
    imported = client.post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json=import_payload(),
    )

    assert created.status_code == 201
    assert imported.status_code == 201
    assert client.get("/model-library").json()["model_count"] == 1
    assert client.get("/model-library/models/pump-a").json()["version_count"] == 1


def test_operator_cannot_create_model(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))

    response = client.post(
        "/model-library/models",
        headers={"X-Actor-ID": "bob", "X-Actor-Roles": "operator"},
        json={},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"


def test_production_ignores_spoofed_actor_headers(tmp_path):
    bindings = {"token-a": {"actor_id": "alice", "roles": ["expert"]}}
    client = TestClient(
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings=bindings,
        )
    )

    response = client.post(
        "/model-library/models",
        headers={
            "X-API-Key": "unknown",
            "X-Actor-ID": "alice",
            "X-Actor-Roles": "expert",
        },
        json={},
    )

    assert response.status_code == 403
    denied = list(
        (tmp_path / "reports" / "model_matching_operations").glob("denied-*")
    )
    assert len(denied) == 1
    assert "unknown" not in (denied[0] / "events.jsonl").read_text(
        encoding="utf-8"
    )
    persisted = "".join(
        path.read_text(encoding="utf-8")
        for path in denied[0].iterdir()
        if path.is_file()
    )
    assert "alice" not in persisted
    assert "expert" not in persisted


def test_import_path_cannot_escape_staging_root(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))

    response = client.post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json={"staged_source": "../secret.obj"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_staged_source"


def test_authentication_happens_before_request_body_or_stream(
    tmp_path, monkeypatch
):
    async def forbidden_body(_request):
        raise AssertionError("Unauthorized route read request.body().")

    async def forbidden_stream(_request):
        raise AssertionError("Unauthorized route read request.stream().")
        yield b""

    monkeypatch.setattr(api_module.Request, "body", forbidden_body)
    monkeypatch.setattr(api_module.Request, "stream", forbidden_stream)
    client = TestClient(
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings={},
        )
    )

    response = client.post(
        "/model-library/models",
        headers={
            "X-API-Key": "raw-secret-token",
            "X-Actor-ID": "spoofed-actor",
            "X-Actor-Roles": "expert",
            "Content-Type": "application/json",
        },
        content=b"{not-json",
    )

    assert response.status_code == 403
    denied = list(
        (tmp_path / "reports" / "model_matching_operations").glob("denied-*")
    )
    assert len(denied) == 1
    persisted = "".join(
        path.read_text(encoding="utf-8")
        for path in denied[0].iterdir()
        if path.is_file()
    )
    assert "raw-secret-token" not in persisted
    assert "spoofed-actor" not in persisted


def test_authorized_request_body_larger_than_limit_returns_413(tmp_path):
    limit = api_module.MAX_PHASE15_REQUEST_BODY_BYTES
    body = b'{' + b'"padding":"' + (b"x" * limit) + b'"}'

    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={**EXPERT_HEADERS, "Content-Type": "application/json"},
        content=body,
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_body_too_large"


def test_oversized_content_length_is_rejected_before_stream_read(
    tmp_path, monkeypatch
):
    async def forbidden_stream(_request):
        raise AssertionError("Oversized Content-Length read request stream.")
        yield b""

    monkeypatch.setattr(api_module.Request, "stream", forbidden_stream)
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={
            **EXPERT_HEADERS,
            "Content-Type": "application/json",
            "Content-Length": str(
                api_module.MAX_PHASE15_REQUEST_BODY_BYTES + 1
            ),
        },
        content=b"{}",
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_body_too_large"


def test_chunked_body_without_content_length_is_bounded_by_actual_chunks(
    tmp_path, monkeypatch
):
    limit = api_module.MAX_PHASE15_REQUEST_BODY_BYTES
    observed_content_lengths = []

    async def oversized_stream(request):
        observed_content_lengths.append(request.headers.get("content-length"))
        yield b"x" * limit
        yield b"y"

    monkeypatch.setattr(api_module.Request, "stream", oversized_stream)
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={**EXPERT_HEADERS, "Content-Type": "application/json"},
        content=iter([b"{}"]),
    )

    assert observed_content_lengths == [None]
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_body_too_large"


def test_forged_small_content_length_cannot_bypass_actual_stream_limit(
    tmp_path, monkeypatch
):
    limit = api_module.MAX_PHASE15_REQUEST_BODY_BYTES
    observed_content_lengths = []

    async def oversized_stream(request):
        observed_content_lengths.append(request.headers.get("content-length"))
        yield b"x" * (limit + 1)

    monkeypatch.setattr(api_module.Request, "stream", oversized_stream)
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={
            **EXPERT_HEADERS,
            "Content-Type": "application/json",
            "Content-Length": "1",
        },
        content=b"{}",
    )

    assert observed_content_lengths == ["1"]
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_body_too_large"


def test_client_disconnect_returns_stable_json_400(tmp_path, monkeypatch):
    async def disconnected_stream(_request):
        raise ClientDisconnect()
        yield b""

    monkeypatch.setattr(api_module.Request, "stream", disconnected_stream)
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={**EXPERT_HEADERS, "Content-Type": "application/json"},
        content=b"{}",
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"] == {
        "code": "request_body_interrupted",
        "message": "Request body transfer was interrupted.",
    }


@pytest.mark.parametrize(
    "stream_error",
    [
        OSError("transport-secret"),
        RuntimeError("transport-secret"),
    ],
)
def test_stream_transport_error_returns_redacted_json_503(
    tmp_path, monkeypatch, stream_error
):
    async def broken_stream(_request):
        raise stream_error
        yield b""

    monkeypatch.setattr(api_module.Request, "stream", broken_stream)
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={**EXPERT_HEADERS, "Content-Type": "application/json"},
        content=b"{}",
    )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"] == {
        "code": "request_stream_error",
        "message": "Request body stream could not be read.",
    }
    assert "transport-secret" not in response.text


def test_stream_base_exception_is_not_normalized(tmp_path, monkeypatch):
    class FatalStreamFailure(BaseException):
        pass

    async def fatal_stream(_request):
        raise FatalStreamFailure("fatal-stream-secret")
        yield b""

    monkeypatch.setattr(api_module.Request, "stream", fatal_stream)
    client = TestClient(create_app(tmp_path, run_mode="development"))

    with pytest.raises(FatalStreamFailure, match="fatal-stream-secret"):
        client.post(
            "/model-library/models",
            headers={**EXPERT_HEADERS, "Content-Type": "application/json"},
            content=b"{}",
        )


def test_request_body_at_exact_limit_is_not_rejected_as_too_large(tmp_path):
    limit = api_module.MAX_PHASE15_REQUEST_BODY_BYTES
    prefix = b'{"padding":"'
    suffix = b'"}'
    body = prefix + (b"x" * (limit - len(prefix) - len(suffix))) + suffix
    assert len(body) == limit

    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={**EXPERT_HEADERS, "Content-Type": "application/json"},
        content=body,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request_body"


def test_denied_audit_failure_returns_service_error_not_permission_denied(
    tmp_path, monkeypatch
):
    def fail_denied_audit(*args, **kwargs):
        raise ModelMatchingError(
            "audit_persistence_error", "Denied audit unavailable."
        )

    monkeypatch.setattr(api_module, "record_denied_operation", fail_denied_audit)
    client = TestClient(
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings={},
        ),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/model-library/models",
        headers={"Content-Type": "application/json"},
        content=b"{not-json",
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "audit_persistence_error"


def test_principal_bindings_are_strictly_frozen_at_app_creation(tmp_path):
    bindings = {"token-a": {"actor_id": "alice", "roles": ["expert"]}}
    app = create_app(
        tmp_path,
        api_key="legacy-key",
        run_mode="production",
        principal_bindings=bindings,
    )
    bindings["token-a"]["actor_id"] = "mallory"
    bindings["token-a"]["roles"].clear()

    response = TestClient(app).post(
        "/model-library/models",
        headers={"X-API-Key": "token-a"},
        json=model_payload(),
    )

    assert response.status_code == 201
    assert response.json()["created_by"] == "alice"


def test_principal_object_binding_is_rebuilt_before_later_mutation(tmp_path):
    configured = Principal(
        "alice", frozenset({"expert"}), "configured_token"
    )
    app = create_app(
        tmp_path,
        api_key="legacy-key",
        run_mode="production",
        principal_bindings={"token-a": configured},
    )
    object.__setattr__(configured, "actor_id", "mallory")
    object.__setattr__(configured, "roles", frozenset({"operator"}))
    object.__setattr__(configured, "source", "development_headers")

    response = TestClient(app).post(
        "/model-library/models",
        headers={"X-API-Key": "token-a"},
        json=model_payload(),
    )

    assert response.status_code == 201
    assert response.json()["created_by"] == "alice"


@pytest.mark.parametrize(
    "bindings",
    [
        {"token-a": {"actor_id": "alice", "roles": ["root"]}},
        {"token-a": {"actor_id": "alice", "roles": ["expert"], "source": "development_headers"}},
        {"token-a": {"actor_id": "alice", "roles": ("expert",)}},
    ],
)
def test_principal_bindings_reject_untrusted_shapes(tmp_path, bindings):
    with pytest.raises(ValueError):
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings=bindings,
        )


def test_production_legacy_api_key_is_not_a_phase15_principal(tmp_path):
    client = TestClient(
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings={},
        )
    )

    response = client.post(
        "/model-library/models",
        headers={"X-API-Key": "legacy-key"},
        json=model_payload(),
    )

    assert response.status_code == 403


@pytest.mark.parametrize("run_mode", ["staging", "", 1])
def test_create_app_rejects_unknown_run_mode(tmp_path, run_mode):
    with pytest.raises(ValueError):
        create_app(tmp_path, run_mode=run_mode)


@pytest.mark.parametrize(
    "staged_source",
    [
        "C:/outside.obj",
        "//server/share/outside.obj",
        "imports\\models\\minimal.obj",
        "imports/models//minimal.obj",
        "imports/models/minimal.obj:stream",
    ],
)
def test_import_rejects_noncanonical_staged_paths(tmp_path, staged_source):
    response = TestClient(
        create_app(tmp_path, run_mode="development")
    ).post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json={"staged_source": staged_source},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_staged_source"


def test_import_rejects_symlinked_staged_source(tmp_path):
    outside = tmp_path / "outside.obj"
    outside.write_text("v 0 0 0\n", encoding="utf-8")
    staged = tmp_path / "imports" / "models" / "linked.obj"
    staged.parent.mkdir(parents=True)
    try:
        os.symlink(outside, staged)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    response = TestClient(
        create_app(tmp_path, run_mode="development")
    ).post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json={"staged_source": "imports/models/linked.obj"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_staged_source"


def test_import_rejects_mocked_windows_reparse_source(tmp_path, monkeypatch):
    staged = tmp_path / "imports" / "models" / "reparse.obj"
    staged.parent.mkdir(parents=True)
    staged.write_text("v 0 0 0\n", encoding="utf-8")
    original_lstat = api_module.Path.lstat

    def reparse_lstat(path):
        info = original_lstat(path)
        if path == staged:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=api_module._REPARSE_POINT,
            )
        return info

    monkeypatch.setattr(api_module.Path, "lstat", reparse_lstat)
    response = TestClient(
        create_app(tmp_path, run_mode="development")
    ).post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json={"staged_source": "imports/models/reparse.obj"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_staged_source"


@pytest.mark.parametrize(
    ("content", "content_type"),
    [(b"{", "application/json"), (b"[]", "application/json")],
)
def test_authorized_malformed_or_non_object_json_returns_stable_400(
    tmp_path, content, content_type
):
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers={**EXPERT_HEADERS, "Content-Type": content_type},
        content=content,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request_body"


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_id": 1},
        {"keywords": [1]},
        {"operation_id": None},
    ],
)
def test_wrong_exact_json_field_types_return_stable_400(tmp_path, overrides):
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers=EXPERT_HEADERS,
        json=model_payload(**overrides),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request_body"


def test_import_does_not_accept_client_mesh_reader_or_server_path(tmp_path):
    staged = tmp_path / "imports" / "models" / "minimal.obj"
    staged.parent.mkdir(parents=True)
    staged.write_text("v 0 0 0\n", encoding="utf-8")
    payload = import_payload(mesh_reader="server.reader", source_path="C:/secret")

    response = TestClient(
        create_app(tmp_path, run_mode="development")
    ).post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request_body"
    assert not (tmp_path / "reports" / "model_matching_operations").exists()


def test_import_rejects_directory_as_staged_source(tmp_path):
    staged = tmp_path / "imports" / "models" / "directory.obj"
    staged.mkdir(parents=True)

    response = TestClient(
        create_app(tmp_path, run_mode="development")
    ).post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json={"staged_source": "imports/models/directory.obj"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_staged_source"


def test_mesh_engine_unavailable_maps_to_503(tmp_path, monkeypatch):
    staged = tmp_path / "imports" / "models" / "minimal.obj"
    staged.parent.mkdir(parents=True)
    staged.write_text("v 0 0 0\n", encoding="utf-8")

    def unavailable(*args, **kwargs):
        raise ModelMatchingError(
            "mesh_engine_unavailable", "Mesh engine unavailable."
        )

    monkeypatch.setattr(api_module, "import_model_version", unavailable)
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models/pump-a/versions",
        headers=EXPERT_HEADERS,
        json=import_payload(),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "mesh_engine_unavailable"


def test_empty_operation_directory_is_recovered_in_place(tmp_path):
    collision = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
    )
    collision.mkdir(parents=True)

    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers=EXPERT_HEADERS,
        json=model_payload(),
    )

    assert response.status_code == 201
    assert response.json()["model_id"] == "pump-a"
    assert (collision / "operation.json").is_file()
    assert [
        event["event_type"]
        for event in audit_module.read_operation_events(
            tmp_path, "op-model-001"
        )
    ] == ["operation.started", "model_asset.created", "operation.completed"]


def test_invalid_audit_request_domain_error_maps_to_400(tmp_path):
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).post(
        "/model-library/models",
        headers=EXPERT_HEADERS,
        json=model_payload(request_id="invalid$request"),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_audit_request"


def test_phase15_http_error_mapping_and_protected_audit_read(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))
    assert client.get("/model-library/models/missing").status_code == 404
    assert client.post(
        "/model-library/models", headers=EXPERT_HEADERS, json=model_payload()
    ).status_code == 201
    duplicate = client.post(
        "/model-library/models",
        headers=EXPERT_HEADERS,
        json=model_payload(
            display_name="Different",
            operation_id="op-model-002",
            request_id="request-model-002",
            idempotency_key="idem-model-002",
        ),
    )
    denied_audit = client.get("/audit/operations/op-model-001")
    audit = client.get(
        "/audit/operations/op-model-001",
        headers={"X-Actor-ID": "auditor-a", "X-Actor-Roles": "auditor"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "model_exists"
    assert denied_audit.status_code == 403
    assert audit.status_code == 200
    assert audit.json()["chain_valid"] is True
    assert audit.json()["operation"]["operation_id"] == "op-model-001"
    assert len(audit.json()["events"]) == 3


def test_audit_route_uses_one_verified_snapshot(tmp_path, monkeypatch):
    operation_root = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-snapshot"
    )
    operation_root.mkdir(parents=True)
    calls = []
    expected = {
        "operation": {"operation_id": "op-snapshot", "status": "running"},
        "events": [{"operation_id": "op-snapshot", "sequence": 1}],
    }

    def snapshot(project_root, operation_id):
        calls.append((project_root, operation_id))
        return expected

    def forbidden_legacy_read(*args, **kwargs):
        raise AssertionError("Audit route performed a second legacy read.")

    monkeypatch.setattr(
        api_module, "read_verified_operation_snapshot", snapshot
    )
    monkeypatch.setattr(
        api_module, "load_operation", forbidden_legacy_read, raising=False
    )
    monkeypatch.setattr(
        api_module,
        "read_verified_operation_events",
        forbidden_legacy_read,
        raising=False,
    )

    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).get(
        "/audit/operations/op-snapshot",
        headers={"X-Actor-ID": "auditor-a", "X-Actor-Roles": "auditor"},
    )

    assert response.status_code == 200
    assert response.json() == {**expected, "chain_valid": True}
    assert calls == [(tmp_path, "op-snapshot")]


def test_audit_route_maps_invalid_snapshot_request_to_400(tmp_path):
    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).get(
        "/audit/operations/invalid$request",
        headers={"X-Actor-ID": "auditor-a", "X-Actor-Roles": "auditor"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_audit_request"


def test_audit_route_rejects_projection_tamper_without_repair(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))
    assert client.post(
        "/model-library/models", headers=EXPERT_HEADERS, json=model_payload()
    ).status_code == 201
    projection_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "operation.json"
    )
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection["result"] = {"model_id": "forged"}
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    tampered_bytes = projection_path.read_bytes()

    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).get(
        "/audit/operations/op-model-001",
        headers={"X-Actor-ID": "auditor-a", "X-Actor-Roles": "auditor"},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "audit_integrity_error"
    assert projection_path.read_bytes() == tampered_bytes


def test_corrupt_audit_read_fails_closed(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))
    assert client.post(
        "/model-library/models", headers=EXPERT_HEADERS, json=model_payload()
    ).status_code == 201
    events_path = (
        tmp_path
        / "reports"
        / "model_matching_operations"
        / "op-model-001"
        / "events.jsonl"
    )
    events = events_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(events[1])
    event["details"]["model_id"] = "tampered"
    events[1] = json.dumps(event)
    events_path.write_text("\n".join(events) + "\n", encoding="utf-8")

    response = TestClient(
        create_app(tmp_path, run_mode="development"),
        raise_server_exceptions=False,
    ).get(
        "/audit/operations/op-model-001",
        headers={"X-Actor-ID": "auditor-a", "X-Actor-Roles": "auditor"},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "audit_integrity_error"
