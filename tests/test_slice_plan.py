import json
from pathlib import Path
from uuid import uuid4

from pc_system.slice_plan import SliceRequest, build_slice_plan, write_slice_plan


def case_dir(name: str) -> Path:
    # 测试输出放在 tests/_output，避免依赖 pytest 默认临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_slice_plan_records_crop_bounds_and_sampling():
    # 这个测试锁定 M5 计划层的核心契约：
    # 输入资产元数据 + 裁剪请求，输出稳定的 slice_plan 字典。
    metadata = {
        "asset_id": "scan",
        "file": {"path": "C:/data/scan.las", "name": "scan.las"},
        "las": {
            "point_count": 1000,
            "bounds": {"min": [0.0, 0.0, 0.0], "max": [10.0, 20.0, 30.0]},
        },
    }
    request = SliceRequest(
        name="room-a",
        bounds={"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]},
        voxel_size=0.05,
        output_format="las",
    )

    plan = build_slice_plan(metadata, request)

    assert plan["asset_id"] == "scan"
    assert plan["slice_id"] == "scan-room-a"
    assert plan["source_file"] == "C:/data/scan.las"
    assert plan["crop_bounds"] == request.bounds
    assert plan["voxel_size"] == 0.05
    assert plan["output"]["file_name"] == "scan-room-a.las"
    assert plan["status"] == "planned"


def test_write_slice_plan_outputs_json_manifest():
    # 这个测试保证切片计划能落盘为 JSON manifest，后续 execute-slice 会读取它。
    metadata = {
        "asset_id": "scan",
        "file": {"path": "C:/data/scan.las", "name": "scan.las"},
        "las": {
            "point_count": 1000,
            "bounds": {"min": [0.0, 0.0, 0.0], "max": [10.0, 20.0, 30.0]},
        },
    }
    request = SliceRequest(
        name="room-a",
        bounds={"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]},
        voxel_size=None,
        output_format="ply",
    )

    output = write_slice_plan(build_slice_plan(metadata, request), case_dir("slice-plan"))

    assert output.name == "slice_plan.json"
    assert json.loads(output.read_text(encoding="utf-8"))["output"]["file_name"] == "scan-room-a.ply"
