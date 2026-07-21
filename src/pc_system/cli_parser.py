import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """定义命令行参数注册。"""

    parser = argparse.ArgumentParser(prog="pc-system")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create the standard project directories.")
    init.add_argument("--project-root", required=True, type=Path)

    demo = subparsers.add_parser("demo-phase1", help="Run the Phase 1 workflow with demo LAS metadata.")
    demo.add_argument("--project-root", required=True, type=Path)
    demo.add_argument("--las-path", required=True, type=Path)

    ingest = subparsers.add_parser("ingest", help="Read real LAS/LAZ metadata and run the Phase 1 workflow.")
    ingest.add_argument("--project-root", required=True, type=Path)
    ingest.add_argument("--las-path", required=True, type=Path)

    # M5：只生成切片计划，不直接裁剪点云。
    plan_slice = subparsers.add_parser("plan-slice", help="Create an M5 slice plan from asset metadata.")
    plan_slice.add_argument("--project-root", required=True, type=Path)
    plan_slice.add_argument("--asset-id", required=True)
    plan_slice.add_argument("--name", required=True)
    plan_slice.add_argument("--min", required=True, type=float, nargs=3, dest="min_bounds")
    plan_slice.add_argument("--max", required=True, type=float, nargs=3, dest="max_bounds")
    plan_slice.add_argument("--voxel-size", type=float)
    plan_slice.add_argument("--output-format", default="las", choices=["las", "laz", "ply"])

    execute_slice = subparsers.add_parser("execute-slice", help="Execute an existing M5 slice plan.")
    execute_slice.add_argument("--project-root", required=True, type=Path)
    execute_slice.add_argument("--asset-id", required=True)
    execute_slice.add_argument("--slice-name", required=True)
    execute_slice.add_argument("--engine", default="placeholder", choices=["placeholder", "pdal"])
    execute_slice.add_argument("--pdal-path", default=Path("pdal"), type=Path)

    publish_potree_parser = subparsers.add_parser("publish-potree", help="Publish an asset with PotreeConverter.")
    publish_potree_parser.add_argument("--project-root", required=True, type=Path)
    publish_potree_parser.add_argument("--asset-id", required=True)
    publish_potree_parser.add_argument("--converter-path", required=True, type=Path)

    plan_rule = subparsers.add_parser("plan-rule-segment", help="Create an M6 rule segmentation plan.")
    plan_rule.add_argument("--project-root", required=True, type=Path)
    plan_rule.add_argument("--asset-id", required=True)
    plan_rule.add_argument("--slice-name", required=True)
    plan_rule.add_argument("--name", required=True)
    plan_rule.add_argument(
        "--methods",
        nargs="+",
        default=["ground", "plane", "cluster", "noise"],
        choices=["ground", "plane", "cluster", "noise"],
    )

    execute_rule = subparsers.add_parser("execute-rule-segment", help="Execute an M6 rule segmentation plan.")
    execute_rule.add_argument("--project-root", required=True, type=Path)
    execute_rule.add_argument("--asset-id", required=True)
    execute_rule.add_argument("--slice-name", required=True)
    execute_rule.add_argument("--name", required=True)
    execute_rule.add_argument("--engine", default="placeholder", choices=["placeholder", "open3d"])
    execute_rule.add_argument("--python-path", default=Path("python"), type=Path)
    execute_rule.add_argument("--script-path", default=Path("open3d_rule_segment.py"), type=Path)

    report_rule = subparsers.add_parser("report-rule-segment", help="Create an M6 rule segmentation summary report.")
    report_rule.add_argument("--project-root", required=True, type=Path)
    report_rule.add_argument("--asset-id", required=True)
    report_rule.add_argument("--slice-name", required=True)
    report_rule.add_argument("--name", required=True)

    module_status = subparsers.add_parser("module-status", help="Write the Phase 1 module status report.")
    module_status.add_argument("--project-root", required=True, type=Path)

    plan_fls = subparsers.add_parser("plan-fls-ingest", help="Create a Phase 2 FLS ingest plan.")
    plan_fls.add_argument("--project-root", required=True, type=Path)
    plan_fls.add_argument("--asset-id", required=True)
    plan_fls.add_argument("--raw-files", required=True, nargs="+", type=Path)
    plan_fls.add_argument("--output-las", required=True, type=Path)
    plan_fls.add_argument("--registration", default="external")

    execute_fls = subparsers.add_parser("execute-fls-ingest", help="Execute a Phase 2 FLS ingest plan.")
    execute_fls.add_argument("--project-root", required=True, type=Path)
    execute_fls.add_argument("--asset-id", required=True)
    execute_fls.add_argument("--converter-path", required=True, type=Path)

    plan_splat = subparsers.add_parser("plan-gaussian-splat", help="Create a Phase 2 Gaussian Splatting plan.")
    plan_splat.add_argument("--project-root", required=True, type=Path)
    plan_splat.add_argument("--asset-id", required=True)
    plan_splat.add_argument("--name", required=True)
    plan_splat.add_argument("--source-las", required=True, type=Path)
    plan_splat.add_argument("--iterations", type=int, default=3000)

    execute_splat = subparsers.add_parser("execute-gaussian-splat", help="Execute a Phase 2 Gaussian Splatting plan.")
    execute_splat.add_argument("--project-root", required=True, type=Path)
    execute_splat.add_argument("--asset-id", required=True)
    execute_splat.add_argument("--name", required=True)
    execute_splat.add_argument("--trainer-path", required=True, type=Path)
    execute_splat.add_argument("--python-path", default=Path("python"), type=Path)

    publish_phase2 = subparsers.add_parser("publish-phase2-viewer", help="Publish the Phase 2 unified viewer manifest.")
    publish_phase2.add_argument("--project-root", required=True, type=Path)
    publish_phase2.add_argument("--asset-id", required=True)
    publish_phase2.add_argument("--potree-path", type=Path)
    publish_phase2.add_argument("--splat-path", type=Path)
    publish_phase2.add_argument("--report", action="append", default=[], type=Path)

    phase2_status = subparsers.add_parser("phase2-status", help="Write the Phase 2 module status report.")
    phase2_status.add_argument("--project-root", required=True, type=Path)

    phase3_tool_check = subparsers.add_parser("phase3-tool-check", help="Check Phase 3 production tool paths.")
    phase3_tool_check.add_argument("--project-root", required=True, type=Path)
    phase3_tool_check.add_argument("--fls-converter", type=Path)
    phase3_tool_check.add_argument("--pdal-path", type=Path)
    phase3_tool_check.add_argument("--potree-converter", type=Path)
    phase3_tool_check.add_argument("--gaussian-trainer", type=Path)
    phase3_tool_check.add_argument("--open3d-script", type=Path)

    plan_production = subparsers.add_parser("plan-production-run", help="Create a Phase 3 production pipeline plan.")
    plan_production.add_argument("--project-root", required=True, type=Path)
    plan_production.add_argument("--asset-id", required=True)
    plan_production.add_argument("--slice-name", default="room-a")
    plan_production.add_argument("--segment-name", default="baseline")
    plan_production.add_argument("--splat-name", default="baseline")
    plan_production.add_argument("--potree-converter", default=Path("PotreeConverter"), type=Path)
    plan_production.add_argument("--pdal-path", default=Path("pdal"), type=Path)
    plan_production.add_argument("--python-path", default=Path("python"), type=Path)
    plan_production.add_argument("--open3d-script", default=Path("scripts/open3d_rule_segment.py"), type=Path)

    report_production = subparsers.add_parser("report-production-run", help="Create a Phase 3 production run report from a plan.")
    report_production.add_argument("--project-root", required=True, type=Path)
    report_production.add_argument("--asset-id", required=True)

    index_assets = subparsers.add_parser("index-assets", help="Build the project asset registry.")
    index_assets.add_argument("--project-root", required=True, type=Path)

    deployment = subparsers.add_parser("check-deployment-package", help="Check Phase 3 deployment package readiness.")
    deployment.add_argument("--project-root", required=True, type=Path)
    deployment.add_argument("--asset-id", required=True)
    export_delivery = subparsers.add_parser("export-delivery-package", help="Export a Phase 3 delivery package for handoff.")
    export_delivery.add_argument("--project-root", required=True, type=Path)
    export_delivery.add_argument("--asset-id", required=True)
    export_delivery.add_argument("--zip", action="store_true", dest="make_zip")
    export_delivery.add_argument("--allow-review-required", action="store_true", dest="allow_review_required")

    create_job = subparsers.add_parser("create-production-job", help="Create a Phase 4 production job from a run plan.")
    create_job.add_argument("--project-root", required=True, type=Path)
    create_job.add_argument("--asset-id", required=True)
    create_job.add_argument("--job-id")

    update_job = subparsers.add_parser("update-job-step", help="Update a Phase 4 production job step status.")
    update_job.add_argument("--project-root", required=True, type=Path)
    update_job.add_argument("--asset-id", required=True)
    update_job.add_argument("--job-id", required=True)
    update_job.add_argument("--step-id", required=True)
    update_job.add_argument("--status", required=True, choices=["planned", "running", "completed", "failed", "blocked"])
    update_job.add_argument("--message", default="")

    serve_api = subparsers.add_parser("serve-api", help="Start the FastAPI service for a workspace.")
    serve_api.add_argument("--project-root", required=True, type=Path)
    serve_api.add_argument("--host", default="127.0.0.1")
    serve_api.add_argument("--port", default=8000, type=int)
    serve_api.add_argument("--api-key")
    serve_api.add_argument("--mode", default="development", choices=["development", "production"])
    serve_api.add_argument("--dry-run", action="store_true")

    consistency = subparsers.add_parser("check-consistency", help="Write a Phase 5 workspace consistency report.")
    consistency.add_argument("--project-root", required=True, type=Path)
    consistency.add_argument("--asset-id", required=True)

    analyze_cloud = subparsers.add_parser("analyze-point-cloud", help="Analyze point cloud sample records for Phase 6 quality insights.")
    analyze_cloud.add_argument("--project-root", required=True, type=Path)
    analyze_cloud.add_argument("--asset-id", required=True)
    analyze_cloud.add_argument("--points-json", required=True, type=Path)
    analyze_cloud.add_argument("--grid-cell-size", default=5.0, type=float)
    analyze_asset = subparsers.add_parser("analyze-asset", help="Analyze an existing workspace asset source for Phase 7 quality insights.")
    analyze_asset.add_argument("--project-root", required=True, type=Path)
    analyze_asset.add_argument("--asset-id", required=True)
    analyze_asset.add_argument("--max-points", default=10000, type=int)
    analyze_asset.add_argument("--grid-cell-size", default=5.0, type=float)
    quality_gate = subparsers.add_parser("check-quality-gate", help="Build a Phase 8 quality gate report from analysis findings.")
    quality_gate.add_argument("--project-root", required=True, type=Path)
    quality_gate.add_argument("--asset-id", required=True)
    segment_objects = subparsers.add_parser("segment-objects", help="Create a Phase 10 object segmentation report from point records.")
    segment_objects.add_argument("--project-root", required=True, type=Path)
    segment_objects.add_argument("--asset-id", required=True)
    segment_objects.add_argument("--points-json", required=True, type=Path)
    segment_objects.add_argument("--distance-threshold", default=1.0, type=float)
    segment_objects.add_argument("--min-points", default=10, type=int)
    segment_asset_objects = subparsers.add_parser("segment-asset-objects", help="Create a Phase 10 object segmentation report from a workspace asset source.")
    segment_asset_objects.add_argument("--project-root", required=True, type=Path)
    segment_asset_objects.add_argument("--asset-id", required=True)
    segment_asset_objects.add_argument("--distance-threshold", default=1.0, type=float)
    segment_asset_objects.add_argument("--min-points", default=10, type=int)
    segment_asset_objects.add_argument("--max-points", default=10000, type=int)
    segment_asset_objects.add_argument("--engine", default="builtin", choices=["builtin", "open3d"])
    segment_asset_objects.add_argument("--config", type=Path)
    project_gate = subparsers.add_parser("check-project-gate", help="Build a Phase 11 project-level gate from asset quality gates.")
    project_gate.add_argument("--project-root", required=True, type=Path)
    batch_run = subparsers.add_parser("plan-batch-run", help="Create a Phase 11 batch run plan for all indexed assets.")
    batch_run.add_argument("--project-root", required=True, type=Path)
    batch_run.add_argument("--operations", nargs="+")
    run_segmentation = subparsers.add_parser("run-segmentation", help="Run a versioned Phase 13A object segmentation.")
    run_segmentation.add_argument("--project-root", required=True, type=Path)
    run_segmentation.add_argument("--asset-id", required=True)
    run_segmentation.add_argument("--run-id", required=True)
    run_segmentation.add_argument("--engine", default="builtin_geometric")
    run_segmentation.add_argument("--allow-fallback", action="store_true")
    run_segmentation.add_argument("--distance-threshold", default=1.0, type=float)
    run_segmentation.add_argument("--min-points", default=10, type=int)
    run_segmentation.add_argument("--voxel-size", type=float)
    run_segmentation.add_argument("--max-points", default=10000, type=int)
    import_benchmark = subparsers.add_parser(
        "import-segmentation-benchmark",
        help="Validate and import a Phase 13B golden benchmark.",
    )
    import_benchmark.add_argument("--project-root", required=True, type=Path)
    import_benchmark.add_argument("--manifest", required=True, type=Path)
    evaluate_segmentation = subparsers.add_parser(
        "evaluate-segmentation-run",
        help="Evaluate a Phase 13A run against golden labels.",
    )
    evaluate_segmentation.add_argument("--project-root", required=True, type=Path)
    evaluate_segmentation.add_argument("--asset-id", required=True)
    evaluate_segmentation.add_argument("--run-id", required=True)
    evaluate_segmentation.add_argument("--benchmark-id", required=True)
    evaluate_segmentation.add_argument("--sample-id", required=True)
    evaluate_segmentation.add_argument("--evaluation-id", required=True)
    evaluate_segmentation.add_argument("--config", required=True, type=Path)
    compare_segmentation = subparsers.add_parser(
        "compare-segmentation-runs",
        help="Compare golden evaluations and write a regression gate.",
    )
    compare_segmentation.add_argument("--project-root", required=True, type=Path)
    compare_segmentation.add_argument("--asset-id", required=True)
    compare_segmentation.add_argument("--comparison-id", required=True)
    compare_segmentation.add_argument("--baseline-evaluation-id", required=True)
    compare_segmentation.add_argument("--candidate-evaluation-id", required=True)
    compare_segmentation.add_argument("--thresholds", required=True, type=Path)
    search_segmentation = subparsers.add_parser(
        "search-segmentation-params",
        help="Run a bounded deterministic Phase 13B parameter search.",
    )
    search_segmentation.add_argument("--project-root", required=True, type=Path)
    search_segmentation.add_argument("--asset-id", required=True)
    search_segmentation.add_argument("--benchmark-id", required=True)
    search_segmentation.add_argument("--sample-id", required=True)
    search_segmentation.add_argument("--search-id", required=True)
    search_segmentation.add_argument("--config", required=True, type=Path)
    search_segmentation.add_argument("--baseline-evaluation-id")
    create_correction = subparsers.add_parser(
        "create-segmentation-correction",
        help="Create a Phase 14 correction draft from a completed segmentation run.",
    )
    create_correction.add_argument("--project-root", required=True, type=Path)
    create_correction.add_argument("--asset-id", required=True)
    create_correction.add_argument("--run-id", required=True)
    create_correction.add_argument("--session-id", required=True)
    create_correction.add_argument("--sample-id", required=True)
    create_correction.add_argument("--actor", required=True)
    create_correction.add_argument("--benchmark-id")
    create_correction.add_argument("--baseline-release-id")
    apply_correction = subparsers.add_parser(
        "apply-segmentation-correction",
        help="Append one validated Phase 14 correction event.",
    )
    apply_correction.add_argument("--project-root", required=True, type=Path)
    apply_correction.add_argument("--asset-id", required=True)
    apply_correction.add_argument("--session-id", required=True)
    apply_correction.add_argument("--actor", required=True)
    apply_correction.add_argument("--expected-revision", required=True, type=int)
    apply_correction.add_argument("--client-request-id", required=True)
    apply_correction.add_argument("--operation", required=True, type=Path)
    submit_correction = subparsers.add_parser(
        "submit-segmentation-correction",
        help="Submit a Phase 14 correction draft for review.",
    )
    submit_correction.add_argument("--project-root", required=True, type=Path)
    submit_correction.add_argument("--asset-id", required=True)
    submit_correction.add_argument("--session-id", required=True)
    submit_correction.add_argument("--actor", required=True)
    submit_correction.add_argument("--expected-revision", required=True, type=int)
    publish_correction = subparsers.add_parser(
        "publish-segmentation-correction",
        help="Publish one reviewed Phase 14 correction release.",
    )
    publish_correction.add_argument("--project-root", required=True, type=Path)
    publish_correction.add_argument("--asset-id", required=True)
    publish_correction.add_argument("--session-id", required=True)
    publish_correction.add_argument("--publication", required=True, type=Path)
    retry_publication = subparsers.add_parser(
        "retry-segmentation-publication",
        help="Retry downstream tasks for a published correction release.",
    )
    retry_publication.add_argument("--project-root", required=True, type=Path)
    retry_publication.add_argument("--asset-id", required=True)
    retry_publication.add_argument("--release-id", required=True)
    retry_publication.add_argument("--actor", required=True)
    return parser





