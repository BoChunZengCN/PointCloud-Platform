from pathlib import Path
import json
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_has_golden_evaluation_panel_and_fetchers():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "app.css").read_text(encoding="utf-8")

    assert 'id="golden-evaluation-panel"' in html
    assert 'id="golden-evaluation-summary"' in html
    assert "fetchGoldenEvaluations" in script
    assert "fetchSegmentationSearches" in script
    assert "fetchSegmentationComparison" in script
    assert "renderGoldenEvaluation" in script
    assert ".golden-evaluation-panel" in css


def test_frontend_distinguishes_accuracy_from_operational_proxy():
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    for label in (
        "黄金标注准确率",
        "实例 F1",
        "包围盒 IoU",
        "回归门禁",
        "推荐参数",
        "标注覆盖率",
    ):
        assert label in script
    assert "运行质量代理指标" in script


def test_frontend_view_model_uses_real_gate_artifact_and_label_coverage():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend behavior verification.")
    module_path = ROOT / "frontend" / "golden-evaluation.js"
    evaluation_payload = {
        "evaluations": [
            {
                "evaluation_id": "eval-001",
                "status": "completed",
                "summary": {
                    "instance_f1": 0.8,
                    "point_miou": 0.7,
                    "mean_box_iou": 0.6,
                    "matched_label_ratio": 0.75,
                },
            }
        ]
    }
    search_payload = {
        "searches": [
            {
                "recommendation": {
                    "status": "recommended",
                    "comparison_id": "cmp-001",
                    "gate_status": "passed",
                    "score": 0.9,
                    "config": {"min_points": 2},
                }
            }
        ]
    }
    script = (
        f"const m=require({json.dumps(str(module_path))});"
        f"const e={json.dumps(evaluation_payload)};"
        f"const s={json.dumps(search_payload)};"
        "const missing=m.buildGoldenEvaluationViewModel(e,s,null);"
        "const failed=m.buildGoldenEvaluationViewModel(e,s,{gate:{status:'failed'}});"
        "console.log(JSON.stringify({missing,failed}));"
    )

    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["missing"]["gateStatus"] == "读取失败"
    assert payload["failed"]["gateStatus"] == "failed"
    assert payload["failed"]["matchedLabelRatio"] == 0.75


def test_phase13b_documentation_describes_formats_metrics_and_search_limits():
    document = (
        ROOT / "docs" / "phase13b-golden-segmentation-evaluation.md"
    ).read_text(encoding="utf-8")

    for term in (
        "JSONL",
        "source_fingerprint",
        "coordinate_tolerance",
        "point_miou",
        "instance_f1",
        "mean_box_iou",
        "regression_gate.json",
        "max_trials",
        "trial_timeout_seconds",
        "advisory",
        "Phase 14",
        "Phase 15",
    ):
        assert term in document


def test_inventory_and_readme_mark_phase13b_complete():
    inventory = (
        ROOT / "docs" / "system-function-module-inventory.md"
    ).read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Phase 13B" in inventory
    assert "P13B-M6" in inventory
    assert "Phase 13B" in readme
    assert "search-segmentation-params" in readme
