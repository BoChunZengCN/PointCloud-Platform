import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main
from pc_system.asset import LasAssetInfo


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_init_creates_project_directories():
    # M1 验收：CLI 能创建后续模块共享的标准目录结构。
    project = case_dir("cli-init") / "workspace"

    exit_code = main(["init", "--project-root", str(project)])

    assert exit_code == 0
    assert (project / "data" / "raw").is_dir()
    assert (project / "data" / "assets").is_dir()
    assert (project / "reports").is_dir()
    assert (project / "previews").is_dir()
    assert (project / "logs").is_dir()


def test_cli_demo_phase1_generates_asset_report_and_preview():
    # demo-phase1 不依赖 laspy，用来验证 M1-M4 的端到端落盘流程。
    workspace = case_dir("cli-demo")
    project = workspace / "workspace"
    las_path = workspace / "scan.las"
    las_path.write_bytes(b"LAS placeholder")

    exit_code = main(
        [
            "demo-phase1",
            "--project-root",
            str(project),
            "--las-path",
            str(las_path),
        ]
    )

    asset_json = project / "data" / "assets" / "scan" / "asset.json"
    report_json = project / "reports" / "scan" / "quality_report.json"
    preview_html = project / "previews" / "scan" / "index.html"
    assert exit_code == 0
    assert asset_json.exists()
    assert report_json.exists()
    assert preview_html.exists()
    assert json.loads(asset_json.read_text(encoding="utf-8"))["asset_id"] == "scan"


def test_cli_ingest_generates_outputs_from_reader():
    # ingest 使用真实读取入口；这里注入 fake_reader，让测试专注 CLI 编排逻辑。
    workspace = case_dir("cli-ingest")
    project = workspace / "workspace"
    las_path = workspace / "scan.las"
    las_path.write_bytes(b"LAS placeholder")

    def fake_reader(path: Path) -> LasAssetInfo:
        # 断言 CLI 会把用户传入的 LAS 路径原样交给读取器。
        assert path == las_path
        return LasAssetInfo(
            point_count=99,
            bounds={"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]},
            has_rgb=True,
            has_classification=True,
            has_crs=True,
            scale=[0.001, 0.001, 0.001],
            offset=[0.0, 0.0, 0.0],
            point_format="3",
        )

    exit_code = main(
        [
            "ingest",
            "--project-root",
            str(project),
            "--las-path",
            str(las_path),
        ],
        las_info_reader=fake_reader,
    )

    asset_json = project / "data" / "assets" / "scan" / "asset.json"
    report_json = project / "reports" / "scan" / "quality_report.json"
    preview_manifest = project / "previews" / "scan" / "preview_manifest.json"
    assert exit_code == 0
    assert json.loads(asset_json.read_text(encoding="utf-8"))["las"]["point_count"] == 99
    assert json.loads(report_json.read_text(encoding="utf-8"))["status"] == "pass"
    assert json.loads(preview_manifest.read_text(encoding="utf-8"))["point_count"] == 99


def test_cli_plan_slice_reads_asset_metadata_and_writes_plan():
    # M5 CLI 验收：从已有 asset.json 读取资产信息，并生成切片计划文件。
    workspace = case_dir("cli-plan-slice")
    project = workspace / "workspace"
    asset_dir = project / "data" / "assets" / "scan"
    asset_dir.mkdir(parents=True)
    asset_json = asset_dir / "asset.json"
    asset_json.write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "file": {"path": "C:/data/scan.las", "name": "scan.las"},
                "las": {
                    "point_count": 1000,
                    "bounds": {"min": [0, 0, 0], "max": [10, 10, 10]},
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "plan-slice",
            "--project-root",
            str(project),
            "--asset-id",
            "scan",
            "--name",
            "room-a",
            "--min",
            "1",
            "2",
            "3",
            "--max",
            "4",
            "5",
            "6",
            "--voxel-size",
            "0.05",
            "--output-format",
            "ply",
        ]
    )

    plan_path = project / "data" / "assets" / "scan" / "slices" / "room-a" / "slice_plan.json"
    assert exit_code == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["slice_id"] == "scan-room-a"
    assert plan["crop_bounds"] == {"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]}
    assert plan["output"]["file_name"] == "scan-room-a.ply"


def test_cli_execute_slice_loads_plan_and_runs_adapter():
    # execute-slice 验收：CLI 能找到 slice_plan.json，并通过注入适配器执行计划。
    workspace = case_dir("cli-execute-slice")
    project = workspace / "workspace"
    slice_dir = project / "data" / "assets" / "scan" / "slices" / "room-a"
    slice_dir.mkdir(parents=True)
    plan_path = slice_dir / "slice_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "slice_id": "scan-room-a",
                "source_file": "C:/data/scan.las",
                "crop_bounds": {"min": [1, 2, 3], "max": [4, 5, 6]},
                "voxel_size": None,
                "output": {"format": "ply", "file_name": "scan-room-a.ply"},
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )

    def fake_adapter(plan: dict, destination: Path):
        from pc_system.slice_executor import SliceExecutionResult

        destination.write_text("slice", encoding="utf-8")
        return SliceExecutionResult(output_path=destination, point_count=3, engine="fake")

    exit_code = main(
        [
            "execute-slice",
            "--project-root",
            str(project),
            "--asset-id",
            "scan",
            "--slice-name",
            "room-a",
        ],
        slice_adapter=fake_adapter,
    )

    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved["status"] == "completed"
    assert saved["execution"]["point_count"] == 3
    assert (slice_dir / "scan-room-a.ply").exists()
