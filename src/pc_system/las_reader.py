from pathlib import Path
from typing import Any

from pc_system.asset import LasAssetInfo


class LasReaderDependencyError(RuntimeError):
    """缺少真实 LAS/LAZ 读取依赖时抛出的明确错误。"""

    pass


def _load_laspy() -> Any:
    """延迟加载 laspy。

    这样项目骨架、测试和 demo 流程可以在未安装 laspy 时运行；
    真正读取 LAS/LAZ 文件时才要求安装该依赖。
    """

    try:
        import laspy
    except ModuleNotFoundError as exc:
        raise LasReaderDependencyError(
            "Install laspy to read real LAS/LAZ files: python -m pip install laspy"
        ) from exc
    return laspy


def _as_float_list(values: Any) -> list[float]:
    """把 laspy/numpy 风格的数组转换成可 JSON 序列化的 float 列表。"""

    return [float(value) for value in values]


def read_las_info(las_path: Path, laspy_module: Any | None = None) -> LasAssetInfo:
    """读取真实 LAS/LAZ header，并转换为系统内部的 LasAssetInfo。

    laspy_module 参数主要服务于测试：测试可以注入假的 laspy 对象，
    从而验证解析逻辑，而不依赖真实点云文件或外部库。
    """

    laspy = laspy_module if laspy_module is not None else _load_laspy()
    las = laspy.read(las_path)
    header = las.header
    dimensions = set(las.point_format.dimension_names)
    return LasAssetInfo(
        point_count=int(header.point_count),
        bounds={"min": _as_float_list(header.mins), "max": _as_float_list(header.maxs)},
        has_rgb={"red", "green", "blue"}.issubset(dimensions),
        has_classification="classification" in dimensions,
        has_crs=header.parse_crs() is not None,
        scale=_as_float_list(header.scales),
        offset=_as_float_list(header.offsets),
        point_format=str(header.point_format),
    )
