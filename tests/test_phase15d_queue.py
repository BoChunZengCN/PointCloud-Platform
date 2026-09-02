import pytest

from pc_system.model_decision_queue import list_model_decision_items, load_model_decision_item
from pc_system.model_match_decision import decide_model_match
from pc_system.model_matching_errors import ModelMatchingError
from phase15d_support import OPERATOR, EXPERT, AUDITOR, prepare_decision_case, publish_registration
from test_phase15d_decisions import arguments


def test_queue_is_automatic_and_role_cropped(tmp_path):
    case = prepare_decision_case(tmp_path)
    page = list_model_decision_items(tmp_path, principal=OPERATOR, status="pending")
    assert len(page["items"]) == 1
    item = load_model_decision_item(tmp_path, case_id=case.request_fields["case_id"], principal=OPERATOR)
    assert item["available_actions"] == ["confirm", "reject", "no_match"]
    assert "technical" not in item
    assert "rigid_transform_4x4" not in item["candidate_summary"][0]
    professional = load_model_decision_item(tmp_path, case_id=item["case_id"], principal=AUDITOR)
    assert professional["available_actions"] == []
    assert professional["technical"]["registrations"][0]["rigid_transform_4x4"][0][3] == 1


def test_confirm_processed_then_new_evidence_reopens_without_operator_confirm(tmp_path):
    case = prepare_decision_case(tmp_path)
    decide_model_match(tmp_path, **arguments(case))
    assert list_model_decision_items(tmp_path, principal=OPERATOR, status="pending")["items"] == []
    assert list_model_decision_items(tmp_path, principal=OPERATOR, status="processed")["items"][0]["status"] == "processed"
    publish_registration(tmp_path, sequence=2)
    item = load_model_decision_item(tmp_path, case_id=case.request_fields["case_id"], principal=OPERATOR)
    assert item["status"] == "pending"
    assert "confirm" not in item["available_actions"]
    assert "supersede" in load_model_decision_item(tmp_path, case_id=item["case_id"], principal=EXPERT)["available_actions"]


def test_candidate_rejection_keeps_pending_and_no_match_closes(tmp_path):
    case = prepare_decision_case(tmp_path)
    decide_model_match(tmp_path, **arguments(case, decision="rejected", binding_id=None))
    item = load_model_decision_item(tmp_path, case_id=case.request_fields["case_id"], principal=OPERATOR)
    assert item["status"] == "pending"
    assert item["available_actions"] == ["no_match"]
    decide_model_match(tmp_path, **arguments(case, 2, decision="no_match", registration_id=None, candidate_rank=None,
                                            binding_id=None, expected_case_revision=item["case_revision"]))
    assert load_model_decision_item(tmp_path, case_id=item["case_id"], principal=OPERATOR)["status"] == "processed"


@pytest.mark.parametrize("limit", [0, 101, True, 1.0])
def test_invalid_pagination_limits_rejected(tmp_path, limit):
    with pytest.raises(ModelMatchingError):
        list_model_decision_items(tmp_path, principal=OPERATOR, limit=limit)


def test_all_algorithm_rejections_offer_only_no_match_and_expert_rerun(tmp_path):
    case = prepare_decision_case(tmp_path, mode="rejected")
    item = load_model_decision_item(tmp_path, case_id=case.request_fields["case_id"], principal=OPERATOR)
    assert item["available_actions"] == ["no_match"]
    expert = load_model_decision_item(tmp_path, case_id=item["case_id"], principal=EXPERT)
    assert expert["available_actions"] == ["no_match", "rerun"]


def test_current_object_case_can_replace_a_stale_binding(tmp_path, monkeypatch):
    import pc_system.model_decision_queue as queue
    from pc_system.model_match_decision import load_decision_context

    case = prepare_decision_case(tmp_path)
    decide_model_match(tmp_path, **arguments(case))
    context = load_decision_context(tmp_path, **case.identity)
    report = dict(next(iter(context["registrations_by_id"].values())), object_fingerprint="f" * 64)
    context = dict(context, object_fingerprint="f" * 64)
    monkeypatch.setattr(queue, "load_decision_context", lambda *args, **kwargs: context)
    identity = {key: report[key] for key in queue._IDENTITY}
    item = queue._project(tmp_path, identity, [report], EXPERT)
    assert item["status"] == "stale"
    assert item["available_actions"] == ["rerun", "supersede"]
    assert queue._project(tmp_path, identity, [report], OPERATOR)["available_actions"] == []


@pytest.mark.parametrize("cursor", ["not-base64", "W10=", "e30=", "////"])
def test_invalid_cursor_is_a_stable_domain_error(tmp_path, cursor):
    with pytest.raises(ModelMatchingError) as failure:
        list_model_decision_items(tmp_path, principal=OPERATOR, cursor=cursor)
    assert failure.value.code == "decision_not_allowed"


def test_cursor_orders_equal_timestamps_and_is_bound_to_filters(tmp_path, monkeypatch):
    import pc_system.model_decision_queue as queue

    reports = [dict(asset_id="asset", source_id="source", instance_id=f"object-{n}",
                    retrieval_run_id="run", object_fingerprint=str(n) * 64) for n in (1, 2, 3)]
    monkeypatch.setattr(queue, "_registered_reports", lambda *args: reports)
    monkeypatch.setattr(queue, "_project", lambda root, identity, reports, principal: dict(
        case_id=queue.compute_case_id(**identity), status="pending", object={"class_id": "pump"},
        candidate_summary=[{"gate_status": "passed"}], updated_at="2026-09-01T00:00:00Z"))
    first = list_model_decision_items(tmp_path, principal=OPERATOR, limit=2)
    second = list_model_decision_items(tmp_path, principal=OPERATOR, limit=2, cursor=first["next_cursor"])
    ids = [item["case_id"] for item in first["items"] + second["items"]]
    assert ids == sorted(set(ids)) and len(ids) == 3
    assert first["counts"] == dict(pending=3, processed=0, stale=0, all=3)
    assert second["next_cursor"] is None
    with pytest.raises(ModelMatchingError):
        list_model_decision_items(tmp_path, principal=OPERATOR, status="pending", cursor=first["next_cursor"])
