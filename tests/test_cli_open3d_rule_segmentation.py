import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_execute_rule_segment_can_use_open3d_engine():
    # execute-rule-segment --engine open3d 验收：CLI 选择 Open3D 适配器并传入脚本路径。
    workspace = case_dir("cli-open3d-rule-segment")
    project = workspace / "workspace"
    segment_dir = project / "data" / "assets" / "scan" / "slices" / "room-a" / "segments" / "baseline"
    segment_dir.mkdir(parents=True)
    (segment_dir / "rule_segmentation_plan.json").write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "slice_id": "scan-room-a",
                "segmentation_id": "scan-room-a-baseline",
                "source_slice": "C:/out/scan-room-a.las",
                "rules": [{"label": "ground", "method": "height_threshold"}],
                "output": {"file_name": "scan-room-a-baseline-labels.json"},
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        config = json.loads((segment_dir / "open3d_rule_segmentation_config.json").read_text(encoding="utf-8"))
        Path(config["labels_path"]).write_text(
            json.dumps(
                {
                    "segmentation_id": config["segmentation_id"],
                    "source_slice": config["source_slice"],
                    "rules": config["rules"],
                    "labels": [{"point_index": 0, "label": "ground"}],
                }
            ),
            encoding="utf-8",
        )
        return 0

    exit_code = main(
        [
            "execute-rule-segment",
            "--project-root",
            str(project),
            "--asset-id",
            "scan",
            "--slice-name",
            "room-a",
            "--name",
            "baseline",
            "--engine",
            "open3d",
            "--python-path",
            "C:/Python/python.exe",
            "--script-path",
            "C:/tools/open3d_segment.py",
        ],
        open3d_runner=fake_runner,
    )

    saved = json.loads((segment_dir / "rule_segmentation_plan.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert calls[0][0] == "C:/Python/python.exe"
    assert calls[0][1] == "C:/tools/open3d_segment.py"
    assert saved["execution"]["engine"] == "open3d-rule-segmentation"
    assert saved["execution"]["label_count"] == 1
