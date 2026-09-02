import copy

import pytest

from pc_system.model_match_decision import (
    build_match_decision,
    compute_case_id,
    compute_case_revision,
    compute_evidence_fingerprint,
    normalize_decision_request,
    require_decision_allowed,
)
from pc_system.model_matching_errors import ModelMatchingError
from phase15d_support import AUDITOR, EXPERT, OPERATOR


def request(**changes):
    value = dict(
        decision_id="decision-1", case_id="a" * 64, decision="confirmed",
        decision_reason="  现场核对一致  ", verification_scope="operational_pose",
        registration_id="registration-1", candidate_rank=1,
        expected_case_revision="b" * 64, binding_id="binding-1",
    )
    return {**value, **changes}


def test_case_identity_binds_every_object_identity_field():
    args = ["asset-1", "source-1", "instance-1", "a" * 64, "retrieval-1"]
    original = compute_case_id(*args)
    assert len(original) == 64
    assert original == compute_case_id(*args)
    for index in range(len(args)):
        changed = args.copy()
        changed[index] = "b" * 64 if index == 3 else changed[index] + "-2"
        assert compute_case_id(*changed) != original


def test_evidence_is_order_independent_but_binds_rank_id_and_report():
    one = dict(registration_id="r-1", candidate_rank=1, report_fingerprint="a" * 64)
    two = dict(registration_id="r-2", candidate_rank=2, report_fingerprint="b" * 64)
    original = compute_evidence_fingerprint([one, two])
    assert original == compute_evidence_fingerprint([two, one])
    for field, value in [("candidate_rank", 3), ("registration_id", "r-3"), ("report_fingerprint", "c" * 64)]:
        assert compute_evidence_fingerprint([{**one, field: value}, two]) != original
    with pytest.raises(ModelMatchingError):
        compute_evidence_fingerprint([one, one])


def test_revision_changes_for_either_head_or_upstream_evidence():
    args = ["a" * 64, "b" * 64, None, None]
    original = compute_case_revision(*args)
    for index in range(4):
        changed = args.copy()
        changed[index] = "c" * 64
        assert compute_case_revision(*changed) != original


@pytest.mark.parametrize("principal,gate,scope,allowed", [
    (OPERATOR, "passed", "identity", True),
    (OPERATOR, "passed", "operational_pose", True),
    (OPERATOR, "review_required", "identity", False),
    (OPERATOR, "passed", "expert_pose", False),
    (EXPERT, "review_required", "expert_pose", True),
    (EXPERT, "rejected", "identity", False),
    (EXPERT, "failed", "identity", False),
    (AUDITOR, "passed", "identity", False),
])
def test_confirm_role_and_gate_matrix(principal, gate, scope, allowed):
    if allowed:
        require_decision_allowed(principal, decision="confirmed", gate_status=gate, verification_scope=scope)
    else:
        with pytest.raises(ModelMatchingError) as caught:
            require_decision_allowed(principal, decision="confirmed", gate_status=gate, verification_scope=scope)
        assert caught.value.code in {"decision_not_allowed", "permission_denied"}


@pytest.mark.parametrize("decision", ["rejected", "no_match"])
def test_business_negative_decisions_are_allowed_but_auditor_cannot_write(decision):
    require_decision_allowed(OPERATOR, decision=decision, gate_status=None, verification_scope="identity")
    with pytest.raises(ModelMatchingError):
        require_decision_allowed(AUDITOR, decision=decision, gate_status=None, verification_scope="identity")


@pytest.mark.parametrize("changes", [
    {"candidate_rank": True}, {"candidate_rank": 0}, {"candidate_rank": 1.0},
    {"registration_id": "../r"}, {"decision_id": "../d"},
    {"case_id": "A" * 64}, {"expected_case_revision": "short"},
    {"decision": "auto"}, {"verification_scope": "unknown"},
    {"binding_id": None}, {"decision": "rejected"},
    {"decision": "no_match", "binding_id": None},
])
def test_malformed_or_incompatible_request_is_rejected(changes):
    with pytest.raises(ModelMatchingError):
        normalize_decision_request(**request(**changes))


@pytest.mark.parametrize("reason", ["", "  ", "x" * 1001, "a\x00b", 123, "\ud800"])
def test_reason_is_nonempty_bounded_unicode_text(reason):
    with pytest.raises(ModelMatchingError) as caught:
        normalize_decision_request(**request(decision_reason=reason))
    assert caught.value.code == "decision_reason_invalid"


def test_request_normalizes_reason_and_no_match_has_no_registration_or_binding():
    assert normalize_decision_request(**request())["decision_reason"] == "现场核对一致"
    result = normalize_decision_request(**request(
        decision="no_match", registration_id=None, candidate_rank=None, binding_id=None,
        decision_reason="中" * 1000,
    ))
    assert result["registration_id"] is None
    assert len(result["decision_reason"]) == 1000
    with pytest.raises(TypeError):
        normalize_decision_request(**request(), actor_id="forged")


def test_builder_binds_authoritative_context_and_original_actor_time():
    context = dict(
        asset_id="asset-1", source_id="source-1", instance_id="instance-1",
        object_fingerprint="c" * 64, retrieval_run_id="retrieval-1",
        evidence_fingerprint="d" * 64, decision_head_id=None,
        decision_head_fingerprint=None, binding_head_fingerprint=None,
        registrations_by_id={"registration-1": {"registration_id": "registration-1", "candidate_rank": 1, "gate_status": "passed"}},
    )
    context["case_id"] = compute_case_id("asset-1", "source-1", "instance-1", "c" * 64, "retrieval-1")
    context["case_revision"] = compute_case_revision("c" * 64, "d" * 64, None, None)
    normalized = normalize_decision_request(**request(case_id=context["case_id"], expected_case_revision=context["case_revision"]))
    operation = dict(operation_id="op-1", actor_id="operator-a", roles=["operator"],
                     principal_source="cli", started_event_at="2026-09-02T00:00:00Z")
    before = copy.deepcopy(context)
    result = build_match_decision(request=normalized, context=context, operation=operation, principal=OPERATOR, previous_decision_id=None)
    assert result["decided_by"] == "operator-a"
    assert result["decided_at"] == "2026-09-02T00:00:00Z"
    assert result["object_fingerprint"] == "c" * 64
    assert result["previous_decision_id"] is None
    assert result["expected_binding_head_fingerprint"] is None
    assert "binding_id" not in result
    assert context == before
    for bad in [{**normalized, "case_id": "e" * 64}, {**normalized, "candidate_rank": 2}]:
        with pytest.raises(ModelMatchingError):
            build_match_decision(request=bad, context=context, operation=operation, principal=OPERATOR, previous_decision_id=None)
    with pytest.raises(ModelMatchingError):
        build_match_decision(request=normalized, context=context, operation=operation, principal=EXPERT, previous_decision_id=None)
