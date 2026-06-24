import json
from pathlib import Path
from uuid import uuid4

from pc_system.rule_segmentation import (
    RuleSegmentationRequest,
    build_rule_segmentation_plan,
    execute_rule_segmentation_plan,
    write_rule_segmentation_plan,
)


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避免依赖系统临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_rule_segmentation_plan_uses_slice_output_and_default_rules():
    # M6 计划层契约：基于一个已完成切片，生成规则分割计划和默认规则集合。
    slice_plan = {
        "asset_id": "scan",
        "slice_id": "scan-room-a",
        "status": "completed",
        "execution": {"output_path": "C:/out/scan-room-a.las"},
    }

    plan = build_rule_segmentation_plan(
        slice_plan,
        RuleSegmentationRequest(name="baseline", methods=["ground", "plane", "cluster", "noise"]),
    )

    assert plan["asset_id"] == "scan"
    assert plan["slice_id"] == "scan-room-a"
    assert plan["segmentation_id"] == "scan-room-a-baseline"
    assert plan["source_slice"] == "C:/out/scan-room-a.las"
    assert [rule["label"] for rule in plan["rules"]] == ["ground", "plane", "cluster", "noise"]
    assert plan["output"]["file_name"] == "scan-room-a-baseline-labels.json"
    assert plan["status"] == "planned"


def test_execute_rule_segmentation_plan_updates_status_and_writes_labels():
    # M6 执行层契约：读取 planned 计划，调用适配器，写回 completed 状态。
    workspace = case_dir("rule-segmentation")
    plan_path = workspace / "rule_segmentation_plan.json"
    labels_path = workspace / "scan-room-a-baseline-labels.json"
    plan_path.write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "slice_id": "scan-room-a",
                "segmentation_id": "scan-room-a-baseline",
                "source_slice": "C:/out/scan-room-a.las",
                "rules": [{"label": "ground", "method": "height_threshold"}],
                "output": {"file_name": labels_path.name},
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )

    updated = execute_rule_segmentation_plan(plan_path)

    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    assert updated["status"] == "completed"
    assert saved["status"] == "completed"
    assert saved["execution"]["engine"] == "placeholder-rule-segmentation"
    assert labels["segmentation_id"] == "scan-room-a-baseline"
    assert labels["labels"] == []


def test_write_rule_segmentation_plan_outputs_json_manifest():
    workspace = case_dir("rule-segmentation-write")
    plan = {
        "asset_id": "scan",
        "slice_id": "scan-room-a",
        "segmentation_id": "scan-room-a-baseline",
        "status": "planned",
    }

    output = write_rule_segmentation_plan(plan, workspace)

    assert output.name == "rule_segmentation_plan.json"
    assert json.loads(output.read_text(encoding="utf-8"))["segmentation_id"] == "scan-room-a-baseline"
