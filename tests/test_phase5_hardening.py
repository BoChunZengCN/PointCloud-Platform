import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.commands.phase5 import build_api_environment, run_serve_api
from pc_system.production_hardening import build_consistency_report, write_consistency_report


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def sample_plan(asset_id: str = "scan") -> dict:
    return {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "steps": [
            {"step_id": "ingest", "phase": "phase1", "name": "Ingest", "status": "planned", "command": [], "outputs": []}
        ],
    }


def test_p5_m1_write_routes_require_api_key_when_configured():
    project = case_dir("p5-api-key") / "workspace"
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())
    client = TestClient(create_app(project, api_key="secret", run_mode="production"))

    blocked = client.post("/runs/scan/jobs", json={"job_id": "job-scan"})
    allowed = client.post("/runs/scan/jobs", json={"job_id": "job-scan"}, headers={"x-api-key": "secret"})

    assert blocked.status_code == 401
    assert allowed.status_code == 201


def test_p5_m2_health_reports_run_mode_and_write_protection():
    project = case_dir("p5-run-mode") / "workspace"
    client = TestClient(create_app(project, api_key="secret", run_mode="production"))

    payload = client.get("/health").json()

    assert payload["run_mode"] == "production"
    assert payload["write_protection"] == "api_key"
    assert payload["cors_origins"] == []


def test_p5_m3_serve_api_command_builds_environment_and_invokes_runner():
    project = case_dir("p5-serve") / "workspace"
    calls = []

    def fake_runner(app_path: str, host: str, port: int) -> None:
        calls.append({"app_path": app_path, "host": host, "port": port})

    exit_code = run_serve_api(project, "127.0.0.1", 8765, "secret", "production", fake_runner)
    env = build_api_environment(project, "secret", "production")

    assert exit_code == 0
    assert calls == [{"app_path": "pc_system.api:app", "host": "127.0.0.1", "port": 8765}]
    assert env["PC_SYSTEM_PROJECT_ROOT"] == str(project)
    assert env["PC_SYSTEM_API_KEY"] == "secret"
    assert env["PC_SYSTEM_RUN_MODE"] == "production"


def test_p5_m3_cli_registers_serve_api_command():
    project = case_dir("p5-cli-serve") / "workspace"

    exit_code = main(["serve-api", "--project-root", str(project), "--dry-run"])

    assert exit_code == 0


def test_p5_m4_frontend_displays_api_connection_and_write_protection_status():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "api-health-status" in html
    assert "fetchApiHealth" in script
    assert "renderApiHealthStatus" in script
    assert "write_protection" in script
    assert "x-api-key" in script
    assert ".api-health-status" in css


def test_p5_m5_builds_consistency_report_for_workspace_outputs():
    project = case_dir("p5-consistency") / "workspace"
    write_json(project / "data" / "assets" / "asset_index.json", {"assets": [{"asset_id": "scan"}]})
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())
    write_json(project / "reports" / "jobs" / "scan" / "job-scan.json", {"job_id": "job-scan", "asset_id": "scan"})
    (project / "reports" / "jobs" / "scan" / "job-scan.events.jsonl").write_text("{}\n", encoding="utf-8")

    report = build_consistency_report(project, "scan")
    outputs = write_consistency_report(report, project / "reports" / "consistency" / "scan")

    assert report["asset_id"] == "scan"
    assert report["ready"] is True
    assert all(check["status"] == "ready" for check in report["checks"])
    assert outputs["json"].name == "consistency_report.json"
    assert outputs["markdown"].name == "consistency_report.md"


def test_p5_m5_cli_writes_consistency_report():
    project = case_dir("p5-cli-consistency") / "workspace"
    write_json(project / "data" / "assets" / "asset_index.json", {"assets": [{"asset_id": "scan"}]})
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())
    write_json(project / "reports" / "jobs" / "scan" / "job-scan.json", {"job_id": "job-scan", "asset_id": "scan"})
    (project / "reports" / "jobs" / "scan" / "job-scan.events.jsonl").write_text("{}\n", encoding="utf-8")

    exit_code = main(["check-consistency", "--project-root", str(project), "--asset-id", "scan"])

    assert exit_code == 0
    assert (project / "reports" / "consistency" / "scan" / "consistency_report.json").exists()


def test_p5_m6_documents_production_hardening_modules():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    doc = (ROOT / "docs" / "phase5-production-hardening.md").read_text(encoding="utf-8")

    assert "P5-M1" in readme
    assert "serve-api" in readme
    assert "check-consistency" in readme
    assert "PC_SYSTEM_API_KEY" in doc
    assert "P5-M6" in doc
