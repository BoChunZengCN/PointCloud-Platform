"""人工决策的纯契约；持久化与服务入口随后在此接入。"""

import hashlib
import re

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_sampling import _canonical_json_bytes


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DECISIONS = frozenset({"confirmed", "rejected", "no_match"})
_SCOPES = frozenset({"identity", "operational_pose", "expert_pose"})


def _error(code: str, message: str) -> ModelMatchingError:
    return ModelMatchingError(code, message)


def _identifier(value: object, label: str) -> str:
    try:
        if type(value) is not str:
            raise ValueError(label)
        return validate_identifier(value, label)
    except ValueError as exc:
        raise _error("decision_not_allowed", f"Invalid {label}.") from exc


def _fingerprint(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise _error("artifact_integrity_failed", "Invalid decision fingerprint.")
    return value


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def compute_case_id(asset_id: str, source_id: str, instance_id: str,
                    object_fingerprint: str, retrieval_run_id: str) -> str:
    return _digest({
        "asset_id": _identifier(asset_id, "asset_id"),
        "source_id": _identifier(source_id, "source_id"),
        "instance_id": _identifier(instance_id, "instance_id"),
        "object_fingerprint": _fingerprint(object_fingerprint),
        "retrieval_run_id": _identifier(retrieval_run_id, "retrieval_run_id"),
    })


def compute_evidence_fingerprint(registrations: list[dict]) -> str:
    if type(registrations) is not list:
        raise _error("artifact_integrity_failed", "Invalid registration evidence collection.")
    evidence, identifiers = [], set()
    for report in registrations:
        if type(report) is not dict:
            raise _error("artifact_integrity_failed", "Invalid registration evidence.")
        registration_id = _identifier(report.get("registration_id"), "registration_id")
        rank = report.get("candidate_rank")
        if type(rank) is not int or rank < 1 or registration_id in identifiers:
            raise _error("artifact_integrity_failed", "Duplicate or invalid registration evidence.")
        identifiers.add(registration_id)
        evidence.append({
            "registration_id": registration_id, "candidate_rank": rank,
            "report_fingerprint": _fingerprint(report.get("report_fingerprint")),
        })
    evidence.sort(key=lambda item: (item["candidate_rank"], item["registration_id"], item["report_fingerprint"]))
    return _digest({"registrations": evidence})


def compute_case_revision(object_fingerprint: str, evidence_fingerprint: str,
                          decision_head_fingerprint: str | None,
                          binding_head_fingerprint: str | None) -> str:
    return _digest({
        "object_fingerprint": _fingerprint(object_fingerprint),
        "evidence_fingerprint": _fingerprint(evidence_fingerprint),
        "decision_head_fingerprint": _fingerprint(decision_head_fingerprint, nullable=True),
        "binding_head_fingerprint": _fingerprint(binding_head_fingerprint, nullable=True),
    })


def normalize_decision_request(*, decision_id: str, case_id: str, decision: str,
                               decision_reason: str, verification_scope: str,
                               registration_id: str | None, candidate_rank: int | None,
                               expected_case_revision: str, binding_id: str | None) -> dict:
    _identifier(decision_id, "decision_id")
    _fingerprint(case_id)
    _fingerprint(expected_case_revision)
    if type(decision) is not str or decision not in _DECISIONS:
        raise _error("decision_not_allowed", "Unknown decision action.")
    if type(verification_scope) is not str or verification_scope not in _SCOPES:
        raise _error("decision_not_allowed", "Unknown verification scope.")
    if type(decision_reason) is not str:
        raise _error("decision_reason_invalid", "Decision reason must be text.")
    reason = decision_reason.strip()
    try:
        reason.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _error("decision_reason_invalid", "Decision reason is not valid Unicode.") from exc
    if not reason or len(decision_reason) > 1000 or "\x00" in decision_reason:
        raise _error("decision_reason_invalid", "Decision reason must contain 1-1000 characters without NUL.")
    if decision == "no_match":
        if registration_id is not None or candidate_rank is not None:
            raise _error("decision_not_allowed", "No-match must not reference a registration.")
    else:
        _identifier(registration_id, "registration_id")
        if type(candidate_rank) is not int or candidate_rank < 1:
            raise _error("decision_not_allowed", "Candidate rank must be a positive integer.")
    if decision == "confirmed":
        _identifier(binding_id, "binding_id")
    elif binding_id is not None:
        raise _error("decision_not_allowed", "Only confirmed decisions create a binding.")
    return {
        "decision_id": decision_id, "case_id": case_id, "decision": decision,
        "decision_reason": reason, "verification_scope": verification_scope,
        "registration_id": registration_id, "candidate_rank": candidate_rank,
        "expected_case_revision": expected_case_revision, "binding_id": binding_id,
    }


def require_decision_allowed(principal: Principal, *, decision: str,
                             gate_status: str | None, verification_scope: str) -> None:
    if type(principal) is not Principal:
        raise _error("permission_denied", "A trusted principal is required.")
    require_any_role(principal, {"operator", "expert"})
    if type(decision) is not str or decision not in _DECISIONS:
        raise _error("decision_not_allowed", "Unknown decision action.")
    if type(verification_scope) is not str or verification_scope not in _SCOPES:
        raise _error("decision_not_allowed", "Unknown verification scope.")
    if verification_scope == "expert_pose" and "expert" not in principal.roles:
        raise _error("decision_not_allowed", "Expert pose requires an expert.")
    if decision == "confirmed" and not (
        gate_status == "passed" or (gate_status == "review_required" and "expert" in principal.roles)
    ):
        raise _error("decision_not_allowed", "Registration cannot be confirmed by this principal.")


def build_match_decision(*, request: dict, context: dict, operation: dict,
                         principal: Principal, previous_decision_id: str | None) -> dict:
    request = normalize_decision_request(**request)
    identity = {key: context[key] for key in (
        "asset_id", "source_id", "instance_id", "object_fingerprint", "retrieval_run_id"
    )}
    if (compute_case_id(**identity) != request["case_id"]
            or context["case_id"] != request["case_id"]
            or compute_case_revision(context["object_fingerprint"], context["evidence_fingerprint"],
                                     context["decision_head_fingerprint"], context["binding_head_fingerprint"])
            != request["expected_case_revision"]
            or previous_decision_id != context["decision_head_id"]):
        raise _error("decision_conflict", "Decision does not match its authoritative context.")
    if previous_decision_id is not None:
        _identifier(previous_decision_id, "previous_decision_id")
    if (previous_decision_id is None) != (context["decision_head_fingerprint"] is None):
        raise _error("artifact_integrity_failed", "Decision predecessor is incomplete.")
    registration = context["registrations_by_id"].get(request["registration_id"])
    if request["decision"] != "no_match" and (
        registration is None or registration["candidate_rank"] != request["candidate_rank"]
    ):
        raise _error("registration_not_eligible", "Registration does not match the chosen candidate.")
    require_decision_allowed(principal, decision=request["decision"],
                             gate_status=None if registration is None else registration["gate_status"],
                             verification_scope=request["verification_scope"])
    if (operation["actor_id"] != principal.actor_id or operation["roles"] != sorted(principal.roles)
            or operation["principal_source"] != principal.source):
        raise _error("permission_denied", "Decision principal differs from the audit owner.")
    _identifier(operation["operation_id"], "operation_id")
    return {
        "schema_version": "1.0", **identity,
        **{key: request[key] for key in (
            "decision_id", "case_id", "decision", "decision_reason", "verification_scope",
            "registration_id", "candidate_rank", "expected_case_revision",
        )},
        "evidence_fingerprint": context["evidence_fingerprint"],
        "decided_by": principal.actor_id, "decider_roles": sorted(principal.roles),
        "decided_at": operation["started_event_at"],
        "previous_decision_id": previous_decision_id,
        "previous_decision_head_fingerprint": context["decision_head_fingerprint"],
        "expected_decision_head_fingerprint": context["decision_head_fingerprint"],
        "expected_binding_head_fingerprint": context["binding_head_fingerprint"],
        "operation_id": operation["operation_id"],
    }
