import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_publish_potree_reads_asset_and_invokes_runner():
    # M4 Potree CLI 验收：从 asset.json 找到源 LAS，并调用 Potree 发布适配器。
    workspace = case_dir("cli-publish-potree")
    project = workspace / "workspace"
    source = workspace / "scan.las"
    source.write_text("LAS placeholder", encoding="utf-8")
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
    converter = workspace / "PotreeConverter.exe"
    converter.write_text("placeholder", encoding="utf-8")
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        output_dir = project / "previews" / "scan" / "potree"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "metadata.json").write_text("{}", encoding="utf-8")
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

    manifest = project / "previews" / "scan" / "potree_manifest.json"
    assert exit_code == 0
    assert manifest.exists()
    assert calls[0][0] == str(converter)
