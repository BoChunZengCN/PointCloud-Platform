from pathlib import Path

from pc_system.json_io import write_json


MODULES = [
    {
        "id": "M1",
        "name": "Project skeleton",
        "status": "completed",
        "outputs": ["standard project directories", "init command"],
    },
    {
        "id": "M2",
        "name": "LAS asset metadata",
        "status": "completed",
        "outputs": ["asset.json", "optional laspy reader"],
    },
    {
        "id": "M3",
        "name": "QA report",
        "status": "completed",
        "outputs": ["quality_report.json", "quality_report.html"],
    },
    {
        "id": "M4",
        "name": "Preview manifest / Potree publisher",
        "status": "completed",
        "outputs": ["preview_manifest.json", "index.html", "potree_manifest.json"],
    },
    {
        "id": "M5",
        "name": "Slice planning / execution",
        "status": "completed",
        "outputs": ["slice_plan.json", "placeholder executor", "PDAL adapter boundary"],
    },
    {
        "id": "M6",
        "name": "Rule segmentation / summary report",
        "status": "completed",
        "outputs": [
            "rule_segmentation_plan.json",
            "labels JSON",
            "Open3D script adapter boundary",
            "reference segmentation script",
            "segmentation_summary.json",
            "segmentation_summary.html",
        ],
    },
]


EXTERNAL_DEPENDENCIES = {
    "laspy": "Required only for real LAS/LAZ metadata ingest.",
    "pdal": "Required only for real slice execution with --engine pdal.",
    "open3d": "Optional for future richer point-cloud segmentation scripts; current reference script has JSON/ASCII PLY support.",
    "PotreeConverter": "Required only for browser-scale Potree publishing.",
}


def build_module_status_report() -> dict:
    """生成 Phase 1 模块完成度报告。

    该报告用于把 WBS 状态固化为机器可读结构，便于后续继续由 CLI 或 agent 调度。
    """

    return {
        "phase": "Phase 1",
        "route": "processed LAS/LAZ first",
        "status": "completed",
        "modules": MODULES,
        "external_dependencies": EXTERNAL_DEPENDENCIES,
        "next_steps": [],
    }


def _render_markdown(report: dict) -> str:
    """把模块状态报告渲染成人能阅读的 Markdown。"""

    lines = [
        f"# {report['phase']} Module Status",
        "",
        f"Route: {report['route']}",
        f"Status: {report['status']}",
        "",
        "| ID | Module | Status | Outputs |",
        "| --- | --- | --- | --- |",
    ]
    for module in report["modules"]:
        outputs = ", ".join(module["outputs"])
        lines.append(f"| {module['id']} | {module['name']} | {module['status']} | {outputs} |")
    lines.extend(["", "## External Dependencies", ""])
    for name, note in report["external_dependencies"].items():
        lines.append(f"- `{name}`: {note}")
    lines.extend(["", "## Next Steps", ""])
    if report["next_steps"]:
        for item in report["next_steps"]:
            lines.append(f"- {item}")
    else:
        lines.append("- None for Phase 1. Move to Phase 2 planning when ready.")
    return "\n".join(lines) + "\n"


def write_module_status_report(report: dict, output_dir: Path) -> dict[str, Path]:
    """写出模块状态 JSON 和 Markdown 文件。"""

    json_path = write_json(report, output_dir / "module_status.json")
    markdown_path = output_dir / "module_status.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
