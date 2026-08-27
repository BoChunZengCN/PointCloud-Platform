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
from pc_system.commands.phase5 import run_check_consistency, run_serve_api
from pc_system.commands.phase6 import run_analyze_asset, run_analyze_point_cloud
from pc_system.commands.phase8 import run_check_quality_gate
from pc_system.commands.phase10 import run_segment_asset_objects, run_segment_objects
from pc_system.commands.phase11 import run_check_project_gate, run_plan_batch_run
from pc_system.commands.phase13 import run_phase13_segmentation
from pc_system.commands.phase13b import (
    run_compare_segmentation,
    run_evaluate_segmentation,
    run_import_segmentation_benchmark,
    run_search_segmentation,
)
from pc_system.commands.phase14 import (
    run_apply_segmentation_correction,
    run_create_segmentation_correction,
    run_publish_segmentation_correction,
    run_retry_segmentation_publication,
    run_submit_segmentation_correction,
)
from pc_system.commands.phase15 import run_create_model_asset, run_import_model
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
from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError
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
        for field in (
            "asset_id",
            "benchmark_id",
            "candidate_evaluation_id",
            "comparison_id",
            "evaluation_id",
            "baseline_evaluation_id",
            "job_id",
            "run_id",
            "session_id",
            "release_id",
            "client_request_id",
            "baseline_release_id",
            "sample_id",
            "search_id",
            "step_id",
            "name",
            "slice_name",
            "segment_name",
            "splat_name",
        ):
            value = getattr(args, field, None)
            if value:
                validate_identifier(value, field)
        # Phase 15 mutations must reach their domain operations before ID
        # validation so rejected requests receive their required audit record.
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
                args.potree_converter,
                args.pdal_path,
                args.python_path,
                args.open3d_script,
            )
        if args.command == "report-production-run":
            return run_report_production_run(args.project_root, args.asset_id)
        if args.command == "index-assets":
            return run_index_assets(args.project_root)
        if args.command == "check-deployment-package":
            return run_check_deployment_package(args.project_root, args.asset_id)
        if args.command == "export-delivery-package":
            return run_export_delivery_package(args.project_root, args.asset_id, args.make_zip, args.allow_review_required)
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
        if args.command == "serve-api":
            return run_serve_api(args.project_root, args.host, args.port, args.api_key, args.mode, dry_run=args.dry_run)
        if args.command == "check-consistency":
            return run_check_consistency(args.project_root, args.asset_id)
        if args.command == "analyze-point-cloud":
            return run_analyze_point_cloud(args.project_root, args.asset_id, args.points_json, args.grid_cell_size)
        if args.command == "analyze-asset":
            return run_analyze_asset(args.project_root, args.asset_id, args.max_points, args.grid_cell_size)
        if args.command == "check-quality-gate":
            return run_check_quality_gate(args.project_root, args.asset_id)
        if args.command == "segment-objects":
            return run_segment_objects(args.project_root, args.asset_id, args.points_json, args.distance_threshold, args.min_points)
        if args.command == "segment-asset-objects":
            return run_segment_asset_objects(args.project_root, args.asset_id, args.distance_threshold, args.min_points, args.max_points, args.engine, args.config)
        if args.command == "check-project-gate":
            return run_check_project_gate(args.project_root)
        if args.command == "plan-batch-run":
            return run_plan_batch_run(args.project_root, args.operations)
        if args.command == "run-segmentation":
            return run_phase13_segmentation(
                args.project_root,
                args.asset_id,
                args.run_id,
                args.engine,
                args.allow_fallback,
                args.distance_threshold,
                args.min_points,
                args.voxel_size,
                args.max_points,
            )
        if args.command == "import-segmentation-benchmark":
            return run_import_segmentation_benchmark(
                args.project_root, args.manifest
            )
        if args.command == "evaluate-segmentation-run":
            return run_evaluate_segmentation(
                args.project_root,
                asset_id=args.asset_id,
                run_id=args.run_id,
                benchmark_id=args.benchmark_id,
                sample_id=args.sample_id,
                evaluation_id=args.evaluation_id,
                config_path=args.config,
            )
        if args.command == "compare-segmentation-runs":
            return run_compare_segmentation(
                args.project_root,
                asset_id=args.asset_id,
                comparison_id=args.comparison_id,
                baseline_evaluation_id=args.baseline_evaluation_id,
                candidate_evaluation_id=args.candidate_evaluation_id,
                thresholds_path=args.thresholds,
            )
        if args.command == "search-segmentation-params":
            return run_search_segmentation(
                args.project_root,
                asset_id=args.asset_id,
                benchmark_id=args.benchmark_id,
                sample_id=args.sample_id,
                search_id=args.search_id,
                config_path=args.config,
                baseline_evaluation_id=args.baseline_evaluation_id,
            )
        if args.command == "create-segmentation-correction":
            return run_create_segmentation_correction(
                args.project_root,
                asset_id=args.asset_id,
                run_id=args.run_id,
                session_id=args.session_id,
                sample_id=args.sample_id,
                actor=args.actor,
                benchmark_id=args.benchmark_id,
                baseline_release_id=args.baseline_release_id,
            )
        if args.command == "apply-segmentation-correction":
            return run_apply_segmentation_correction(
                args.project_root,
                asset_id=args.asset_id,
                session_id=args.session_id,
                actor=args.actor,
                expected_revision=args.expected_revision,
                client_request_id=args.client_request_id,
                operation_path=args.operation,
            )
        if args.command == "submit-segmentation-correction":
            return run_submit_segmentation_correction(
                args.project_root,
                asset_id=args.asset_id,
                session_id=args.session_id,
                actor=args.actor,
                expected_revision=args.expected_revision,
            )
        if args.command == "publish-segmentation-correction":
            return run_publish_segmentation_correction(
                args.project_root,
                asset_id=args.asset_id,
                session_id=args.session_id,
                publication_path=args.publication,
            )
        if args.command == "retry-segmentation-publication":
            return run_retry_segmentation_publication(
                args.project_root,
                asset_id=args.asset_id,
                release_id=args.release_id,
                actor=args.actor,
            )
        if args.command == "create-model-asset":
            return run_create_model_asset(
                args.project_root,
                model_id=args.model_id,
                display_name=args.display_name,
                category_id=args.category_id,
                manufacturer=args.manufacturer,
                model_number=args.model_number,
                keywords=args.keyword,
                tags=args.tag,
                actor=args.actor,
                operation_id=args.operation_id,
                request_id=args.request_id,
                idempotency_key=args.idempotency_key,
            )
        if args.command == "import-model":
            return run_import_model(
                args.project_root,
                model_id=args.model_id,
                version_id=args.version_id,
                source_path=args.source,
                declared_unit=args.unit,
                license_name=args.license,
                provenance_path=args.provenance,
                actor=args.actor,
                operation_id=args.operation_id,
                request_id=args.request_id,
                idempotency_key=args.idempotency_key,
            )
        raise ValueError(f"Unsupported command: {args.command}")
    except ModelMatchingError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())





