from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


@dataclass(frozen=True)
class SliceRequest:
    """一次基础切片请求。

    M5 第一版先记录“想切哪里、如何降采样、输出什么格式”，
    后续再把这个请求交给 PDAL/Open3D 等真实执行引擎。
    """

    # name 是用户给切片取的短名，例如 room-a、floor-01、pipe-zone。
    name: str
    # bounds 使用 min/max 两个三维坐标描述裁剪包围盒。
    bounds: dict[str, list[float]]
    # voxel_size 为 None 时表示不做降采样；有值时交给后续执行器使用。
    voxel_size: float | None
    # 当前只规划格式，不做格式转换；真实输出由后续执行器生成。
    output_format: str


def build_slice_plan(metadata: dict[str, Any], request: SliceRequest) -> dict[str, Any]:
    """根据资产元数据和切片请求生成 slice_plan.json 内容。"""

    asset_id = metadata["asset_id"]
    # 输出格式统一转成小写，避免 PLY/ply 这类输入差异影响文件名。
    output_format = request.output_format.lower()
    # slice_id 同时包含资产名和切片名，便于跨目录汇总时保持唯一性。
    slice_id = f"{asset_id}-{request.name}"
    return {
        "asset_id": asset_id,
        "slice_id": slice_id,
        # 保留源文件路径，后续 execute-slice 不必再额外查询用户输入。
        "source_file": metadata["file"]["path"],
        # source_bounds 是原始资产范围，crop_bounds 是本次计划裁剪范围。
        "source_bounds": metadata["las"].get("bounds"),
        "crop_bounds": request.bounds,
        "voxel_size": request.voxel_size,
        "output": {
            "format": output_format,
            "file_name": f"{slice_id}.{output_format}",
        },
        # planned 表示系统已经生成可执行计划，但还没有实际裁剪点云。
        "status": "planned",
    }


def write_slice_plan(plan: dict[str, Any], output_dir: Path) -> Path:
    """写出 M5 切片计划 manifest。"""

    return write_json(plan, output_dir / "slice_plan.json")
