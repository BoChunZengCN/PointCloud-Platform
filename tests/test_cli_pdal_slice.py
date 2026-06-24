import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_execute_slice_can_use_pdal_engine():
    # execute-slice --engine pdal 验收：
    # CLI 根据参数选择 PDAL 适配器，并把 pdal_path 传入 runner 命令。
    workspace = case_dir("cli-pdal-slice")
    project = workspace / "workspace"
    slice_dir = project / "data" / "assets" / "scan" / "slices" / "room-a"
    slice_dir.mkdir(parents=True)
    (slice_dir / "slice_plan.json").write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "slice_id": "scan-room-a",
                "source_file": "C:/data/scan.las",
                "crop_bounds": {"min": [1, 2, 3], "max": [4, 5, 6]},
                "voxel_size": 0.1,
                "output": {"format": "las", "file_name": "scan-room-a.las"},
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_pdal_runner(command: list[str]) -> int:
        calls.append(command)
        (slice_dir / "scan-room-a.las").write_text("slice", encoding="utf-8")
        return 0

    exit_code = main(
        [
            "execute-slice",
            "--project-root",
            str(project),
            "--asset-id",
            "scan",
            "--slice-name",
            "room-a",
            "--engine",
            "pdal",
            "--pdal-path",
            "C:/tools/pdal.exe",
        ],
        pdal_runner=fake_pdal_runner,
    )

    saved = json.loads((slice_dir / "slice_plan.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert calls[0][0] == "C:/tools/pdal.exe"
    assert calls[0][1] == "pipeline"
    assert saved["execution"]["engine"] == "pdal"
