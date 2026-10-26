"""完整 LAS 预览的只读来源识别；不加载点记录数组。"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from pc_system.identifiers import validate_identifier


@dataclass(frozen=True)
class LasSource:
    path: Path
    asset_id: str
    size: int
    sha256: str
    point_count: int
    point_format: int
    bounds: dict[str, list[float]]
    unit: str
    source_format: str = "las"
    representation_kind: str = "point_cloud"


def inspect_las_source(path: Path, asset_id: str, declared_unit: str = "m") -> LasSource:
    """只读 LAS 1.2/1.3 头部并流式计算 SHA-256。"""

    asset_id = validate_identifier(asset_id, "asset_id")
    if declared_unit != "m":
        raise ValueError("完整场景首版仅接受已确认单位 m。")
    source_path = Path(path).resolve(strict=True)
    if not source_path.is_file() or source_path.suffix.lower() != ".las":
        raise ValueError("请选择未压缩的 LAS 文件。")
    size = source_path.stat().st_size
    if size < 227:
        raise ValueError("LAS 文件头截断。")

    with source_path.open("rb") as handle:
        header = handle.read(235)
        if header[:4] != b"LASF":
            raise ValueError("LAS 签名无效。")
        major, minor = header[24:26]
        if major != 1 or minor not in (2, 3):
            raise ValueError("完整场景首版仅支持 LAS 1.2/1.3。")
        minimum_header = 227 if minor == 2 else 235
        if len(header) < minimum_header:
            raise ValueError("LAS 文件头截断。")
        header_size = struct.unpack_from("<H", header, 94)[0]
        point_offset = struct.unpack_from("<I", header, 96)[0]
        point_format = header[104]
        if point_format & 0xC0 or point_format not in (2, 3):
            raise ValueError("完整场景首版仅支持未压缩的 RGB 点格式 2/3。")
        record_length = struct.unpack_from("<H", header, 105)[0]
        point_count = struct.unpack_from("<I", header, 107)[0]
        minimum_record = 26 if point_format == 2 else 34
        if (
            header_size < minimum_header
            or point_offset < header_size
            or point_offset > size
            or record_length < minimum_record
            or point_count == 0
            or point_offset + point_count * record_length > size
        ):
            raise ValueError("LAS 头部或点记录范围无效。")
        scales = struct.unpack_from("<3d", header, 131)
        offsets = struct.unpack_from("<3d", header, 155)
        if any(not math.isfinite(value) or value <= 0 for value in scales):
            raise ValueError("LAS 坐标比例尺无效。")
        if any(not math.isfinite(value) for value in offsets):
            raise ValueError("LAS 坐标偏移无效。")
        limits = struct.unpack_from("<6d", header, 179)
        minimum = [limits[index] for index in (1, 3, 5)]
        maximum = [limits[index] for index in (0, 2, 4)]
        if any(
            not math.isfinite(low) or not math.isfinite(high) or low > high
            for low, high in zip(minimum, maximum)
        ):
            raise ValueError("LAS 坐标边界无效。")

        digest = hashlib.sha256()
        handle.seek(0)
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)

    if source_path.stat().st_size != size:
        raise ValueError("LAS 文件在检查期间发生变化。")
    return LasSource(
        path=source_path,
        asset_id=asset_id,
        size=size,
        sha256=digest.hexdigest(),
        point_count=point_count,
        point_format=point_format,
        bounds={"min": minimum, "max": maximum},
        unit=declared_unit,
    )
