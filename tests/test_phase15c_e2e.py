from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.model_matching_audit import read_verified_operation_snapshot
from phase15c_support import DeterministicRegistrationEngine, prepare_phase15c_case


def test_phase15c_api_e2e_stops_before_model_binding(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    client = TestClient(
        create_app(
            tmp_path,
            api_key="legacy-key",
            run_mode="production",
            principal_bindings={
                "expert-token": {"actor_id": "alice", "roles": ["expert"]}
            },
            registration_engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        )
    )
    response = client.post(
        "/model-matching/registrations",
        json={
            **prepared,
            "registration_id": "registration-e2e-1",
            "candidate_rank": 1,
            "operation_id": "op-registration-e2e-1",
            "request_id": "req-registration-e2e-1",
            "idempotency_key": "idem-registration-e2e-1",
        },
        headers={"Authorization": "Bearer expert-token"},
    )

    assert response.status_code == 200
    assert response.json()["gate_status"] == "passed"
    snapshot = read_verified_operation_snapshot(tmp_path, "op-registration-e2e-1")
    assert snapshot["operation"]["status"] == "completed"
    assert not list(tmp_path.rglob("*model_binding*"))
