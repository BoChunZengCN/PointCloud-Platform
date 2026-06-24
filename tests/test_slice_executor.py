import json
from pathlib import Path
from uuid import uuid4

import pytest

from pc_system.slice_executor import SliceExecutionError, SliceExecutionResult, execute_slice_plan


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避免依赖系统临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_execute_slice_plan_updates_status_and_records_adapter_result():
    # 这个测试锁定 M5 执行层契约：
    # 读取 planned 计划 -> 调用适配器 -> 写回 completed 状态。
    workspace = case_dir("execute-slice")
    output_file = workspace / "scan-room-a.ply"
    plan_path = workspace / "slice_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "slice_id": "scan-room-a",
                "source_file": "C:/data/scan.las",
                "crop_bounds": {"min": [1, 2, 3], "max": [4, 5, 6]},
                "voxel_size": 0.05,
                "output": {"format": "ply", "file_name": output_file.name},
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )

    def fake_adapter(plan: dict, destination: Path) -> SliceExecutionResult:
        assert plan["source_file"] == "C:/data/scan.las"
        assert destination == output_file
        destination.write_text("sliced point cloud placeholder", encoding="utf-8")
        return SliceExecutionResult(output_path=destination, point_count=25, engine="fake")

    updated = execute_slice_plan(plan_path, fake_adapter)

    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert updated["status"] == "completed"
    assert saved["status"] == "completed"
    assert saved["execution"]["engine"] == "fake"
    assert saved["execution"]["point_count"] == 25
    assert saved["execution"]["output_path"] == str(output_file)
    assert output_file.exists()


def test_execute_slice_plan_fails_when_adapter_does_not_create_output():
    # 可靠性要求：适配器返回成功但输出文件不存在时，不能把计划标记为 completed。
    workspace = case_dir("execute-slice-missing-output")
    output_file = workspace / "scan-room-a.ply"
    plan_path = workspace / "slice_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "asset_id": "scan",
                "slice_id": "scan-room-a",
                "source_file": "C:/data/scan.las",
                "crop_bounds": {"min": [1, 2, 3], "max": [4, 5, 6]},
                "voxel_size": None,
                "output": {"format": "ply", "file_name": output_file.name},
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )

    def fake_adapter(plan: dict, destination: Path) -> SliceExecutionResult:
        return SliceExecutionResult(output_path=destination, point_count=0, engine="fake")

    with pytest.raises(SliceExecutionError, match="output file was not created"):
        execute_slice_plan(plan_path, fake_adapter)

    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert saved["status"] == "planned"
