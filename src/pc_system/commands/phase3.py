import json
import sys
from pathlib import Path

from pc_system.asset_registry import build_asset_registry, discover_asset_metadata, write_asset_registry
from pc_system.config import ProjectConfig
from pc_system.delivery_gate import evaluate_delivery_gate
from pc_system.delivery_package import export_delivery_package
from pc_system.deployment_checklist import build_deployment_checklist, write_deployment_checklist
from pc_system.phase3_tool_check import ToolSpec, build_tool_check_report, write_tool_check_report
from pc_system.production_pipeline import build_production_run_plan, write_production_run_plan
from pc_system.production_run_report import build_production_run_report, write_production_run_report


def run_phase3_tool_check(
    project_root: Path,
    fls_converter: Path | None,
    pdal_path: Path | None,
    potree_converter: Path | None,
    gaussian_trainer: Path | None,
    open3d_script: Path | None,
) -> int:
    """检查 Phase 3 生产外部工具路径，并写出报告。"""

    specs = [
        ToolSpec("fls_converter", fls_converter, fls_converter is not None),
        ToolSpec("pdal", pdal_path, pdal_path is not None),
        ToolSpec("potree_converter", potree_converter, potree_converter is not None),
        ToolSpec("3dgs_trainer", gaussian_trainer, gaussian_trainer is not None),
        ToolSpec("open3d_script", open3d_script, open3d_script is not None),
    ]
    report = build_tool_check_report(specs)
    output_dir = ProjectConfig(project_root=project_root).ensure_directories()["reports"]
    write_tool_check_report(report, output_dir)
    return 0


def run_plan_production_run(
    project_root: Path,
    asset_id: str,
    slice_name: str,
    segment_name: str,
    splat_name: str,
) -> int:
    """读取 asset.json 并写出 P3-M2 生产运行计划。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    metadata_path = paths["assets"] / asset_id / "asset.json"
    if not metadata_path.exists():
        print(f"Asset metadata not found: {metadata_path}", file=sys.stderr)
        return 2
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    plan = build_production_run_plan(
        metadata,
        slice_name=slice_name,
        segment_name=segment_name,
        splat_name=splat_name,
    )
    output_dir = paths["reports"] / "production_runs" / asset_id
    write_production_run_plan(plan, output_dir)
    return 0


def run_report_production_run(project_root: Path, asset_id: str) -> int:
    """读取 P3-M2 计划并写出 P3-M3 运行报告。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    output_dir = paths["reports"] / "production_runs" / asset_id
    plan_path = output_dir / "production_run_plan.json"
    if not plan_path.exists():
        print(f"Production run plan not found: {plan_path}", file=sys.stderr)
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    report = build_production_run_report(plan)
    write_production_run_report(report, output_dir)
    return 0



def run_index_assets(project_root: Path) -> int:
    """扫描项目资产目录并写出 asset_index。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    assets = discover_asset_metadata(paths["assets"])
    registry = build_asset_registry(assets, project_root=project_root)
    write_asset_registry(registry, paths["assets"])
    return 0



def run_check_deployment_package(project_root: Path, asset_id: str) -> int:
    """检查 Phase 3 交付包是否具备关键产物。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    checklist = build_deployment_checklist(project_root, asset_id)
    output_dir = paths["reports"] / "deployment" / asset_id
    write_deployment_checklist(checklist, output_dir)
    return 0



def run_export_delivery_package(project_root: Path, asset_id: str, make_zip: bool = False, allow_review_required: bool = False) -> int:
    """导出资产交付包，并写出交付 manifest。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    registry_path = paths["assets"] / "asset_index.json"
    if not registry_path.exists():
        print(f"Asset registry not found: {registry_path}", file=sys.stderr)
        return 2
    gate_path = paths["reports"] / "quality_gates" / asset_id / "quality_gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.exists() else None
    decision = evaluate_delivery_gate(gate, allow_review_required=allow_review_required)
    if not decision["allowed"]:
        print(decision["message"], file=sys.stderr)
        return 2
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    export_delivery_package(project_root, registry, asset_id, project_root / "delivery" / asset_id, make_zip=make_zip)
    return 0



