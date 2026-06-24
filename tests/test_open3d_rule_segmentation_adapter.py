import json
from pathlib import Path
from uuid import uuid4

import pytest

from pc_system.open3d_rule_segmentation_adapter import (
    Open3DSegmentationFailed,
    build_open3d_segmentation_config,
    open3d_rule_segmentation_adapter,
)


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避免依赖系统临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_open3d_segmentation_config_records_source_rules_and_output():
    plan = {
        "segmentation_id": "scan-room-a-baseline",
        "source_slice": "C:/out/scan-room-a.las",
        "rules": [{"label": "ground", "method": "height_threshold"}],
    }
    destination = Path("C:/out/scan-room-a-baseline-labels.json")

    config = build_open3d_segmentation_config(plan, destination)

    assert config["segmentation_id"] == "scan-room-a-baseline"
    assert config["source_slice"] == "C:/out/scan-room-a.las"
    assert config["labels_path"] == destination.as_posix()
    assert config["rules"] == [{"label": "ground", "method": "height_threshold"}]


def test_open3d_rule_segmentation_adapter_writes_config_invokes_runner_and_counts_labels():
    workspace = case_dir("open3d-rule-segmentation")
    destination = workspace / "scan-room-a-baseline-labels.json"
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        config = json.loads((workspace / "open3d_rule_segmentation_config.json").read_text(encoding="utf-8"))
        Path(config["labels_path"]).write_text(
            json.dumps(
                {
                    "segmentation_id": config["segmentation_id"],
                    "source_slice": config["source_slice"],
                    "rules": config["rules"],
                    "labels": [
                        {"point_index": 0, "label": "ground"},
                        {"point_index": 1, "label": "noise"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return 0

    result = open3d_rule_segmentation_adapter(
        {
            "segmentation_id": "scan-room-a-baseline",
            "source_slice": "C:/out/scan-room-a.las",
            "rules": [{"label": "ground", "method": "height_threshold"}],
        },
        destination,
        python_path=Path("C:/Python/python.exe"),
        script_path=Path("C:/tools/open3d_segment.py"),
        runner=fake_runner,
    )

    assert calls == [["C:/Python/python.exe", "C:/tools/open3d_segment.py", str(workspace / "open3d_rule_segmentation_config.json")]]
    assert result.engine == "open3d-rule-segmentation"
    assert result.label_count == 2
    assert destination.exists()


def test_open3d_rule_segmentation_adapter_raises_when_runner_fails():
    workspace = case_dir("open3d-rule-segmentation-failure")

    with pytest.raises(Open3DSegmentationFailed):
        open3d_rule_segmentation_adapter(
            {
                "segmentation_id": "scan-room-a-baseline",
                "source_slice": "C:/out/scan-room-a.las",
                "rules": [],
            },
            workspace / "labels.json",
            runner=lambda command: 7,
        )
