import json
from pathlib import Path

from pc_system.json_io import write_json


JOB_STATUSES = ["planned", "running", "completed", "failed", "blocked"]


def _summarize_steps(steps: list[dict]) -> dict:
    """汇总 job 中每个步骤的状态数量。"""

    summary = {status: 0 for status in JOB_STATUSES}
    for step in steps:
        status = step.get("status", "planned")
        if status in summary:
            summary[status] += 1
    summary["total"] = len(steps)
    return summary


def _job_status(summary: dict) -> str:
    """根据步骤摘要推导整个 job 的状态。"""

    if summary["failed"]:
        return "failed"
    if summary["blocked"]:
        return "blocked"
    if summary["completed"] == summary["total"] and summary["total"] > 0:
        return "completed"
    if summary["completed"] or summary["running"]:
        return "running"
    return "planned"


def create_job_from_plan(plan: dict, job_id: str | None = None) -> dict:
    """从生产运行计划创建本地 job 状态。"""

    resolved_job_id = job_id or f"job-{plan['asset_id']}"
    steps = []
    for index, step in enumerate(plan["steps"], start=1):
        steps.append(
            {
                "order": index,
                "step_id": step["step_id"],
                "phase": step["phase"],
                "name": step["name"],
                "status": step.get("status", "planned"),
                "command": step.get("command", []),
                "outputs": step.get("outputs", []),
                "message": "",
            }
        )
    summary = _summarize_steps(steps)
    return {
        "schema_version": "1.0",
        "job_id": resolved_job_id,
        "asset_id": plan["asset_id"],
        "status": _job_status(summary),
        "summary": summary,
        "steps": steps,
    }


def mark_step_status(job: dict, step_id: str, status: str, message: str = "") -> dict:
    """更新单个 job step 的状态并重新汇总。"""

    if status not in JOB_STATUSES:
        raise ValueError(f"Unsupported job step status: {status}")
    matched = False
    for step in job["steps"]:
        if step["step_id"] == step_id:
            step["status"] = status
            step["message"] = message
            matched = True
            break
    if not matched:
        raise KeyError(f"Job step not found: {step_id}")
    summary = _summarize_steps(job["steps"])
    job["summary"] = summary
    job["status"] = _job_status(summary)
    return job


def write_job(job: dict, output_dir: Path) -> Path:
    """写出 job 状态 JSON。"""

    return write_json(job, output_dir / f"{job['job_id']}.json")


def load_job(path: Path) -> dict:
    """读取 job 状态 JSON。"""

    return json.loads(path.read_text(encoding="utf-8"))
