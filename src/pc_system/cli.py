import argparse
import json
import sys
from pathlib import Path

from pc_system.asset import LasAssetInfo, build_asset_metadata, write_asset_metadata
from pc_system.config import ProjectConfig
from pc_system.fls_ingest import (
    FlsIngestRequest,
    FlsRunner,
    build_fls_ingest_plan,
    execute_fls_ingest_plan,
    subprocess_runner as fls_subprocess_runner,
    write_fls_ingest_plan,
)
from pc_system.gaussian_splatting import (
    GaussianRunner,
    GaussianSplatRequest,
    build_gaussian_splat_plan,
    execute_gaussian_splat_plan,
    subprocess_runner as gaussian_subprocess_runner,
    write_gaussian_splat_plan,
)
from pc_system.las_reader import LasReaderDependencyError, read_las_info
from pc_system.module_status import build_module_status_report, write_module_status_report
from pc_system.open3d_rule_segmentation_adapter import (
    Open3DRunner,
    open3d_rule_segmentation_adapter,
    subprocess_runner as open3d_subprocess_runner,
)
from pc_system.pdal_slice_adapter import PdalRunner, pdal_slice_adapter, subprocess_runner as pdal_subprocess_runner
from pc_system.phase2_status import build_phase2_status_report, write_phase2_status_report
from pc_system.phase2_viewer import build_phase2_viewer_manifest, publish_phase2_viewer
from pc_system.phase3_tool_check import ToolSpec, build_tool_check_report, write_tool_check_report
from pc_system.potree_publisher import PotreePublishRequest, PotreeRunner, publish_potree, subprocess_runner
from pc_system.preview import publish_preview
from pc_system.qa import build_quality_report, write_quality_report
from pc_system.rule_segmentation import (
    RuleSegmentationRequest,
    build_rule_segmentation_plan,
    execute_rule_segmentation_plan,
    write_rule_segmentation_plan,
)
from pc_system.segmentation_summary import build_segmentation_summary, write_segmentation_summary
from pc_system.slice_executor import SliceAdapter, execute_slice_plan, placeholder_slice_adapter
from pc_system.slice_plan import SliceRequest, build_slice_plan, write_slice_plan


def _demo_las_info() -> LasAssetInfo:
    """生成 demo 用的最小 LAS 元数据。

    在未安装 laspy 或没有真实 LAS 样例时，仍然可以跑通 M1-M4 流程。
    """

    return LasAssetInfo(
        point_count=1,
        bounds={"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]},
        has_rgb=True,
        has_classification=False,
        has_crs=False,
        scale=[0.001, 0.001, 0.001],
        offset=[0.0, 0.0, 0.0],
        point_format="demo",
    )


def build_parser() -> argparse.ArgumentParser:
    """定义命令行接口。

    第一阶段先提供 init 和 demo-phase1；后续会增加 ingest 来读取真实 LAS。
    """

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

    # plan-slice 只生成切片计划，不真正裁剪点云。
    # 这样可以先把“用户想切的空间范围”稳定落盘，后续再替换执行引擎。
    plan_slice = subparsers.add_parser("plan-slice", help="Create an M5 slice plan from asset metadata.")
    plan_slice.add_argument("--project-root", required=True, type=Path)
    plan_slice.add_argument("--asset-id", required=True)
    plan_slice.add_argument("--name", required=True)
    # argparse 会把三个坐标值转换成 list[float]，分别代表包围盒最小点和最大点。
    plan_slice.add_argument("--min", required=True, type=float, nargs=3, dest="min_bounds")
    plan_slice.add_argument("--max", required=True, type=float, nargs=3, dest="max_bounds")
    plan_slice.add_argument("--voxel-size", type=float)
    plan_slice.add_argument("--output-format", default="las", choices=["las", "laz", "ply"])

    # execute-slice 读取已有 slice_plan.json，并通过适配器执行实际裁剪。
    execute_slice = subparsers.add_parser("execute-slice", help="Execute an existing M5 slice plan.")
    execute_slice.add_argument("--project-root", required=True, type=Path)
    execute_slice.add_argument("--asset-id", required=True)
    execute_slice.add_argument("--slice-name", required=True)
    execute_slice.add_argument("--engine", default="placeholder", choices=["placeholder", "pdal"])
    execute_slice.add_argument("--pdal-path", default=Path("pdal"), type=Path)

    # publish-potree 负责把已有资产交给 PotreeConverter 生成浏览器可加载的点云目录。
    publish_potree_parser = subparsers.add_parser("publish-potree", help="Publish an asset with PotreeConverter.")
    publish_potree_parser.add_argument("--project-root", required=True, type=Path)
    publish_potree_parser.add_argument("--asset-id", required=True)
    publish_potree_parser.add_argument("--converter-path", required=True, type=Path)

    # plan-rule-segment 生成 M6 规则分割计划，不直接跑复杂算法。
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

    # execute-rule-segment 执行已有规则分割计划，当前默认使用占位适配器。
    execute_rule = subparsers.add_parser("execute-rule-segment", help="Execute an M6 rule segmentation plan.")
    execute_rule.add_argument("--project-root", required=True, type=Path)
    execute_rule.add_argument("--asset-id", required=True)
    execute_rule.add_argument("--slice-name", required=True)
    execute_rule.add_argument("--name", required=True)
    execute_rule.add_argument("--engine", default="placeholder", choices=["placeholder", "open3d"])
    execute_rule.add_argument("--python-path", default=Path("python"), type=Path)
    execute_rule.add_argument("--script-path", default=Path("open3d_rule_segment.py"), type=Path)

    # report-rule-segment 把 labels JSON 转成可审计的摘要报告。
    report_rule = subparsers.add_parser("report-rule-segment", help="Create an M6 rule segmentation summary report.")
    report_rule.add_argument("--project-root", required=True, type=Path)
    report_rule.add_argument("--asset-id", required=True)
    report_rule.add_argument("--slice-name", required=True)
    report_rule.add_argument("--name", required=True)

    module_status = subparsers.add_parser("module-status", help="Write the Phase 1 module status report.")
    module_status.add_argument("--project-root", required=True, type=Path)
    # Phase 2: FLS 原始格式接入计划与执行。
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

    # Phase 2: Gaussian Splatting 训练/发布边界。
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
    return parser


def run_init(project_root: Path) -> int:
    """初始化项目目录。"""

    ProjectConfig(project_root=project_root).ensure_directories()
    return 0


def run_demo_phase1(project_root: Path, las_path: Path) -> int:
    """用模拟元数据执行阶段一完整流程。

    数据流：
    LAS 路径 -> asset.json -> 质检报告 -> 预览 manifest/HTML。
    """

    return run_phase1_workflow(project_root, las_path, _demo_las_info())


def run_phase1_workflow(project_root: Path, las_path: Path, las_info: LasAssetInfo) -> int:
    """执行阶段一公共流程。

    demo 和 ingest 的区别只在 LAS 元数据来源不同，落盘结构完全一致。
    """

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    metadata = build_asset_metadata(las_path, las_info)
    # 所有派生文件按 asset_id 分目录存放，便于一个项目下管理多个点云资产。
    asset_dir = paths["assets"] / metadata["asset_id"]
    report_dir = paths["reports"] / metadata["asset_id"]
    preview_dir = paths["previews"] / metadata["asset_id"]

    # 阶段一的三个核心产物：资产元数据、质检报告、预览入口。
    write_asset_metadata(metadata, asset_dir / "asset.json")
    report = build_quality_report(metadata)
    write_quality_report(report, report_dir)
    publish_preview(metadata, preview_dir)
    return 0


def _asset_dir(project_root: Path, asset_id: str) -> Path:
    """统一通过 ProjectConfig 解析资产目录，避免各命令各自拼路径。"""

    return ProjectConfig(project_root=project_root).paths()["assets"] / asset_id


def _load_asset_metadata(metadata_path: Path) -> dict:
    """读取 asset.json；缺失时抛出 FileNotFoundError 由调用方转成退出码。"""

    return json.loads(metadata_path.read_text(encoding="utf-8"))


def run_ingest(project_root: Path, las_path: Path, las_info_reader=read_las_info) -> int:
    """读取真实 LAS/LAZ 元数据，并执行阶段一公共流程。"""

    try:
        # 真实读取逻辑通过参数注入，测试时可以替换为 fake reader。
        las_info = las_info_reader(las_path)
    except LasReaderDependencyError as exc:
        # 缺少 laspy 是环境问题，不是未知崩溃；返回 2 便于脚本识别失败原因。
        print(exc, file=sys.stderr)
        return 2
    return run_phase1_workflow(project_root, las_path, las_info)


def run_plan_slice(
    project_root: Path,
    asset_id: str,
    name: str,
    min_bounds: list[float],
    max_bounds: list[float],
    voxel_size: float | None,
    output_format: str,
) -> int:
    """读取资产元数据并写出 M5 切片计划。"""

    # M5 依赖 M2 的 asset.json，所以这里从资产目录反查原始点云路径和范围。
    asset_dir = _asset_dir(project_root, asset_id)
    metadata_path = asset_dir / "asset.json"
    try:
        metadata = _load_asset_metadata(metadata_path)
    except FileNotFoundError:
        print(f"Asset metadata not found: {metadata_path}", file=sys.stderr)
        return 2

    # CLI 参数转换成领域对象 SliceRequest，后续执行器也会复用这个对象。
    request = SliceRequest(
        name=name,
        bounds={"min": min_bounds, "max": max_bounds},
        voxel_size=voxel_size,
        output_format=output_format,
    )
    plan = build_slice_plan(metadata, request)
    # 每个切片独立一个目录，后续真实裁剪结果、日志和状态都可以放在这里。
    write_slice_plan(plan, asset_dir / "slices" / name)
    return 0


def run_execute_slice(
    project_root: Path,
    asset_id: str,
    slice_name: str,
    slice_adapter: SliceAdapter = placeholder_slice_adapter,
    engine: str = "placeholder",
    pdal_path: Path = Path("pdal"),
    pdal_runner: PdalRunner = pdal_subprocess_runner,
) -> int:
    """执行已存在的 M5 切片计划。"""

    plan_path = _asset_dir(project_root, asset_id) / "slices" / slice_name / "slice_plan.json"
    if not plan_path.exists():
        print(f"Slice plan not found: {plan_path}", file=sys.stderr)
        return 2
    if engine == "pdal":
        # 将 PDAL 参数包装成 SliceAdapter 协议，保持 execute_slice_plan 的入口不变。
        def adapter(plan: dict, destination: Path):
            return pdal_slice_adapter(plan, destination, pdal_path=pdal_path, runner=pdal_runner)

        execute_slice_plan(plan_path, adapter)
        return 0
    execute_slice_plan(plan_path, slice_adapter)
    return 0


def run_publish_potree(
    project_root: Path,
    asset_id: str,
    converter_path: Path,
    potree_runner: PotreeRunner = subprocess_runner,
) -> int:
    """读取资产元数据，并调用 PotreeConverter 生成 Potree 预览目录。"""

    asset_dir = _asset_dir(project_root, asset_id)
    try:
        metadata = _load_asset_metadata(asset_dir / "asset.json")
    except FileNotFoundError:
        print(f"Asset metadata not found: {asset_dir / 'asset.json'}", file=sys.stderr)
        return 2
    output_dir = ProjectConfig(project_root=project_root).paths()["previews"] / asset_id / "potree"
    publish_potree(
        PotreePublishRequest(
            asset_id=asset_id,
            source_file=Path(metadata["file"]["path"]),
            output_dir=output_dir,
            converter_path=converter_path,
        ),
        runner=potree_runner,
    )
    return 0



def _slice_dir(project_root: Path, asset_id: str, slice_name: str) -> Path:
    """统一解析切片目录。"""

    return _asset_dir(project_root, asset_id) / "slices" / slice_name


def run_plan_rule_segment(
    project_root: Path,
    asset_id: str,
    slice_name: str,
    name: str,
    methods: list[str],
) -> int:
    """读取切片计划并写出 M6 规则分割计划。"""

    slice_plan_path = _slice_dir(project_root, asset_id, slice_name) / "slice_plan.json"
    if not slice_plan_path.exists():
        print(f"Slice plan not found: {slice_plan_path}", file=sys.stderr)
        return 2
    slice_plan = json.loads(slice_plan_path.read_text(encoding="utf-8"))
    request = RuleSegmentationRequest(name=name, methods=methods)
    plan = build_rule_segmentation_plan(slice_plan, request)
    write_rule_segmentation_plan(plan, _slice_dir(project_root, asset_id, slice_name) / "segments" / name)
    return 0


def run_execute_rule_segment(
    project_root: Path,
    asset_id: str,
    slice_name: str,
    name: str,
    engine: str = "placeholder",
    python_path: Path = Path("python"),
    script_path: Path = Path("open3d_rule_segment.py"),
    open3d_runner: Open3DRunner = open3d_subprocess_runner,
    fls_runner: FlsRunner = fls_subprocess_runner,
    gaussian_runner: GaussianRunner = gaussian_subprocess_runner,
) -> int:
    """执行已有 M6 规则分割计划。"""

    plan_path = _slice_dir(project_root, asset_id, slice_name) / "segments" / name / "rule_segmentation_plan.json"
    if not plan_path.exists():
        print(f"Rule segmentation plan not found: {plan_path}", file=sys.stderr)
        return 2
    if engine == "open3d":
        # 将 Open3D 脚本参数包装成 RuleSegmentationAdapter 协议。
        def adapter(plan: dict, destination: Path):
            return open3d_rule_segmentation_adapter(
                plan,
                destination,
                python_path=python_path,
                script_path=script_path,
                runner=open3d_runner,
            )

        execute_rule_segmentation_plan(plan_path, adapter)
        return 0
    execute_rule_segmentation_plan(plan_path)
    return 0

def run_report_rule_segment(
    project_root: Path,
    asset_id: str,
    slice_name: str,
    name: str,
) -> int:
    """读取规则分割 labels JSON，并生成摘要报告。"""

    segment_dir = _slice_dir(project_root, asset_id, slice_name) / "segments" / name
    plan_path = segment_dir / "rule_segmentation_plan.json"
    if not plan_path.exists():
        print(f"Rule segmentation plan not found: {plan_path}", file=sys.stderr)
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    labels_path = segment_dir / plan["output"]["file_name"]
    if not labels_path.exists():
        print(f"Rule segmentation labels not found: {labels_path}", file=sys.stderr)
        return 2
    labels_payload = json.loads(labels_path.read_text(encoding="utf-8"))
    summary = build_segmentation_summary(labels_payload)
    report_dir = ProjectConfig(project_root=project_root).paths()["reports"] / asset_id / "segments" / slice_name / name
    write_segmentation_summary(summary, report_dir)
    return 0


def run_module_status(project_root: Path) -> int:
    """写出 Phase 1 模块完成度报告。"""

    report = build_module_status_report()
    output_dir = ProjectConfig(project_root=project_root).paths()["reports"]
    write_module_status_report(report, output_dir)
    return 0



def _fls_ingest_dir(project_root: Path, asset_id: str) -> Path:
    """统一解析 Phase 2 FLS 接入目录。"""

    return ProjectConfig(project_root=project_root).ensure_directories()["data_raw"] / "fls" / asset_id


def run_plan_fls_ingest(
    project_root: Path,
    asset_id: str,
    raw_files: list[Path],
    output_las: Path,
    registration: str,
) -> int:
    """写出 Phase 2 FLS 接入计划。"""

    plan = build_fls_ingest_plan(
        FlsIngestRequest(
            asset_id=asset_id,
            raw_files=raw_files,
            output_las=output_las,
            registration=registration,
        )
    )
    write_fls_ingest_plan(plan, _fls_ingest_dir(project_root, asset_id))
    return 0


def run_execute_fls_ingest(
    project_root: Path,
    asset_id: str,
    converter_path: Path,
    fls_runner: FlsRunner = fls_subprocess_runner,
) -> int:
    """执行 Phase 2 FLS 接入计划。"""

    plan_path = _fls_ingest_dir(project_root, asset_id) / "fls_ingest_plan.json"
    if not plan_path.exists():
        print(f"FLS ingest plan not found: {plan_path}", file=sys.stderr)
        return 2
    execute_fls_ingest_plan(plan_path, converter_path=converter_path, runner=fls_runner)
    return 0


def _splat_dir(project_root: Path, asset_id: str, name: str) -> Path:
    """统一解析 Phase 2 Gaussian Splatting 输出目录。"""

    return ProjectConfig(project_root=project_root).paths()["previews"] / asset_id / "splats" / name


def run_plan_gaussian_splat(
    project_root: Path,
    asset_id: str,
    name: str,
    source_las: Path,
    iterations: int,
) -> int:
    """写出 Phase 2 Gaussian Splatting 计划。"""

    output_dir = _splat_dir(project_root, asset_id, name)
    plan = build_gaussian_splat_plan(
        GaussianSplatRequest(
            name=name,
            source_las=source_las,
            output_dir=output_dir,
            iterations=iterations,
        )
    )
    write_gaussian_splat_plan(plan, output_dir)
    return 0


def run_execute_gaussian_splat(
    project_root: Path,
    asset_id: str,
    name: str,
    trainer_path: Path,
    python_path: Path,
    gaussian_runner: GaussianRunner = gaussian_subprocess_runner,
) -> int:
    """执行 Phase 2 Gaussian Splatting 计划。"""

    plan_path = _splat_dir(project_root, asset_id, name) / "gaussian_splat_plan.json"
    if not plan_path.exists():
        print(f"Gaussian Splatting plan not found: {plan_path}", file=sys.stderr)
        return 2
    execute_gaussian_splat_plan(
        plan_path,
        trainer_path=trainer_path,
        python_path=python_path,
        runner=gaussian_runner,
    )
    return 0


def run_publish_phase2_viewer(
    project_root: Path,
    asset_id: str,
    potree_path: Path | None,
    splat_path: Path | None,
    reports: list[Path],
) -> int:
    """发布 Phase 2 统一预览入口。"""

    manifest = build_phase2_viewer_manifest(asset_id, potree_path, splat_path, reports)
    output_dir = ProjectConfig(project_root=project_root).paths()["previews"] / asset_id
    publish_phase2_viewer(manifest, output_dir)
    return 0


def run_phase2_status(project_root: Path) -> int:
    """写出 Phase 2 模块完成度报告。"""

    report = build_phase2_status_report()
    output_dir = ProjectConfig(project_root=project_root).paths()["reports"]
    write_phase2_status_report(report, output_dir)
    return 0

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

    这里统一拦截运行期错误，避免外部工具或文件缺失导致 traceback 直接暴露给用户。
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
        raise ValueError(f"Unsupported command: {args.command}")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())












