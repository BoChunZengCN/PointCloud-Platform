"""从分割纠正、检索、配准到人工绑定的真实领域闭环。"""

import json

import pytest

from pc_system.model_decision_queue import load_model_decision_item, list_model_decision_items
from pc_system.model_decision_queue import load_model_bindings
import pc_system.model_match_decision as decision_module
from pc_system.model_match_decision import decide_model_match
from pc_system.model_matching_audit import read_verified_operation_snapshot
from pc_system.model_matching_errors import ModelMatchingError
from phase15d_support import OPERATOR, prepare_decision_case
from test_phase15d_decisions import arguments


def test_phase15d_e2e_confirm_and_replay(tmp_path):
    case = prepare_decision_case(tmp_path)
    request = arguments(case)
    first = decide_model_match(tmp_path, **request)
    assert decide_model_match(tmp_path, **request) == first
    item = load_model_decision_item(tmp_path, case_id=case.request_fields["case_id"], principal=OPERATOR)
    assert item["status"] == "processed"
    snapshot = read_verified_operation_snapshot(tmp_path, request["operation_id"])
    assert snapshot["operation"]["status"] == "completed"
    assert snapshot["operation"]["result"]["result_fingerprint"] == first["commit"]["result_fingerprint"]
    assert {e["event_type"] for e in snapshot["events"]} >= {"match.decision_confirmed", "model_binding.created"}


def test_phase15d_queue_fails_closed_when_registration_bytes_are_tampered(tmp_path):
    prepare_decision_case(tmp_path)
    path = next((tmp_path / "reports/model_registrations").glob("*/*/*/*/registration_report.json"))
    original = path.read_bytes()
    report = json.loads(original)
    report["gate_status"] = "rejected"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as failure:
        list_model_decision_items(tmp_path, principal=OPERATOR)
    assert failure.value.code == "artifact_integrity_failed"


def test_changed_authoritative_object_snapshot_marks_existing_binding_stale(tmp_path, monkeypatch):
    case = prepare_decision_case(tmp_path)
    result = decide_model_match(tmp_path, **arguments(case))
    reload_object = decision_module._reload_retrieval_object
    def changed_snapshot(*args, **kwargs):
        return {**reload_object(*args, **kwargs), "object_fingerprint":"f" * 64}
    # 在上游权威对象读取边界模拟新快照，不篡改已发布历史工件。
    monkeypatch.setattr(decision_module, "_reload_retrieval_object", changed_snapshot)
    item = load_model_decision_item(tmp_path, case_id=case.request_fields["case_id"], principal=OPERATOR)
    assert item["status"] == "stale" and item["available_actions"] == []
    bindings = load_model_bindings(tmp_path, principal=OPERATOR,
        **{key:case.identity[key] for key in ("asset_id", "source_id", "instance_id")})
    assert bindings["current_status"] == "stale"
    assert bindings["current_binding"]["binding_id"] == result["binding"]["binding_id"]
