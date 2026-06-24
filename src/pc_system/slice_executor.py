import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json


class SliceExecutionError(RuntimeError):
    """切片执行结果不符合预期时抛出的明确错误。"""


@dataclass(frozen=True)
class SliceExecutionResult:
    """切片执行器返回的统一结果。

    真实 PDAL/Open3D 适配器未来只要返回这个结构，上层 CLI 和状态更新逻辑
    就不需要关心底层到底用了哪种点云处理引擎。
    """

    output_path: Path
    point_count: int
    engine: str


SliceAdapter = Callable[[dict, Path], SliceExecutionResult]


def placeholder_slice_adapter(plan: dict, destination: Path) -> SliceExecutionResult:
    """占位切片适配器。

    当前项目还没有接入真实 PDAL/Open3D，所以默认适配器只写一个占位文件。
    它的价值是让 execute-slice 的目录、状态和接口先跑通。
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "Placeholder slice output. Replace adapter with PDAL/Open3D execution.\n",
        encoding="utf-8",
    )
    return SliceExecutionResult(output_path=destination, point_count=0, engine="placeholder")


def execute_slice_plan(plan_path: Path, adapter: SliceAdapter = placeholder_slice_adapter) -> dict:
    """执行 slice_plan.json，并把执行结果写回计划文件。

    数据流：
    slice_plan.json -> adapter(plan, output_path) -> 校验输出文件 -> 更新 status/execution -> 写回 JSON。
    """

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    destination = plan_path.parent / plan["output"]["file_name"]
    result = adapter(plan, destination)
    if not result.output_path.exists():
        raise SliceExecutionError(f"Slice output file was not created: {result.output_path}")
    plan["status"] = "completed"
    plan["execution"] = {
        "engine": result.engine,
        "point_count": result.point_count,
        "output_path": str(result.output_path),
    }
    write_json(plan, plan_path)
    return plan
