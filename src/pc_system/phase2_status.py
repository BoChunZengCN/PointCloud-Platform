from pathlib import Path

from pc_system.json_io import write_json


PHASE2_MODULES = [
    {"id": "P2-M1", "name": "FLS ingest adapter", "status": "completed"},
    {"id": "P2-M2", "name": "Gaussian Splatting adapter", "status": "completed"},
    {"id": "P2-M3", "name": "Unified Phase 2 viewer", "status": "completed"},
    {"id": "P2-M4", "name": "Phase 2 status report", "status": "completed"},
]


def build_phase2_status_report() -> dict:
    """生成 Phase 2 模块完成度报告。"""

    return {
        "phase": "Phase 2",
        "route": "raw FLS plus Gaussian Splatting expansion",
        "status": "completed",
        "modules": PHASE2_MODULES,
        "external_dependencies": {
            "fls_converter": "Required for real FLS to LAS conversion.",
            "gaussian_splat_trainer": "Required for production 3DGS training.",
            "browser_viewer": "Current viewer is a lightweight manifest landing page.",
        },
    }


def _render_markdown(report: dict) -> str:
    """渲染 Phase 2 状态 Markdown。"""

    lines = [
        f"# {report['phase']} Module Status",
        "",
        f"Route: {report['route']}",
        f"Status: {report['status']}",
        "",
        "| ID | Module | Status |",
        "| --- | --- | --- |",
    ]
    for module in report["modules"]:
        lines.append(f"| {module['id']} | {module['name']} | {module['status']} |")
    lines.extend(["", "## External Dependencies", ""])
    for name, note in report["external_dependencies"].items():
        lines.append(f"- `{name}`: {note}")
    return "\n".join(lines) + "\n"


def write_phase2_status_report(report: dict, output_dir: Path) -> dict[str, Path]:
    """写出 Phase 2 状态 JSON 和 Markdown。"""

    json_path = write_json(report, output_dir / "phase2_status.json")
    markdown_path = output_dir / "phase2_status.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
