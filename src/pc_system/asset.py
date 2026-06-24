from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


@dataclass(frozen=True)
class LasAssetInfo:
    """LAS/LAZ 资产的核心元数据。

    第一阶段只关心后续模块一定会用到的信息：点数、范围、颜色、
    分类字段、坐标参考和 LAS header 中的 scale/offset。
    这里不保存真实点数组，避免大点云在资产入库阶段就被加载进内存。
    """

    point_count: int
    bounds: dict[str, list[float]]
    has_rgb: bool
    has_classification: bool
    has_crs: bool
    scale: list[float]
    offset: list[float]
    point_format: str


def build_asset_metadata(las_path: Path, info: LasAssetInfo) -> dict[str, Any]:
    """把文件路径和 LAS 元数据合并为统一的 asset.json 结构。"""

    resolved = las_path.resolve()
    return {
        # asset_id 先使用文件名主干，后续如接数据库可替换为 UUID 或哈希。
        "asset_id": resolved.stem,
        "file": {
            "path": str(resolved),
            "name": resolved.name,
            "suffix": resolved.suffix.lower(),
        },
        "las": asdict(info),
    }


def write_asset_metadata(metadata: dict[str, Any], output_path: Path) -> Path:
    """写出资产元数据，供质检、预览、分割等后续模块复用。"""

    return write_json(metadata, output_path)
