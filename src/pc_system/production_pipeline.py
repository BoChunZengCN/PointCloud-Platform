from pathlib import Path

from pc_system.json_io import write_json


def _command(*parts: object) -> list[str]:
    """把命令片段统一转成字符串列表，便于 JSON 记录和后续执行器复用。"""

    return [str(part) for part in parts]


def _step(step_id: str, phase: str, name: str, command: list[str], outputs: list[str]) -> dict:
    """构造生产计划中的单个步骤。"""

    return {
        "step_id": step_id,
        "phase": phase,
        "name": name,
        "status": "planned",
        "command": command,
        "outputs": outputs,
    }


def build_production_run_plan(
    asset_metadata: dict,
    slice_name: str = "room-a",
    segment_name: str = "baseline",
    splat_name: str = "baseline",
    project_root: Path = Path("workspace"),
    potree_converter: Path = Path("PotreeConverter"),
    pdal_path: Path = Path("pdal"),
    python_path: Path = Path("python"),
    open3d_script: Path = Path("scripts/open3d_rule_segment.py"),
) -> dict:
    """生成 P3-M2 生产运行计划，不执行任何重型外部工具。

    计划只串联已有 CLI 命令和预期输出，让真实生产前可以先审查步骤顺序。
    """

    asset_id = asset_metadata["asset_id"]
    source_path = asset_metadata["file"]["path"]
    bounds = asset_metadata.get("las", {}).get("bounds", {})
    min_bounds = bounds.get("min", [0, 0, 0])
    max_bounds = bounds.get("max", [0, 0, 0])
    root_args = ("--project-root", project_root)

    steps = [
        _step(
            "ingest",
            "Phase 1",
            "读取 LAS/LAZ 资产元数据",
            _command("pc-system", "ingest", *root_args, "--las-path", source_path),
            [f"data/assets/{asset_id}/asset.json"],
        ),
        _step(
            "qa_preview",
            "Phase 1",
            "生成 QA 报告与基础预览入口",
            _command("pc-system", "ingest", *root_args, "--las-path", source_path),
            [f"reports/{asset_id}/quality_report.json", f"previews/{asset_id}/preview_manifest.json"],
        ),
        _step(
            "potree_publish",
            "Phase 1",
            "发布 Potree 点云预览目录",
            _command("pc-system", "publish-potree", *root_args, "--asset-id", asset_id, "--converter-path", potree_converter),
            [f"previews/{asset_id}/potree_manifest.json", f"previews/{asset_id}/potree/metadata.json"],
        ),
        _step(
            "slice_plan",
            "Phase 1",
            "创建空间切片计划",
            _command(
                "pc-system",
                "plan-slice",
                *root_args,
                "--asset-id",
                asset_id,
                "--name",
                slice_name,
                "--min",
                *min_bounds,
                "--max",
                *max_bounds,
            ),
            [f"data/assets/{asset_id}/slices/{slice_name}/slice_plan.json"],
        ),
        _step(
            "slice_execute",
            "Phase 1",
            "执行空间切片计划",
            _command("pc-system", "execute-slice", *root_args, "--asset-id", asset_id, "--slice-name", slice_name, "--engine", "pdal", "--pdal-path", pdal_path),
            [f"data/assets/{asset_id}/slices/{slice_name}/{asset_id}-{slice_name}.las"],
        ),
        _step(
            "rule_segment_plan",
            "Phase 1",
            "创建规则分割计划",
            _command("pc-system", "plan-rule-segment", *root_args, "--asset-id", asset_id, "--slice-name", slice_name, "--name", segment_name),
            [f"data/assets/{asset_id}/slices/{slice_name}/segments/{segment_name}/rule_segmentation_plan.json"],
        ),
        _step(
            "rule_segment_execute",
            "Phase 1",
            "执行规则分割计划",
            _command("pc-system", "execute-rule-segment", *root_args, "--asset-id", asset_id, "--slice-name", slice_name, "--name", segment_name, "--engine", "open3d", "--python-path", python_path, "--script-path", open3d_script),
            [f"data/assets/{asset_id}/slices/{slice_name}/segments/{segment_name}/{asset_id}-{slice_name}-{segment_name}-labels.json"],
        ),
        _step(
            "rule_segment_report",
            "Phase 1",
            "生成分割汇总报告",
            _command("pc-system", "report-rule-segment", *root_args, "--asset-id", asset_id, "--slice-name", slice_name, "--name", segment_name),
            [f"reports/{asset_id}/segments/{slice_name}/{segment_name}/segmentation_summary.json"],
        ),
        _step(
            "gaussian_splat_plan",
            "Phase 2",
            "创建 Gaussian Splatting 训练计划",
            _command("pc-system", "plan-gaussian-splat", *root_args, "--asset-id", asset_id, "--name", splat_name, "--source-las", source_path),
            [f"previews/{asset_id}/splats/{splat_name}/gaussian_splat_plan.json"],
        ),
        _step(
            "phase2_viewer",
            "Phase 2",
            "发布统一查看器入口",
            _command("pc-system", "publish-phase2-viewer", *root_args, "--asset-id", asset_id),
            [f"previews/{asset_id}/phase2_viewer_manifest.json"],
        ),
        _step(
            "phase3_tool_check",
            "Phase 3",
            "检查生产外部工具路径",
            _command("pc-system", "phase3-tool-check", *root_args, "--pdal-path", pdal_path, "--potree-converter", potree_converter, "--open3d-script", open3d_script),
            ["reports/phase3_tool_check.json"],
        ),
    ]

    return {
        "phase": "Phase 3",
        "module": "P3-M2 Production Pipeline Plan",
        "asset_id": asset_id,
        "status": "planned",
        "parameters": {
            "slice_name": slice_name,
            "segment_name": segment_name,
            "splat_name": splat_name,
            "project_root": str(project_root),
            "potree_converter": str(potree_converter),
            "pdal_path": str(pdal_path),
            "python_path": str(python_path),
            "open3d_script": str(open3d_script),
        },
        "steps": steps,
    }


def _render_markdown(plan: dict) -> str:
    """把生产运行计划渲染为 Markdown 审查表。"""

    lines = [
        "# Production Run Plan",
        "",
        f"Asset: {plan['asset_id']}",
        f"Status: {plan['status']}",
        "",
        "| Step | Phase | Status | Name |",
        "| --- | --- | --- | --- |",
    ]
    for step in plan["steps"]:
        lines.append(f"| {step['step_id']} | {step['phase']} | {step['status']} | {step['name']} |")
    return "\n".join(lines) + "\n"


def write_production_run_plan(plan: dict, output_dir: Path) -> dict[str, Path]:
    """写出生产运行计划 JSON 和 Markdown。"""

    json_path = write_json(plan, output_dir / "production_run_plan.json")
    markdown_path = output_dir / "production_run_plan.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_markdown(plan), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
