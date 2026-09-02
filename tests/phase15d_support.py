"""Phase 15D 的可信主体和确定性配准夹具。"""

from types import SimpleNamespace
from pc_system.model_matching_identity import Principal
from pc_system.model_registration import register_model_candidate
from phase15c_support import DeterministicRegistrationEngine, prepare_phase15c_case


OPERATOR = Principal("operator-a", frozenset({"operator"}), "cli")
EXPERT = Principal("expert-a", frozenset({"expert"}), "cli")
AUDITOR = Principal("auditor-a", frozenset({"auditor"}), "cli")


def publish_registration(project_root, *, sequence=1, mode="passed"):
    prepared = prepare_phase15c_case(project_root)
    return register_model_candidate(
        project_root,
        registration_id=f"registration-{sequence}",
        candidate_rank=1,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(mode),
        principal=EXPERT,
        operation_id=f"op-registration-{sequence}",
        request_id=f"req-registration-{sequence}",
        idempotency_key=f"idem-registration-{sequence}",
        **prepared,
    )


def prepare_decision_case(project_root, *, mode="passed"):
    from pc_system.model_match_decision import load_decision_context

    report = publish_registration(project_root, mode=mode)
    identity = {key: report[key] for key in ("asset_id", "source_id", "instance_id", "retrieval_run_id")}
    context = load_decision_context(project_root, **identity)
    return SimpleNamespace(identity=identity, request_fields={
        "case_id": context["case_id"], "registration_id": report["registration_id"],
        "candidate_rank": report["candidate_rank"], "expected_case_revision": context["case_revision"],
    })
