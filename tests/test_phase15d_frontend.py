import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_business_page_has_complete_chinese_controls():
    html = (ROOT / "frontend/model-decisions.html").read_text(encoding="utf-8")
    for marker in ('data-status="pending"', 'data-status="processed"', 'data-status="all"',
                   'id="filters"', 'id="decision-list"', 'id="candidate"', 'data-testid="decision-reason"',
                   'data-testid="case-status"', 'data-testid="binding-id"', 'id="message"',
                   'data-action="confirm"', 'data-action="reject"', 'data-action="no_match"', '刷新'):
        assert marker in html
    assert 'href="model-decisions.html"' in (ROOT / "frontend/index.html").read_text(encoding="utf-8")


def test_shared_actions_never_expand_server_permissions_and_payload_is_frozen():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the shared-module behavior probe")
    path = ROOT / "frontend/model-matching-workbench.js"
    script = r'''
const m = require(process.argv[1]);
const item = {case_id:"case",case_revision:"revision",status:"pending",available_actions:["confirm","rerun"],
 candidate_summary:[{registration_id:"r",candidate_rank:1,gate_status:"passed",available_actions:["confirm"]}]};
const p = m.buildDecisionPayload(item,{decision:"confirmed",decision_reason:"核验",verification_scope:"identity",
 registration_id:"r",decision_id:"d",binding_id:"b",operation_id:"o",request_id:"q",idempotency_key:"i"});
console.log(JSON.stringify({operator:m.availableActions(item,"operator"),auditor:m.availableActions(item,"auditor"),
 empty:m.availableActions({...item,available_actions:[]},"expert"),label:m.statusLabel("processed"),payload:p}));
'''
    result = subprocess.run([node, "-e", script, str(path)], capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    assert value["operator"] == ["confirm"] and value["auditor"] == [] and value["empty"] == []
    assert value["payload"]["expected_case_revision"] == "revision" and value["label"] == "已处理"
    assert "principal" not in value["payload"]


def test_professional_page_has_evidence_and_version_controls():
    html = (ROOT / "frontend/model-matching-lab.html").read_text(encoding="utf-8")
    for marker in ('id="retrieval-evidence"', 'id="registration-config"', 'id="engine"', 'id="matrix"',
                   'id="metrics"', 'id="gate-reasons"', 'id="decision-history"', 'id="binding-history"',
                   'id="audit"', 'data-action="rerun"', 'data-action="supersede"', 'data-action="restore"',
                   '将创建新版本，不修改旧绑定', '只读'):
        assert marker in html


def test_shared_matrix_validation_rejects_nonfinite_or_wrong_shape():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for matrix rendering validation")
    result = subprocess.run([node, "-e", '''const m=require(process.argv[1]);
const good=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]];
console.log(JSON.stringify([m.validMatrix(good),m.validMatrix([[1]]),m.validMatrix(good.map(r=>r.map(v=>v?Infinity:v)))]));''',
        str(ROOT / "frontend/model-matching-workbench.js")], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [True, False, False]
