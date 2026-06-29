import json
from datetime import datetime, timezone
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
                "attempt_count": 0,
                "last_error": "",
                "updated_at": "",
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
            previous_status = step.get("status", "planned")
            if status == "running" and previous_status in {"failed", "blocked"}:
                step["attempt_count"] = int(step.get("attempt_count", 0)) + 1
            step["status"] = status
            step["message"] = message
            if status == "failed":
                step["last_error"] = message
            step["updated_at"] = datetime.now(timezone.utc).isoformat()
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

def write_job_event(
    output_dir: Path,
    job_id: str,
    *,
    action: str,
    step_id: str = "",
    old_status: str = "",
    new_status: str = "",
    message: str = "",
    actor: str = "system",
) -> Path:
    """追加写入 job 审计事件 JSONL，便于追踪状态变化。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{job_id}.events.jsonl"
    event = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "action": action,
        "step_id": step_id,
        "old_status": old_status,
        "new_status": new_status,
        "message": message,
        "actor": actor,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def read_job_events(output_dir: Path, job_id: str) -> list[dict]:
    """读取 job 审计事件；事件文件缺失时返回空列表。"""

    path = output_dir / f"{job_id}.events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def execute_job_steps(job: dict, output_dir: Path, actor: str = "executor") -> dict:
    """轻量执行适配器：按顺序模拟完成未完成 step，并写入审计事件。

    当前函数不执行 step.command，避免在 API/测试环境运行任意外部命令。
    后续真实执行器可以复用这个状态推进和事件记录边界。
    """

    for step in job.get("steps", []):
        if step.get("status") == "completed":
            continue
        old_status = step.get("status", "planned")
        mark_step_status(job, step["step_id"], "running", message="executor started")
        write_job_event(
            output_dir,
            job["job_id"],
            action="step_status_updated",
            step_id=step["step_id"],
            old_status=old_status,
            new_status="running",
            message="executor started",
            actor=actor,
        )
        mark_step_status(job, step["step_id"], "completed", message="executor completed")
        write_job_event(
            output_dir,
            job["job_id"],
            action="step_status_updated",
            step_id=step["step_id"],
            old_status="running",
            new_status="completed",
            message="executor completed",
            actor=actor,
        )
    write_job(job, output_dir)
    write_job_event(output_dir, job["job_id"], action="job_completed", new_status=job["status"], actor=actor)
    return job

def enqueue_job(queue_dir: Path, *, asset_id: str, job_id: str, requested_by: str = "system") -> Path:
    """写入轻量本地队列记录，为后续替换异步队列保留接口。"""

    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / "job_queue.jsonl"
    entry = {
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "asset_id": asset_id,
        "job_id": job_id,
        "status": "queued",
        "requested_by": requested_by,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def list_job_queue(queue_dir: Path) -> list[dict]:
    """读取轻量本地队列记录；缺失时返回空列表。"""

    path = queue_dir / "job_queue.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

