import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from pc_system.api import create_app


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path



def sample_plan(asset_id: str = "scan") -> dict:
    return {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "steps": [
            {
                "step_id": "ingest",
                "phase": "phase1",
                "name": "Ingest asset",
                "status": "planned",
                "command": ["pc-system", "ingest"],
                "outputs": ["asset.json"],
            },
            {
                "step_id": "publish_viewer",
                "phase": "phase2",
                "name": "Publish viewer",
                "status": "planned",
                "command": ["pc-system", "publish-phase2-viewer"],
                "outputs": ["phase2_viewer.html"],
            },
        ],
    }

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



def test_api_job_list_includes_read_only_summary_for_frontend():
    project = case_dir("api-job-summary") / "workspace"
    write_json(
        project / "reports" / "jobs" / "scan" / "job-a.json",
        {
            "job_id": "job-a",
            "asset_id": "scan",
            "status": "completed",
            "summary": {"planned": 0, "running": 0, "completed": 2, "failed": 0, "blocked": 0, "total": 2},
        },
    )
    write_json(
        project / "reports" / "jobs" / "scan" / "job-b.json",
        {
            "job_id": "job-b",
            "asset_id": "scan",
            "status": "running",
            "summary": {"planned": 1, "running": 1, "completed": 1, "failed": 0, "blocked": 0, "total": 3},
        },
    )

    client = TestClient(create_app(project))
    payload = client.get("/runs/scan/jobs").json()

    assert payload["asset_id"] == "scan"
    assert payload["job_count"] == 2
    assert payload["latest_job"]["job_id"] == "job-b"
    assert payload["status_summary"] == {"completed": 1, "running": 1}

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

def test_api_can_create_production_job_from_plan():
    project = case_dir("api-create-job") / "workspace"
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())

    client = TestClient(create_app(project))
    response = client.post("/runs/scan/jobs", json={"job_id": "job-scan-prod"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["job_id"] == "job-scan-prod"
    assert payload["asset_id"] == "scan"
    assert payload["summary"]["planned"] == 2
    assert (project / "reports" / "jobs" / "scan" / "job-scan-prod.json").exists()


def test_api_can_update_production_job_step_status():
    project = case_dir("api-update-job-step") / "workspace"
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())

    client = TestClient(create_app(project))
    client.post("/runs/scan/jobs", json={"job_id": "job-scan-prod"})
    response = client.patch(
        "/runs/scan/jobs/job-scan-prod/steps/ingest",
        json={"status": "completed", "message": "LAS metadata ready"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["summary"]["completed"] == 1
    assert payload["steps"][0]["status"] == "completed"
    assert payload["steps"][0]["message"] == "LAS metadata ready"

    saved = json.loads((project / "reports" / "jobs" / "scan" / "job-scan-prod.json").read_text(encoding="utf-8"))
    assert saved["steps"][0]["status"] == "completed"


def test_api_job_write_routes_return_clear_errors():
    project = case_dir("api-job-write-errors") / "workspace"
    client = TestClient(create_app(project))

    missing_plan = client.post("/runs/scan/jobs", json={"job_id": "job-scan-prod"})
    missing_job = client.patch(
        "/runs/scan/jobs/job-missing/steps/ingest",
        json={"status": "completed", "message": "done"},
    )

    assert missing_plan.status_code == 404
    assert "Production run plan not found" in missing_plan.json()["detail"]
    assert missing_job.status_code == 404
    assert "Job not found" in missing_job.json()["detail"]

def test_api_job_writes_audit_events_for_create_and_update():
    project = case_dir("api-job-events") / "workspace"
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())

    client = TestClient(create_app(project))
    client.post("/runs/scan/jobs", json={"job_id": "job-scan-prod"})
    client.patch(
        "/runs/scan/jobs/job-scan-prod/steps/ingest",
        json={"status": "completed", "message": "LAS metadata ready"},
    )

    events_path = project / "reports" / "jobs" / "scan" / "job-scan-prod.events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]

    assert [event["action"] for event in events] == ["job_created", "step_status_updated"]
    assert events[0]["actor"] == "api"
    assert events[1]["step_id"] == "ingest"
    assert events[1]["old_status"] == "planned"
    assert events[1]["new_status"] == "completed"

def test_api_can_read_single_job_with_events():
    project = case_dir("api-job-detail") / "workspace"
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())

    client = TestClient(create_app(project))
    client.post("/runs/scan/jobs", json={"job_id": "job-scan-prod"})
    client.patch(
        "/runs/scan/jobs/job-scan-prod/steps/ingest",
        json={"status": "running", "message": "started"},
    )
    response = client.get("/runs/scan/jobs/job-scan-prod")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["job_id"] == "job-scan-prod"
    assert [event["action"] for event in payload["events"]] == ["job_created", "step_status_updated"]


def test_api_single_job_returns_404_when_missing():
    project = case_dir("api-job-detail-missing") / "workspace"
    client = TestClient(create_app(project))

    response = client.get("/runs/scan/jobs/job-missing")

    assert response.status_code == 404
    assert "Job not found" in response.json()["detail"]

