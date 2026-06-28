import json
import sys

from pc_system.cli_parser import build_parser
from pc_system.commands.phase1 import (
    run_demo_phase1,
    run_execute_rule_segment,
    run_execute_slice,
    run_ingest,
    run_init,
    run_module_status,
    run_plan_rule_segment,
    run_plan_slice,
    run_publish_potree,
    run_report_rule_segment,
)
from pc_system.commands.phase2 import (
    run_execute_fls_ingest,
    run_execute_gaussian_splat,
    run_phase2_status,
    run_plan_fls_ingest,
    run_plan_gaussian_splat,
    run_publish_phase2_viewer,
)
from pc_system.commands.phase4 import run_create_production_job, run_update_job_step
from pc_system.commands.phase3 import (
    run_check_deployment_package,
    run_export_delivery_package,
    run_index_assets,
    run_phase3_tool_check,
    run_plan_production_run,
    run_report_production_run,
)
from pc_system.fls_ingest import FlsRunner, subprocess_runner as fls_subprocess_runner
from pc_system.gaussian_splatting import GaussianRunner, subprocess_runner as gaussian_subprocess_runner
from pc_system.las_reader import read_las_info
from pc_system.open3d_rule_segmentation_adapter import Open3DRunner, subprocess_runner as open3d_subprocess_runner
from pc_system.pdal_slice_adapter import PdalRunner, subprocess_runner as pdal_subprocess_runner
from pc_system.potree_publisher import PotreeRunner, subprocess_runner
from pc_system.slice_executor import SliceAdapter, placeholder_slice_adapter


def main(
    argv: list[str] | None = None,
    las_info_reader=read_las_info,
    slice_adapter: SliceAdapter = placeholder_slice_adapter,
    potree_runner: PotreeRunner = subprocess_runner,
    pdal_runner: PdalRunner = pdal_subprocess_runner,
    open3d_runner: Open3DRunner = open3d_subprocess_runner,
    fls_runner: FlsRunner = fls_subprocess_runner,
    gaussian_runner: GaussianRunner = gaussian_subprocess_runner,
) -> int:
    """CLI 入口，返回进程退出码。

    参数解析、阶段命令实现已经拆到独立模块；这里只负责分发和统一错误处理。
    """

    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return run_init(args.project_root)
        if args.command == "demo-phase1":
            return run_demo_phase1(args.project_root, args.las_path)
        if args.command == "ingest":
            return run_ingest(args.project_root, args.las_path, las_info_reader)
        if args.command == "plan-slice":
            return run_plan_slice(
                args.project_root,
                args.asset_id,
                args.name,
                args.min_bounds,
                args.max_bounds,
                args.voxel_size,
                args.output_format,
            )
        if args.command == "execute-slice":
            return run_execute_slice(
                args.project_root,
                args.asset_id,
                args.slice_name,
                slice_adapter,
                args.engine,
                args.pdal_path,
                pdal_runner,
            )
        if args.command == "publish-potree":
            return run_publish_potree(args.project_root, args.asset_id, args.converter_path, potree_runner)
        if args.command == "plan-rule-segment":
            return run_plan_rule_segment(args.project_root, args.asset_id, args.slice_name, args.name, args.methods)
        if args.command == "execute-rule-segment":
            return run_execute_rule_segment(
                args.project_root,
                args.asset_id,
                args.slice_name,
                args.name,
                args.engine,
                args.python_path,
                args.script_path,
                open3d_runner,
            )
        if args.command == "report-rule-segment":
            return run_report_rule_segment(args.project_root, args.asset_id, args.slice_name, args.name)
        if args.command == "module-status":
            return run_module_status(args.project_root)
        if args.command == "plan-fls-ingest":
            return run_plan_fls_ingest(args.project_root, args.asset_id, args.raw_files, args.output_las, args.registration)
        if args.command == "execute-fls-ingest":
            return run_execute_fls_ingest(args.project_root, args.asset_id, args.converter_path, fls_runner)
        if args.command == "plan-gaussian-splat":
            return run_plan_gaussian_splat(args.project_root, args.asset_id, args.name, args.source_las, args.iterations)
        if args.command == "execute-gaussian-splat":
            return run_execute_gaussian_splat(
                args.project_root,
                args.asset_id,
                args.name,
                args.trainer_path,
                args.python_path,
                gaussian_runner,
            )
        if args.command == "publish-phase2-viewer":
            return run_publish_phase2_viewer(args.project_root, args.asset_id, args.potree_path, args.splat_path, args.report)
        if args.command == "phase2-status":
            return run_phase2_status(args.project_root)
        if args.command == "phase3-tool-check":
            return run_phase3_tool_check(
                args.project_root,
                args.fls_converter,
                args.pdal_path,
                args.potree_converter,
                args.gaussian_trainer,
                args.open3d_script,
            )
        if args.command == "plan-production-run":
            return run_plan_production_run(
                args.project_root,
                args.asset_id,
                args.slice_name,
                args.segment_name,
                args.splat_name,
            )
        if args.command == "report-production-run":
            return run_report_production_run(args.project_root, args.asset_id)
        if args.command == "index-assets":
            return run_index_assets(args.project_root)
        if args.command == "check-deployment-package":
            return run_check_deployment_package(args.project_root, args.asset_id)
        if args.command == "export-delivery-package":
            return run_export_delivery_package(args.project_root, args.asset_id, args.make_zip)
        if args.command == "create-production-job":
            return run_create_production_job(args.project_root, args.asset_id, args.job_id)
        if args.command == "update-job-step":
            return run_update_job_step(
                args.project_root,
                args.asset_id,
                args.job_id,
                args.step_id,
                args.status,
                args.message,
            )
        raise ValueError(f"Unsupported command: {args.command}")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

