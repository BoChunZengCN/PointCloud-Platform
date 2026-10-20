import copy
import json

from fastapi.testclient import TestClient

from pc_system import mesh_reconstruction
from pc_system.api import create_app
from pc_system.mesh_reconstruction_config import (
    publish_mesh_config,
    publish_mesh_quality_policy,
)
from pc_system.model_matching_identity import Principal

from test_mesh_reconstruction import FakeWorker, _source_snapshot
from test_mesh_reconstruction_config import POISSON_CONFIG, QUALITY_POLICY


EXPERT = Principal("mesh-expert", frozenset({"expert"}), "configured_token")


def _headers(role="expert"):
    return {"X-Actor-ID": f"mesh-{role}", "X-Actor-Roles": role}


def _prepare(root, monkeypatch):
    publish_mesh_config(
        root,
        config_id="mesh-poisson-v1",
        config=POISSON_CONFIG,
        principal=EXPERT,
        operation_id="publish-config-1",
        request_id="request-config-1",
        idempotency_key="idem-config-1",
    )
    publish_mesh_quality_policy(
        root,
        policy_id="mesh-quality-v1",
        policy=QUALITY_POLICY,
        principal=EXPERT,
        operation_id="publish-policy-1",
        request_id="request-policy-1",
        idempotency_key="idem-policy-1",
    )
    monkeypatch.setattr(
        mesh_reconstruction,
        "freeze_mesh_source",
        lambda *args, **kwargs: copy.deepcopy(_source_snapshot()),
    )


def _run_payload(run_id="mesh-run-1", sequence="1"):
    return {
        "run_id": run_id,
        "source_kind": "phase14_correction_release",
        "source_ref": {
            "asset_id": "scan",
            "release_id": "release-1",
            "instance_id": "pipe-1",
        },
        "config_id": "mesh-poisson-v1",
        "policy_id": "mesh-quality-v1",
        "operation_id": f"create-run-{sequence}",
        "request_id": f"request-run-{sequence}",
        "idempotency_key": f"idem-run-{sequence}",
    }


def _client(root):
    return TestClient(
        create_app(
            root,
            run_mode="development",
            mesh_worker=FakeWorker(),
            scan_to_mesh_cursor_secret=b"test-only-cursor-secret",
        )
    )


def test_api_exposes_role_cropped_mesh_workflow_without_local_paths(tmp_path, monkeypatch):
    """绕过目录投影或泄露工件 path/点集会使该端到端契约失败。"""
    _prepare(tmp_path, monkeypatch)
    client = _client(tmp_path)

    session = client.get("/scan-to-mesh/session", headers=_headers("expert"))
    configs = client.get("/scan-to-mesh/configs", headers=_headers("expert"))
    created = client.post(
        "/scan-to-mesh/runs", headers=_headers("expert"), json=_run_payload()
    )
    operator_before = client.get(
        "/scan-to-mesh/runs", headers=_headers("operator")
    )
    audited = client.get(
        "/scan-to-mesh/runs/mesh-run-1", headers=_headers("auditor")
    )

    assert session.status_code == configs.status_code == created.status_code == 200
    assert session.json() == {"viewer_role": "expert"}
    assert configs.json()["items"][0]["config_id"] == "mesh-poisson-v1"
    assert created.json()["run_id"] == "mesh-run-1"
    assert created.json()["quality"]["status"] == "passed"
    assert operator_before.json()["items"] == []
    assert audited.json()["audit"]["operation_id"] == "create-run-1"

    decision = client.post(
        "/scan-to-mesh/runs/mesh-run-1/decision",
        headers=_headers("expert"),
        json={
            "decision": "approve",
            "reason": "质量证据已核验",
            "acknowledgements": [],
            "operation_id": "decision-1",
            "request_id": "request-decision-1",
            "idempotency_key": "idem-decision-1",
        },
    )
    object_key = decision.json()["object_key"]
    released = client.post(
        f"/scan-to-mesh/objects/{object_key}/releases",
        headers=_headers("expert"),
        json={
            "run_id": "mesh-run-1",
            "release_id": "mesh-release-1",
            "action": "activate",
            "reason": "发布经核验网格",
            "expected_current_release_id": None,
            "operation_id": "release-op-1",
            "request_id": "request-release-1",
            "idempotency_key": "idem-release-1",
        },
    )
    operator_after = client.get(
        "/scan-to-mesh/runs", headers=_headers("operator")
    )
    history = client.get(
        f"/scan-to-mesh/objects/{object_key}/releases",
        headers=_headers("auditor"),
    )

    assert decision.status_code == released.status_code == 201
    assert operator_after.status_code == history.status_code == 200
    assert operator_after.json()["items"] == [
        {
            "run_id": "mesh-run-1",
            "object_key": object_key,
            "source_kind": "phase14_correction_release",
            "source_identity": {
                "asset_id": "scan",
                "release_id": "release-1",
                "instance_id": "pipe-1",
                "class_id": "pipe",
            },
            "quality_status": "passed",
            "publication_status": "current",
            "current_release_id": "mesh-release-1",
            "created_at": created.json()["created_at"],
        }
    ]
    encoded = json.dumps(
        {"created": created.json(), "audited": audited.json(), "history": history.json()},
        ensure_ascii=False,
    )
    for forbidden in ("source_points", "stderr", "raw_mesh.ply", "preview_mesh.glb", str(tmp_path)):
        assert forbidden not in encoded


def test_api_cursor_is_signed_and_bound_to_role_and_filter(tmp_path, monkeypatch):
    """可篡改、可跨角色或跨筛选复用的游标会使该测试失败。"""
    _prepare(tmp_path, monkeypatch)
    client = _client(tmp_path)
    for sequence in (1, 2):
        assert client.post(
            "/scan-to-mesh/runs",
            headers=_headers("expert"),
            json=_run_payload(f"mesh-run-{sequence}", str(sequence)),
        ).status_code == 200

    first = client.get(
        "/scan-to-mesh/runs?limit=1&status=passed", headers=_headers("expert")
    )
    cursor = first.json()["next_cursor"]
    second = client.get(
        f"/scan-to-mesh/runs?limit=1&status=passed&cursor={cursor}",
        headers=_headers("expert"),
    )
    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    bad_signature = client.get(
        f"/scan-to-mesh/runs?limit=1&status=passed&cursor={tampered}",
        headers=_headers("expert"),
    )
    changed_filter = client.get(
        f"/scan-to-mesh/runs?limit=1&status=pending&cursor={cursor}",
        headers=_headers("expert"),
    )
    changed_role = client.get(
        f"/scan-to-mesh/runs?limit=1&status=passed&cursor={cursor}",
        headers=_headers("auditor"),
    )

    assert [item["run_id"] for item in first.json()["items"] + second.json()["items"]] == [
        "mesh-run-1",
        "mesh-run-2",
    ]
    for response in (bad_signature, changed_filter, changed_role):
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "scan_to_mesh_catalog_invalid"


def test_api_rejects_untrusted_and_malformed_mesh_requests_with_stable_errors(tmp_path, monkeypatch):
    """未认证、重复字段、未知字段或非法来源不能进入领域状态机。"""
    _prepare(tmp_path, monkeypatch)
    client = _client(tmp_path)

    denied = client.get("/scan-to-mesh/session")
    unknown = client.post(
        "/scan-to-mesh/runs",
        headers=_headers("expert"),
        json={**_run_payload(), "local_path": "C:/secret/source.ply"},
    )
    duplicate_body = json.dumps(_run_payload(), ensure_ascii=False)[:-1] + ',"run_id":"other"}'
    duplicate = client.post(
        "/scan-to-mesh/runs",
        headers={**_headers("expert"), "Content-Type": "application/json"},
        content=duplicate_body.encode("utf-8"),
    )
    invalid_source = client.post(
        "/scan-to-mesh/runs",
        headers=_headers("expert"),
        json={**_run_payload(), "source_kind": "local_file", "operation_id": "invalid-source"},
    )
    missing = client.get(
        "/scan-to-mesh/runs/unknown-run", headers=_headers("expert")
    )

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "permission_denied"
    assert len(list((tmp_path / "reports" / "model_matching_operations").glob("denied-*"))) == 1
    for response in (unknown, duplicate):
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_request_body"
    assert invalid_source.status_code == 400
    assert invalid_source.json()["detail"]["code"] == "mesh_run_invalid"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "mesh_run_not_found"
