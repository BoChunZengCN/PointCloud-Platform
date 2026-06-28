import json
from pathlib import Path
from uuid import uuid4

from pc_system.job_runner import create_job_from_plan, load_job, mark_step_status, write_job
from pc_system.production_pipeline import build_production_run_plan


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def sample_plan() -> dict:
    return build_production_run_plan(
        {
            "asset_id": "scan",
            "file": {"path": "C:/data/scan.las", "name": "scan.las"},
            "las": {"bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}},
        }
    )


def test_create_job_from_plan_initializes_step_state():
    job = create_job_from_plan(sample_plan(), job_id="job-scan")

    assert job["job_id"] == "job-scan"
    assert job["asset_id"] == "scan"
    assert job["status"] == "planned"
    assert job["summary"]["planned"] == len(job["steps"])
    assert job["steps"][0]["step_id"] == "ingest"


def test_mark_step_status_updates_summary_and_job_status():
    job = create_job_from_plan(sample_plan(), job_id="job-scan")

    updated = mark_step_status(job, "ingest", "completed", message="done")

    assert updated["steps"][0]["status"] == "completed"
    assert updated["steps"][0]["message"] == "done"
    assert updated["summary"]["completed"] == 1
    assert updated["status"] == "running"


def test_write_and_load_job_roundtrip():
    output_dir = case_dir("job-runner")
    job = create_job_from_plan(sample_plan(), job_id="job-scan")

    path = write_job(job, output_dir)
    loaded = load_job(path)

    assert path.name == "job-scan.json"
    assert loaded["job_id"] == "job-scan"
