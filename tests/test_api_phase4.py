import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from pc_system.api import create_app


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_api_exposes_runs_reports_and_deployment_resources():
    project = case_dir("api-runs") / "workspace"
    write_json(project / "data" / "assets" / "asset_index.json", {"schema_version": "1.0", "asset_count": 0, "assets": []})
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", {"asset_id": "scan", "steps": []})
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_report.json", {"asset_id": "scan", "status": "not_started"})
    write_json(project / "reports" / "deployment" / "scan" / "deployment_checklist.json", {"asset_id": "scan", "ready": True})
    write_json(project / "reports" / "jobs" / "scan" / "job-scan.json", {"job_id": "job-scan", "asset_id": "scan", "status": "planned"})

    client = TestClient(create_app(project))

    assert client.get("/runs/scan/plan").json()["asset_id"] == "scan"
    assert client.get("/runs/scan/report").json()["status"] == "not_started"
    assert client.get("/runs/scan/jobs").json()["jobs"][0]["job_id"] == "job-scan"
    assert client.get("/reports/scan").json()["production_report"].endswith("production_run_report.json")
    assert client.get("/deployment/scan").json()["ready"] is True


def test_api_reports_delivery_output_status_and_file_kinds():
    project = case_dir("api-delivery-status") / "workspace"
    write_json(
        project / "data" / "assets" / "asset_index.json",
        {
            "schema_version": "1.0",
            "asset_count": 1,
            "assets": [
                {
                    "asset_id": "scan",
                    "viewer_paths": {
                        "viewer_url": "previews/scan/phase2_viewer.html",
                        "manifest_path": "previews/scan/phase2_viewer_manifest.json",
                        "report_path": "reports/production_runs/scan/production_run_report.md",
                    },
                    "report_paths": {"quality_report": "reports/scan/quality_report.html"},
                }
            ],
        },
    )
    (project / "previews" / "scan").mkdir(parents=True)
    (project / "previews" / "scan" / "phase2_viewer.html").write_text("<html></html>", encoding="utf-8")
    (project / "previews" / "scan" / "phase2_viewer_manifest.json").write_text("{}", encoding="utf-8")

    client = TestClient(create_app(project))
    response = client.get("/delivery/scan/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_id"] == "scan"
    assert payload["outputs"]["viewer_url"]["exists"] is True
    assert payload["outputs"]["viewer_url"]["kind"] == "html"
    assert payload["outputs"]["manifest_path"]["kind"] == "manifest"
    assert payload["outputs"]["report_path"]["exists"] is False
