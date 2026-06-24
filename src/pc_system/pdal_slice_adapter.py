import subprocess
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json
from pc_system.slice_executor import SliceExecutionResult


class PdalExecutionFailed(RuntimeError):
    """PDAL pipeline 执行失败时抛出的明确错误。"""


PdalRunner = Callable[[list[str]], int]


def subprocess_runner(command: list[str]) -> int:
    """默认 PDAL 命令执行器。

    测试通过注入 fake runner 避免依赖本机安装 PDAL；生产环境则使用该函数。
    """

    return subprocess.run(command, check=False).returncode


def _format_pdal_bounds(bounds: dict[str, list[float]]) -> str:
    """把 min/max 三维坐标转换成 PDAL filters.crop 需要的 bounds 字符串。"""

    min_x, min_y, min_z = bounds["min"]
    max_x, max_y, max_z = bounds["max"]
    return f"([{float(min_x)},{float(max_x)}],[{float(min_y)},{float(max_y)}],[{float(min_z)},{float(max_z)}])"


def _writer_type(destination: Path) -> str:
    """根据输出扩展名选择 PDAL writer。"""

    suffix = destination.suffix.lower()
    if suffix in {".las", ".laz"}:
        return "writers.las"
    if suffix == ".ply":
        return "writers.ply"
    raise ValueError(f"Unsupported PDAL output format: {destination.suffix}")


def build_pdal_pipeline(plan: dict, destination: Path) -> dict:
    """根据 slice_plan 构建 PDAL pipeline JSON。

    pipeline 顺序固定为：读取源文件、空间裁剪、可选 voxel 降采样、写出目标文件。
    """

    pipeline = [
        {"type": "readers.las", "filename": plan["source_file"]},
        {"type": "filters.crop", "bounds": _format_pdal_bounds(plan["crop_bounds"])},
    ]
    if plan.get("voxel_size") is not None:
        pipeline.append(
            {
                "type": "filters.voxelcenternearestneighbor",
                "cell": float(plan["voxel_size"]),
            }
        )
    # pipeline JSON 中统一使用 POSIX 风格路径，避免 Windows 反斜杠影响测试和日志对比。
    pipeline.append({"type": _writer_type(destination), "filename": destination.as_posix()})
    return {"pipeline": pipeline}


def pdal_slice_adapter(
    plan: dict,
    destination: Path,
    pdal_path: Path = Path("pdal"),
    runner: PdalRunner = subprocess_runner,
) -> SliceExecutionResult:
    """执行 PDAL 切片。

    该函数符合 SliceAdapter 协议，可以直接传给 execute_slice_plan。
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    pipeline_path = destination.parent / "pdal_pipeline.json"
    write_json(build_pdal_pipeline(plan, destination), pipeline_path)
    exit_code = runner([pdal_path.as_posix(), "pipeline", str(pipeline_path)])
    if exit_code != 0:
        raise PdalExecutionFailed(f"PDAL pipeline failed with exit code {exit_code}")
    return SliceExecutionResult(output_path=destination, point_count=0, engine="pdal")


