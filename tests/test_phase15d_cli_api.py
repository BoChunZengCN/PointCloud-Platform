import json

import pytest
from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from phase15d_support import prepare_decision_case, publish_registration
from test_phase15d_decisions import arguments


def client_for(root):
    return TestClient(create_app(root, run_mode="production", api_key="phase15d-test-service-key",
        principal_bindings={role + "-token": {"actor_id": role + "-a", "roles": [role]}
                            for role in ("operator", "expert", "auditor")}))


def headers(role="operator"):
    return {"Authorization": "Bearer " + role + "-token"}


def payload(case, sequence=1, **changes):
    result = arguments(case, sequence, **changes)
    result.pop("principal")
    return result


def test_api_confirm_replay_conflict_and_cropped_bindings(tmp_path):
    case = prepare_decision_case(tmp_path)
    client = client_for(tmp_path)
    path = "/model-matching/decision-items"
    listing = client.get(path, headers=headers())
    assert listing.status_code == 200
    assert listing.json()["counts"]["pending"] == 1
    response = client.post("/model-matching/decisions", json=payload(case), headers=headers())
    assert response.status_code == 201, response.text
    assert "rigid_transform_4x4" not in response.text
    assert client.post("/model-matching/decisions", json=payload(case), headers=headers()).json() == response.json()
    conflict = client.post("/model-matching/decisions", json=payload(case, 2), headers=headers())
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "decision_conflict"
    detail = client.get(path + "/" + case.request_fields["case_id"], headers=headers()).json()
    assert detail["status"] == "processed" and "technical" not in detail
    binding_path = "/model-matching/bindings/" + "/".join(case.identity[k] for k in ("asset_id", "source_id", "instance_id"))
    for suffix in ("", "/history"):
        business = client.get(binding_path + suffix, headers=headers())
        assert business.status_code == 200
        assert "rigid_transform_4x4" not in business.text
        technical = client.get(binding_path + suffix, headers=headers("auditor"))
        assert technical.status_code == 200 and "rigid_transform_4x4" in technical.text


@pytest.mark.parametrize("path,role", [("/model-matching/decisions", "auditor"),
    ("/model-matching/bindings/binding-1/restore", "operator"),
    ("/model-matching/bindings/binding-1/supersede", "operator")])
def test_api_authorizes_before_reading_body(tmp_path, path, role):
    response = client_for(tmp_path).post(path, content=b"{" * (1024 * 1024 + 1), headers=headers(role))
    assert response.status_code == 403 and response.json()["detail"]["code"] == "permission_denied"


@pytest.mark.parametrize("change", [{"candidate_rank": True}, {"registration_id": 123},
    {"binding_id": []}, {"actor_id": "forged"}, {"roles": ["expert"]}])
def test_api_requires_exact_request_shape(tmp_path, change):
    values = dict(decision_id="d", case_id="c", decision="no_match", decision_reason="无匹配",
                  verification_scope="identity", registration_id=None, binding_id=None, candidate_rank=None,
                  expected_case_revision="a" * 64, operation_id="o", request_id="r", idempotency_key="i", **{})
    response = client_for(tmp_path).post("/model-matching/decisions", json={**values, **change}, headers=headers())
    assert response.status_code == 400


def test_api_rejects_duplicate_json_fields(tmp_path):
    response = client_for(tmp_path).post("/model-matching/decisions",
        content='{"decision":"confirmed","decision":"no_match"}', headers=headers())
    assert response.status_code == 400


def test_api_empty_list_and_unknown_case(tmp_path):
    client = client_for(tmp_path)
    assert client.get("/model-matching/decision-items", headers=headers()).json()["items"] == []
    assert client.get("/model-matching/decision-items/" + "a" * 64, headers=headers()).status_code == 404


def test_api_expert_supersedes_and_restores_without_rewriting_history(tmp_path):
    case = prepare_decision_case(tmp_path)
    client = client_for(tmp_path)
    assert client.post("/model-matching/decisions", json=payload(case), headers=headers()).status_code == 201
    publish_registration(tmp_path, sequence=2)
    path = "/model-matching/decision-items/" + case.request_fields["case_id"]
    revision = lambda: client.get(path, headers=headers("expert")).json()["case_revision"]
    common = dict(**case.identity, decision_reason="专家复核", verification_scope="expert_pose")
    replacement = dict(common, registration_id="registration-2", candidate_rank=1, decision_id="decision-2",
        binding_id="binding-2", expected_case_revision=revision(), operation_id="op-replace",
        request_id="req-replace", idempotency_key="idem-replace")
    response = client.post("/model-matching/bindings/binding-1/supersede", json=replacement, headers=headers("expert"))
    assert response.status_code == 201, response.text
    restore = dict(common, restores_binding_id="binding-1", decision_id="decision-3", binding_id="binding-3",
        expected_case_revision=revision(), operation_id="op-restore", request_id="req-restore", idempotency_key="idem-restore")
    response = client.post("/model-matching/bindings/binding-2/restore", json=restore, headers=headers("expert"))
    assert response.status_code == 201, response.text
    assert response.json()["binding"]["restores_binding_id"] == "binding-1"
    assert response.json()["binding"]["supersedes_binding_id"] == "binding-2"


def test_api_duplicate_fields_are_rejected_before_domain_lookup(tmp_path):
    values = dict(decision_id="d", case_id="a" * 64, decision="no_match", decision_reason="无匹配",
        verification_scope="identity", registration_id=None, candidate_rank=None, binding_id=None,
        expected_case_revision="b" * 64, operation_id="o", request_id="r", idempotency_key="i")
    body = json.dumps(values)[:-1] + ', "decision":"no_match"}'
    response = client_for(tmp_path).post("/model-matching/decisions", content=body, headers=headers())
    assert response.status_code == 400 and response.json()["detail"]["code"] == "invalid_request_body"
