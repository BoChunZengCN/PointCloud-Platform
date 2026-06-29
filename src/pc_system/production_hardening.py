import json
from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


def _check(name: str, path: Path, required: bool = True) -> dict[str, Any]:
    """构造单项一致性检查结果。"""

    exists = path.exists()
    return {
        "name": name,
        "path": str(path),
        "required": required,
        "status": "ready" if exists or not required else "missing",
    }


def build_consistency_report(project_root: Path, asset_id: str) -> dict[str, Any]:
    """检查资产索引、生产计划、job 和事件日志是否形成一致链路。"""

    jobs_dir = project_root / "reports" / "jobs" / asset_id
    job_files = sorted(jobs_dir.glob("*.json")) if jobs_dir.exists() else []
    event_files = sorted(jobs_dir.glob("*.events.jsonl")) if jobs_dir.exists() else []
    checks = [
        _check("asset_registry", project_root / "data" / "assets" / "asset_index.json"),
        _check("production_run_plan", project_root / "reports" / "production_runs" / asset_id / "production_run_plan.json"),
        {
            "name": "job_files",
            "path": str(jobs_dir),
            "required": True,
            "status": "ready" if job_files else "missing",
            "count": len(job_files),
        },
        {
            "name": "job_event_files",
            "path": str(jobs_dir),
            "required": True,
            "status": "ready" if event_files else "missing",
            "count": len(event_files),
        },
    ]
    return {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "ready": all(check["status"] == "ready" for check in checks),
        "checks": checks,
    }


def _markdown(report: dict[str, Any]) -> str:
    """生成一致性检查 Markdown 摘要。"""

    lines = [
        f"# Consistency Report: {report['asset_id']}",
        "",
        f"Ready: {report['ready']}",
        "",
        "| Check | Status | Path |",
        "| --- | --- | --- |",
    ]
    for check in report["checks"]:
        lines.append(f"| {check['name']} | {check['status']} | {check['path']} |")
    return "\n".join(lines) + "\n"


def write_consistency_report(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """写出一致性报告 JSON 和 Markdown。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(report, output_dir / "consistency_report.json")
    markdown_path = output_dir / "consistency_report.md"
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
