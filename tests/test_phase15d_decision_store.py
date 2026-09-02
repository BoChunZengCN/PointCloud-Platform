import json

import pytest

import pc_system.model_match_decision as store
from pc_system.model_binding import build_model_binding
from pc_system.model_matching_audit import start_operation, complete_operation, read_verified_operation_snapshot
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_resource_lock import model_resource_lock
from phase15d_support import OPERATOR, publish_registration


@pytest.fixture
def prepared(tmp_path):
    report = publish_registration(tmp_path)
    identity = {key: report[key] for key in ("asset_id", "source_id", "instance_id", "retrieval_run_id")}
    context = store.load_decision_context(tmp_path, **identity)
    request = store.normalize_decision_request(
        decision_id="decision-1", case_id=context["case_id"], decision="confirmed",
        decision_reason="现场已确认", verification_scope="operational_pose",
        registration_id=report["registration_id"], candidate_rank=1,
        expected_case_revision=context["case_revision"], binding_id="binding-1",
    )
    operation, _ = start_operation(
        tmp_path, operation_id="op-decision-1", operation_type="model_match.decision",
        principal=OPERATOR, request_id="req-decision-1", idempotency_key="idem-decision-1", request_payload=request,
    )
    snapshot = read_verified_operation_snapshot(tmp_path, operation["operation_id"])
    operation["started_event_at"] = snapshot["events"][0]["timestamp"]
    return report, identity, context, request, operation


def publish(tmp_path, prepared, *, finish=True):
    report, identity, context, request, operation = prepared
    with model_resource_lock(tmp_path, "model-decision", identity["asset_id"], identity["source_id"], identity["instance_id"]):
        owner = store._prepare_decision_owner_locked(tmp_path, request=request, context=context,
                                                     operation=operation, principal=OPERATOR, transition="create", restores_binding=None)
        frozen = store._load_frozen_decision_context_locked(tmp_path, owner=owner, operation=operation)
        decision = store.build_match_decision(request=request, context=frozen, operation=operation, principal=OPERATOR, previous_decision_id=None)
        binding = build_model_binding(binding_id="binding-1", decision=decision, registration=report,
                                      transition="create", current_binding=None, restores_binding=None)
        commit = store._publish_decision_bundle_locked(tmp_path, owner=owner, operation=operation, decision=decision, binding=binding)
        if finish:
            complete_operation(tmp_path, operation["operation_id"], {"result_fingerprint": commit["result_fingerprint"]})
    return owner, commit


def directory(tmp_path, identity):
    return tmp_path / "reports" / "model_match_decisions" / identity["asset_id"] / identity["source_id"] / identity["instance_id"] / "decision-1"


def test_context_and_locator_use_verified_evidence(prepared, tmp_path):
    report, identity, context, _, _ = prepared
    assert context["current_binding"] is None
    assert list(context["registrations_by_id"]) == [report["registration_id"]]
    assert store.resolve_decision_case_identity(tmp_path, context["case_id"]) == {
        **identity, "object_fingerprint": report["object_fingerprint"],
    }
    with pytest.raises(ModelMatchingError) as caught:
        store.resolve_decision_case_identity(tmp_path, "f" * 64)
    assert caught.value.code == "decision_item_not_found"


def test_audited_commit_is_visible_and_replay_reads_same_result(prepared, tmp_path):
    _, identity, before, _, operation = prepared
    publish(tmp_path, prepared)
    values = store.list_decision_bundles(tmp_path)
    assert len(values) == 1
    assert values[0]["binding"]["binding_id"] == "binding-1"
    current = store.load_decision_context(tmp_path, **identity)
    assert current["case_revision"] != before["case_revision"]
    assert current["current_binding"]["binding_id"] == "binding-1"
    assert store.load_operation_decision_result(tmp_path, operation) == values[0]


def test_commit_without_completed_audit_is_hidden_but_blocks_another_writer(prepared, tmp_path):
    _, identity, context, _, operation = prepared
    owner, commit = publish(tmp_path, prepared, finish=False)
    assert store.list_decision_bundles(tmp_path) == []
    foreign = {**operation, "operation_id": "another-operation"}
    with pytest.raises(ModelMatchingError) as caught:
        store._inspect_object_decision_writes_locked(tmp_path, identity=identity, operation=foreign)
    assert caught.value.code == "publication_recovery_required"
    assert store._inspect_object_decision_writes_locked(tmp_path, identity=identity, operation=operation) == owner
    assert store.load_decision_context(tmp_path, **identity)["case_revision"] == context["case_revision"]
    complete_operation(tmp_path, operation["operation_id"], {"result_fingerprint": commit["result_fingerprint"]})
    assert len(store.list_decision_bundles(tmp_path)) == 1


@pytest.mark.parametrize("name", ["owner.json", "decision.json", "binding.json", "commit.json"])
def test_tampered_bundle_is_never_exposed(prepared, tmp_path, name):
    publish(tmp_path, prepared)
    path = directory(tmp_path, prepared[1]) / name
    value = json.loads(path.read_text(encoding="utf-8"))
    value["tampered"] = True
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as caught:
        store.list_decision_bundles(tmp_path)
    assert caught.value.code == "artifact_integrity_failed"


def test_missing_binding_and_duplicate_json_key_fail_closed(prepared, tmp_path):
    publish(tmp_path, prepared)
    path = directory(tmp_path, prepared[1]) / "binding.json"
    saved = path.read_bytes()
    path.unlink()
    with pytest.raises(ModelMatchingError):
        store.list_decision_bundles(tmp_path)
    path.write_bytes(saved[:-1] + b',"binding_id":"forged"}')
    with pytest.raises(ModelMatchingError):
        store.list_decision_bundles(tmp_path)


def test_non_plain_object_directory_is_rejected(tmp_path):
    parent = tmp_path / "reports" / "model_match_decisions"
    parent.mkdir(parents=True)
    (parent / "asset").write_text("not a directory")
    with pytest.raises(ModelMatchingError) as caught:
        store.list_decision_bundles(tmp_path)
    assert caught.value.code == "artifact_integrity_failed"


def test_frozen_owner_rejects_another_request(prepared, tmp_path):
    owner, _ = publish(tmp_path, prepared, finish=False)
    forged = {**owner, "request": {**owner["request"], "decision_reason": "tampered"}}
    with pytest.raises(ModelMatchingError) as caught:
        store._load_frozen_decision_context_locked(tmp_path, owner=forged, operation=prepared[4])
    assert caught.value.code == "artifact_integrity_failed"


def test_partial_owner_cannot_be_moved_to_another_decision_identity(prepared, tmp_path):
    publish(tmp_path, prepared, finish=False)
    original = directory(tmp_path, prepared[1])
    (original / "commit.json").unlink()
    original.rename(original.with_name("another-decision"))
    with pytest.raises(ModelMatchingError) as caught:
        store._inspect_object_decision_writes_locked(tmp_path, identity=prepared[1], operation=prepared[4])
    assert caught.value.code == "artifact_integrity_failed"


def test_no_match_has_no_binding_and_owner_only_is_hidden(prepared, tmp_path):
    _, identity, context, _, _ = prepared
    request = store.normalize_decision_request(
        decision_id="decision-no-match", case_id=context["case_id"], decision="no_match",
        decision_reason="模型库暂无合适模型", verification_scope="identity",
        registration_id=None, candidate_rank=None, expected_case_revision=context["case_revision"], binding_id=None,
    )
    operation, _ = start_operation(tmp_path, operation_id="op-no-match", operation_type="model_match.decision",
        principal=OPERATOR, request_id="req-no-match", idempotency_key="idem-no-match", request_payload=request)
    operation["started_event_at"] = read_verified_operation_snapshot(tmp_path, "op-no-match")["events"][0]["timestamp"]
    owner = store._prepare_decision_owner_locked(tmp_path, request=request, context=context, operation=operation,
                                                principal=OPERATOR, transition=None, restores_binding=None)
    assert store.list_decision_bundles(tmp_path) == []
    decision = store.build_match_decision(request=request, context=context, operation=operation,
                                          principal=OPERATOR, previous_decision_id=None)
    commit = store._publish_decision_bundle_locked(tmp_path, owner=owner, operation=operation, decision=decision, binding=None)
    complete_operation(tmp_path, "op-no-match", {"result_fingerprint": commit["result_fingerprint"]})
    bundle = store.load_decision_bundle(tmp_path, **{k: identity[k] for k in ("asset_id", "source_id", "instance_id")}, decision_id="decision-no-match")
    assert bundle["binding"] is None
    path = directory(tmp_path, identity).with_name("decision-no-match") / "binding.json"
    path.write_text("{}")
    with pytest.raises(ModelMatchingError):
        store.list_decision_bundles(tmp_path)
