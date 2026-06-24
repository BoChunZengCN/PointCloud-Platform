import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_plan_rule_segment_reads_slice_plan_and_writes_manifest():
    # M6 CLI 计划验收：从 slice_plan.json 生成 rule_segmentation_plan.json。
    workspace = case_dir("cli-plan-rule-segment")
    project = workspace / "workspace"
    slice_dir = project / "data" / "assets" / "scan" / "slices" / "room-a"
    slice_dir.mkdir(parents=True)
    (slice_dir / "slice_plan.json").write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "slice_id": "scan-room-a",
                "status": "completed",
                "execution": {"output_path": "C:/out/scan-room-a.las"},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "plan-rule-segment",
            "--project-root",
            str(project),
            "--asset-id",
            "scan",
            "--slice-name",
            "room-a",
            "--name",
            "baseline",
        ]
    )

    plan_path = slice_dir / "segments" / "baseline" / "rule_segmentation_plan.json"
    assert exit_code == 0
    assert json.loads(plan_path.read_text(encoding="utf-8"))["segmentation_id"] == "scan-room-a-baseline"


def test_cli_execute_rule_segment_updates_manifest_and_writes_labels():
    # M6 CLI 执行验收：执行 rule_segmentation_plan.json，并生成 labels JSON。
    workspace = case_dir("cli-execute-rule-segment")
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
        ]
    )

    saved = json.loads((segment_dir / "rule_segmentation_plan.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved["status"] == "completed"
    assert (segment_dir / "scan-room-a-baseline-labels.json").exists()
