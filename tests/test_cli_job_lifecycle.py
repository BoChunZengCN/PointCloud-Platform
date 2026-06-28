import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main
from pc_system.production_pipeline import build_production_run_plan


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def sample_plan() -> dict:
    return build_production_run_plan(
        {
            "asset_id": "scan",
            "file": {"path": "C:/data/scan.las", "name": "scan.las"},
            "las": {"bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}},
        }
    )


def test_cli_create_production_job_from_plan():
    project = case_dir("cli-create-job") / "workspace"
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())

    exit_code = main([
        "create-production-job",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--job-id",
        "job-scan-prod",
    ])

    job_path = project / "reports" / "jobs" / "scan" / "job-scan-prod.json"
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["job_id"] == "job-scan-prod"
    assert payload["asset_id"] == "scan"
    assert payload["status"] == "planned"
    assert payload["summary"]["planned"] == len(payload["steps"])


def test_cli_update_job_step_status_and_summary():
    project = case_dir("cli-update-job") / "workspace"
    write_json(project / "reports" / "production_runs" / "scan" / "production_run_plan.json", sample_plan())
    assert main([
        "create-production-job",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--job-id",
        "job-scan-prod",
    ]) == 0

    exit_code = main([
        "update-job-step",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--job-id",
        "job-scan-prod",
        "--step-id",
        "ingest",
        "--status",
        "completed",
        "--message",
        "LAS metadata ready",
    ])

    job_path = project / "reports" / "jobs" / "scan" / "job-scan-prod.json"
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["steps"][0]["status"] == "completed"
    assert payload["steps"][0]["message"] == "LAS metadata ready"
    assert payload["summary"]["completed"] == 1
    assert payload["status"] == "running"


def test_cli_create_production_job_returns_2_when_plan_missing():
    project = case_dir("cli-create-job-missing-plan") / "workspace"

    exit_code = main([
        "create-production-job",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
    ])

    assert exit_code == 2


def test_cli_update_job_step_returns_2_when_job_missing():
    project = case_dir("cli-update-job-missing") / "workspace"

    exit_code = main([
        "update-job-step",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--job-id",
        "job-missing",
        "--step-id",
        "ingest",
        "--status",
        "completed",
    ])

    assert exit_code == 2
