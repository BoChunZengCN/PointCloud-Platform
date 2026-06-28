import json
import sys
from pathlib import Path

from pc_system.config import ProjectConfig
from pc_system.job_runner import JOB_STATUSES, create_job_from_plan, load_job, mark_step_status, write_job


def _job_dir(project_root: Path, asset_id: str) -> Path:
    """返回资产 job 状态目录。"""

    return ProjectConfig(project_root=project_root).paths()["reports"] / "jobs" / asset_id


def _plan_path(project_root: Path, asset_id: str) -> Path:
    """返回 Phase 3 生产运行计划路径。"""

    return ProjectConfig(project_root=project_root).paths()["reports"] / "production_runs" / asset_id / "production_run_plan.json"


def run_create_production_job(project_root: Path, asset_id: str, job_id: str | None) -> int:
    """从生产运行计划创建本地 job 状态文件。"""

    ProjectConfig(project_root=project_root).ensure_directories()
    plan_path = _plan_path(project_root, asset_id)
    if not plan_path.exists():
        print(f"Production run plan not found: {plan_path}", file=sys.stderr)
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    job = create_job_from_plan(plan, job_id=job_id)
    write_job(job, _job_dir(project_root, asset_id))
    return 0


def run_update_job_step(
    project_root: Path,
    asset_id: str,
    job_id: str,
    step_id: str,
    status: str,
    message: str,
) -> int:
    """更新 job step 状态，并重新写回 job JSON。"""

    if status not in JOB_STATUSES:
        print(f"Unsupported job step status: {status}", file=sys.stderr)
        return 2
    job_path = _job_dir(project_root, asset_id) / f"{job_id}.json"
    if not job_path.exists():
        print(f"Job not found: {job_path}", file=sys.stderr)
        return 2
    job = load_job(job_path)
    try:
        updated = mark_step_status(job, step_id, status, message=message)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    write_job(updated, job_path.parent)
    return 0
