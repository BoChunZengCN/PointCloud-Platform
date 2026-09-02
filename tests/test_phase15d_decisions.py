import concurrent.futures
import multiprocessing
import os
import threading

import pytest

import pc_system.model_match_decision as service
from pc_system.model_matching_audit import read_verified_operation_snapshot
from pc_system.model_matching_errors import ModelMatchingError
from phase15d_support import OPERATOR, EXPERT, prepare_decision_case, publish_registration


def arguments(case, sequence=1, **changes):
    return {**case.request_fields,
            "decision_id": f"decision-{sequence}", "binding_id": f"binding-{sequence}",
            "decision": "confirmed", "decision_reason": "现场核验一致", "verification_scope": "operational_pose",
            "principal": OPERATOR, "operation_id": f"op-decision-{sequence}",
            "request_id": f"req-decision-{sequence}", "idempotency_key": f"idem-decision-{sequence}", **changes}


def test_confirm_replay_and_old_page_conflict(tmp_path):
    case = prepare_decision_case(tmp_path)
    request = arguments(case)
    first = service.decide_model_match(tmp_path, **request)
    assert first["binding"]["binding_id"] == "binding-1"
    assert service.decide_model_match(tmp_path, **request) == first
    with pytest.raises(ModelMatchingError) as caught:
        service.decide_model_match(tmp_path, **arguments(case, 2))
    assert caught.value.code == "decision_conflict"


@pytest.mark.parametrize("mode,principal,allowed", [("passed", OPERATOR, True), ("review_required", OPERATOR, False),
                                                   ("review_required", EXPERT, True), ("rejected", EXPERT, False)])
def test_service_enforces_registration_eligibility(tmp_path, mode, principal, allowed):
    case = prepare_decision_case(tmp_path, mode=mode)
    if allowed:
        assert service.decide_model_match(tmp_path, **arguments(case, principal=principal))["binding"] is not None
    else:
        with pytest.raises(ModelMatchingError):
            service.decide_model_match(tmp_path, **arguments(case, principal=principal))
        assert read_verified_operation_snapshot(tmp_path, "op-decision-1")["operation"]["status"] == "failed"


def test_failure_after_commit_blocks_foreign_operation_and_recovers_once(tmp_path, monkeypatch):
    case = prepare_decision_case(tmp_path)
    complete = service.complete_operation
    monkeypatch.setattr(service, "complete_operation", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected failure")))
    with pytest.raises(ModelMatchingError) as caught:
        service.decide_model_match(tmp_path, **arguments(case))
    assert caught.value.code == "publication_recovery_required"
    assert service.list_decision_bundles(tmp_path) == []
    with pytest.raises(ModelMatchingError) as caught:
        service.decide_model_match(tmp_path, **arguments(case, 2))
    assert caught.value.code == "publication_recovery_required"
    monkeypatch.setattr(service, "complete_operation", complete)
    recovered = service.decide_model_match(tmp_path, **arguments(case))
    assert recovered["binding"]["binding_id"] == "binding-1"
    events = read_verified_operation_snapshot(tmp_path, "op-decision-1")["events"]
    assert sum(e["event_type"] == "model_binding.created" for e in events) == 1


def test_two_concurrent_confirmations_have_one_winner(tmp_path):
    case = prepare_decision_case(tmp_path)
    def submit(sequence):
        try:
            return service.decide_model_match(tmp_path, **arguments(case, sequence))["decision"]["decision"]
        except ModelMatchingError as exc:
            return exc.code
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        results = list(executor.map(submit, (1, 2)))
    assert sorted(results) == ["confirmed", "decision_conflict"]
    assert len(service.list_decision_bundles(tmp_path)) == 1


def test_new_evidence_does_not_allow_a_second_binding_root(tmp_path):
    case = prepare_decision_case(tmp_path)
    service.decide_model_match(tmp_path, **arguments(case))
    publish_registration(tmp_path, sequence=2)
    current = service.load_decision_context(tmp_path, **case.identity)
    with pytest.raises(ModelMatchingError) as caught:
        service.decide_model_match(tmp_path, **arguments(case, 2, expected_case_revision=current["case_revision"], registration_id="registration-2"))
    assert caught.value.code == "binding_exists"


def test_expert_supersede_restore_and_replay(tmp_path):
    case = prepare_decision_case(tmp_path)
    service.decide_model_match(tmp_path, **arguments(case))
    publish_registration(tmp_path, sequence=2)
    current = service.load_decision_context(tmp_path, **case.identity)
    common = dict(**case.identity, decision_reason="专家复核", verification_scope="expert_pose", principal=EXPERT)
    second = service.supersede_model_binding(tmp_path, **common, current_binding_id="binding-1", registration_id="registration-2",
        candidate_rank=1, decision_id="decision-2", binding_id="binding-2", expected_case_revision=current["case_revision"],
        operation_id="op-replace", request_id="req-replace", idempotency_key="idem-replace")
    assert second["binding"]["supersedes_binding_id"] == "binding-1"
    current = service.load_decision_context(tmp_path, **case.identity)
    request = dict(common, current_binding_id="binding-2", restores_binding_id="binding-1", decision_id="decision-3", binding_id="binding-3",
                   expected_case_revision=current["case_revision"], operation_id="op-restore", request_id="req-restore", idempotency_key="idem-restore")
    restored = service.restore_model_binding(tmp_path, **request)
    assert restored["binding"]["supersedes_binding_id"] == "binding-2"
    assert restored["binding"]["restores_binding_id"] == "binding-1"
    assert service.restore_model_binding(tmp_path, **request) == restored


def _exit_after_owner(root, request):
    prepare = service._prepare_decision_owner_locked
    def crash(*args, **kwargs):
        prepare(*args, **kwargs)
        os._exit(73)
    service._prepare_decision_owner_locked = crash
    service.decide_model_match(root, **request)


def test_process_exit_releases_lock_but_keeps_owner_recovery_barrier(tmp_path):
    case = prepare_decision_case(tmp_path)
    process = multiprocessing.get_context("spawn").Process(target=_exit_after_owner, args=(tmp_path, arguments(case)))
    process.start()
    process.join(30)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("Child did not exit at the owner publication boundary")
    assert process.exitcode == 73
    with pytest.raises(ModelMatchingError) as caught:
        service.decide_model_match(tmp_path, **arguments(case, 2))
    assert caught.value.code == "publication_recovery_required"
    recovered = service.decide_model_match(tmp_path, **arguments(case))
    assert recovered["binding"]["binding_id"] == "binding-1"


def test_object_lock_is_held_until_audit_completion(tmp_path, monkeypatch):
    case = prepare_decision_case(tmp_path)
    at_complete, release, second_started = threading.Event(), threading.Event(), threading.Event()
    original_complete, original_start = service.complete_operation, service.start_operation
    def complete(root, operation_id, result):
        if operation_id == "op-decision-1":
            at_complete.set()
            assert release.wait(10)
        return original_complete(root, operation_id, result)
    def start(*args, **kwargs):
        result = original_start(*args, **kwargs)
        if kwargs["operation_id"] == "op-decision-2":
            second_started.set()
        return result
    monkeypatch.setattr(service, "complete_operation", complete)
    monkeypatch.setattr(service, "start_operation", start)
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        first = executor.submit(service.decide_model_match, tmp_path, **arguments(case))
        try:
            assert at_complete.wait(10)
            second = executor.submit(service.decide_model_match, tmp_path, **arguments(case, 2))
            assert second_started.wait(10)
            assert not second.done()
        finally:
            release.set()
        assert first.result()["binding"]["binding_id"] == "binding-1"
        with pytest.raises(ModelMatchingError) as caught:
            second.result()
        assert caught.value.code == "decision_conflict"


def test_recovery_uses_frozen_evidence_when_a_new_registration_arrives(tmp_path, monkeypatch):
    case = prepare_decision_case(tmp_path)
    complete = service.complete_operation
    monkeypatch.setattr(service, "complete_operation", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected failure")))
    with pytest.raises(ModelMatchingError):
        service.decide_model_match(tmp_path, **arguments(case))
    publish_registration(tmp_path, sequence=2)
    monkeypatch.setattr(service, "complete_operation", complete)
    result = service.decide_model_match(tmp_path, **arguments(case))
    current = service.load_decision_context(tmp_path, **case.identity)
    assert result["decision"]["evidence_fingerprint"] != current["evidence_fingerprint"]
    assert result["binding"]["registration_id"] == "registration-1"
