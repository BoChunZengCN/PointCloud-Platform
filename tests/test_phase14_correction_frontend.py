import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_correction_workbench_has_simple_primary_controls():
    html = (ROOT / "frontend" / "correction.html").read_text(encoding="utf-8")
    for marker in (
        'id="correction-canvas"',
        'id="review-queue"',
        'id="object-panel"',
        'data-action="confirm"',
        'data-action="merge"',
        'data-action="split"',
        'data-action="relabel"',
        'data-action="noise"',
        'data-action="undo"',
        'data-action="redo"',
        'data-action="restore"',
    ):
        assert marker in html
    assert "系统建议" in html
    assert "人工已确认" in html
    assert "<details" in html


def test_dashboard_links_to_correction_workbench():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'href="correction.html"' in html
    assert "分割纠正" in html


def test_workbench_uses_session_editor_or_explicit_actor_for_writes():
    script = (ROOT / "frontend" / "correction.js").read_text(encoding="utf-8")

    assert 'params.get("actor")' in script
    assert "state.session?.active_editor" in script


def test_projection_selection_and_context_operations_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend behavior verification.")
    module_path = ROOT / "frontend" / "segmentation-correction.js"
    script = (
        f"const m=require({json.dumps(str(module_path))});"
        "const p=m.projectPoint({source_point_index:7,x:1,y:2,z:3},"
        "{view:'top',zoom:10,panX:0,panY:0},{width:100,height:100});"
        "const selected=m.pickIndices([p],[[50,50],[70,50],[70,80],[50,80]]);"
        "const op=m.buildOperation('split',[7],{instanceId:'obj-1'});"
        "console.log(JSON.stringify({p,selected,op}));"
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["p"]["source_point_index"] == 7
    assert payload["selected"] == [7]
    assert payload["op"] == {
        "type": "split",
        "instance_id": "obj-1",
        "source_point_indices": [7],
    }


def test_view_model_keeps_suggestions_distinct_from_confirmation():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend behavior verification.")
    module_path = ROOT / "frontend" / "segmentation-correction.js"
    script = (
        f"const m=require({json.dumps(str(module_path))});"
        "console.log(JSON.stringify(m.buildCorrectionViewModel("
        "{status:'draft',revision:2,correction_diff:{changed_point_count:3}},"
        "{items:[{instance_id:'obj-1',suggested_action:'merge',confirmed:false}]},"
        "{objects:[{instance_id:'obj-1',review_state:'unreviewed'}]})));"
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["suggestions"][0]["confirmed"] is False
    assert payload["objects"][0]["review_state"] == "unreviewed"
    assert payload["changedPointCount"] == 3
