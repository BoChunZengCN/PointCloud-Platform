import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.delivery_package import export_delivery_package
from pc_system.job_runner import create_job_from_plan, write_job
from pc_system.phase11_batch import build_batch_run_plan
from pc_system.phase11_project_gate import build_project_gate, write_project_gate_report
from pc_system.phase11_quality_job_link import apply_quality_gate_to_job
from pc_system.phase11_report_center import build_report_center


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def registry(*asset_ids: str) -> dict:
    return {
        "schema_version": "1.0",
        "asset_count": len(asset_ids),
        "assets": [
            {
                "asset_id": asset_id,
                "metadata_path": f"data/assets/{asset_id}/asset.json",
                "report_paths": {"quality_report": f"reports/{asset_id}/quality_report.html"},
                "viewer_paths": {},
            }
            for asset_id in asset_ids
        ],
    }


def write_registry(project: Path, data: dict) -> None:
    path = project / "data" / "assets" / "asset_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_quality_gate(project: Path, asset_id: str, status: str) -> None:
    path = project / "reports" / "quality_gates" / asset_id / "quality_gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"asset_id": asset_id, "status": status, "severity": "critical" if status == "blocked" else "info", "finding_count": 0}),
        encoding="utf-8",
    )


def test_p11_m1_builds_project_level_gate_from_asset_gates():
    gate = build_project_gate(
        registry("scan-a", "scan-b", "scan-c"),
        {
            "scan-a": {"asset_id": "scan-a", "status": "passed"},
            "scan-b": {"asset_id": "scan-b", "status": "review_required"},
            "scan-c": {"asset_id": "scan-c", "status": "blocked"},
        },
    )

    assert gate["status"] == "blocked"
    assert gate["asset_count"] == 3
    assert gate["status_summary"] == {"passed": 1, "review_required": 1, "blocked": 1, "missing": 0}
    assert gate["assets"][2]["status"] == "blocked"


def test_p11_m2_cli_writes_project_gate_report():
    project = case_dir("p11-project-gate") / "workspace"
    write_registry(project, registry("scan-a", "scan-b"))
    write_quality_gate(project, "scan-a", "passed")
    write_quality_gate(project, "scan-b", "review_required")

    exit_code = main(["check-project-gate", "--project-root", str(project)])

    output = project / "reports" / "project_gate" / "project_gate.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["status"] == "review_required"
    assert payload["status_summary"]["review_required"] == 1


def test_p11_m3_delivery_manifest_records_gate_decision():
    project = case_dir("p11-delivery-audit") / "workspace"
    data = registry("scan")
    metadata = project / "data" / "assets" / "scan" / "asset.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("{}", encoding="utf-8")
    data["assets"][0]["metadata_path"] = "data/assets/scan/asset.json"

    outputs = export_delivery_package(
        project,
        data,
        "scan",
        project / "delivery" / "scan",
        gate_decision={"allowed": True, "reason": "passed", "message": "ok"},
    )

    manifest = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert manifest["delivery_gate_decision"]["reason"] == "passed"


def test_p11_m4_quality_gate_can_block_job_step():
    plan = {
        "asset_id": "scan",
        "steps": [
            {"step_id": "quality_gate", "phase": "Phase 8", "name": "Quality Gate"},
            {"step_id": "delivery", "phase": "Phase 9", "name": "Delivery"},
        ],
    }
    job = create_job_from_plan(plan, job_id="job-scan")

    updated = apply_quality_gate_to_job(job, {"status": "blocked"}, step_id="quality_gate")

    assert updated["status"] == "blocked"
    assert updated["steps"][0]["status"] == "blocked"
    assert "Quality gate blocked" in updated["steps"][0]["message"]


def test_p11_m5_builds_batch_run_plan_for_multiple_assets():
    plan = build_batch_run_plan(["scan-a", "scan-b"], operations=["analyze", "quality_gate", "segment", "delivery_gate"])

    assert plan["asset_count"] == 2
    assert len(plan["steps"]) == 8
    assert plan["steps"][0]["command"][:2] == ["analyze-asset", "--asset-id"]
    assert plan["steps"][-1]["operation"] == "delivery_gate"


def test_p11_m6_api_exposes_project_gate_and_report_center():
    project = case_dir("p11-api") / "workspace"
    write_registry(project, registry("scan"))
    write_quality_gate(project, "scan", "passed")
    write_project_gate_report(build_project_gate(registry("scan"), {"scan": {"status": "passed"}}), project / "reports" / "project_gate")
    report_path = project / "reports" / "scan" / "quality_report.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("<html></html>", encoding="utf-8")

    client = TestClient(create_app(project))
    gate_response = client.get("/project-gate")
    reports_response = client.get("/reports/center")

    assert gate_response.status_code == 200
    assert gate_response.json()["status"] == "passed"
    assert reports_response.status_code == 200
    assert reports_response.json()["report_count"] >= 1


def test_p11_m6_frontend_has_report_center_contract():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "project-gate-status" in html
    assert "report-center-panel" in html
    assert "fetchProjectGate" in script
    assert "fetchReportCenter" in script
    assert "/project-gate" in script
    assert "/reports/center" in script
    assert ".report-center-panel" in css


def test_p11_docs_describe_project_workflow_modules():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    doc_path = ROOT / "docs" / "phase11-project-workflow.md"

    assert "P11-M1" in readme
    assert "check-project-gate" in readme
    assert doc_path.exists()
    doc = doc_path.read_text(encoding="utf-8")
    assert "P11-M6" in doc
    assert "report center" in doc
