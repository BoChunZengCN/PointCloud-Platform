import json
from pathlib import Path
from uuid import uuid4

from pc_system.job_runner import create_job_from_plan, enqueue_job, execute_job_steps, list_job_queue, load_job, mark_step_status, read_job_events, write_job, write_job_event
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

def test_write_and_read_job_event_log_jsonl():
    output_dir = case_dir("job-events")

    event_path = write_job_event(
        output_dir,
        "job-scan",
        action="step_status_updated",
        step_id="ingest",
        old_status="planned",
        new_status="completed",
        message="done",
        actor="api",
    )
    events = read_job_events(output_dir, "job-scan")

    assert event_path.name == "job-scan.events.jsonl"
    assert len(events) == 1
    assert events[0]["action"] == "step_status_updated"
    assert events[0]["actor"] == "api"
    assert events[0]["old_status"] == "planned"
    assert events[0]["new_status"] == "completed"
    assert "created_at" in events[0]

def test_mark_step_status_tracks_retry_attempts_and_last_error():
    job = create_job_from_plan(sample_plan(), job_id="job-scan")

    failed = mark_step_status(job, "ingest", "failed", message="converter failed")
    retried = mark_step_status(failed, "ingest", "running", message="retrying")

    step = retried["steps"][0]
    assert step["attempt_count"] == 1
    assert step["last_error"] == "converter failed"
    assert step["message"] == "retrying"
    assert "updated_at" in step

def test_execute_job_steps_completes_pending_steps_with_events():
    output_dir = case_dir("job-executor")
    job = create_job_from_plan(sample_plan(), job_id="job-scan")

    executed = execute_job_steps(job, output_dir, actor="executor")
    events = read_job_events(output_dir, "job-scan")

    assert executed["status"] == "completed"
    assert executed["summary"]["completed"] == executed["summary"]["total"]
    assert all(step["status"] == "completed" for step in executed["steps"])
    assert events[-1]["action"] == "job_completed"
    assert any(event["action"] == "step_status_updated" for event in events)

def test_enqueue_and_list_job_queue_entries():
    output_dir = case_dir("job-queue")

    queue_path = enqueue_job(output_dir, asset_id="scan", job_id="job-scan", requested_by="api")
    entries = list_job_queue(output_dir)

    assert queue_path.name == "job_queue.jsonl"
    assert len(entries) == 1
    assert entries[0]["asset_id"] == "scan"
    assert entries[0]["job_id"] == "job-scan"
    assert entries[0]["status"] == "queued"
    assert entries[0]["requested_by"] == "api"

