import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_returns_nonzero_when_pdal_execution_fails(capsys):
    # CLI 可靠性要求：PDAL 失败时返回非零并打印清晰错误，而不是 traceback。
    workspace = case_dir("cli-pdal-error")
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
                "voxel_size": None,
                "output": {"format": "las", "file_name": "scan-room-a.las"},
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )

    def fake_pdal_runner(command: list[str]) -> int:
        return 7

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
        ],
        pdal_runner=fake_pdal_runner,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "PDAL pipeline failed" in captured.err


def test_cli_returns_nonzero_when_potree_metadata_missing(capsys):
    # CLI 可靠性要求：Potree 转换产物缺失时返回非零并打印清晰错误。
    workspace = case_dir("cli-potree-error")
    project = workspace / "workspace"
    source = workspace / "scan.las"
    source.write_text("LAS placeholder", encoding="utf-8")
    converter = workspace / "PotreeConverter.exe"
    converter.write_text("placeholder", encoding="utf-8")
    asset_dir = project / "data" / "assets" / "scan"
    asset_dir.mkdir(parents=True)
    (asset_dir / "asset.json").write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "file": {"path": str(source), "name": "scan.las"},
                "las": {"point_count": 1},
            }
        ),
        encoding="utf-8",
    )

    def fake_runner(command: list[str]) -> int:
        return 0

    exit_code = main(
        [
            "publish-potree",
            "--project-root",
            str(project),
            "--asset-id",
            "scan",
            "--converter-path",
            str(converter),
        ],
        potree_runner=fake_runner,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "metadata.json" in captured.err
