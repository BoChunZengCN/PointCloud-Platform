from pc_system.job_runner import mark_step_status


def apply_quality_gate_to_job(job: dict, gate: dict, step_id: str = "quality_gate") -> dict:
    """把质量门禁状态同步到 job step，blocked 会阻塞后续生产。"""

    status = gate.get("status", "missing")
    if status == "blocked":
        return mark_step_status(job, step_id, "blocked", message="Quality gate blocked this job step.")
    if status == "review_required":
        return mark_step_status(job, step_id, "blocked", message="Quality gate requires review before continuing.")
    if status == "passed":
        return mark_step_status(job, step_id, "completed", message="Quality gate passed.")
    return mark_step_status(job, step_id, "blocked", message="Quality gate report is missing.")
