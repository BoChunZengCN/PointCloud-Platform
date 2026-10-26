"""外部 Potree 2.x 转换与发布前产物门禁。"""

from __future__ import annotations

import hashlib
import json
import math
import stat
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pc_system.full_preview_source import LasSource

_MAX_METADATA = 1024 * 1024
_MAX_HIERARCHY = 256 * 1024 * 1024
_CONVERSION_TIMEOUT = 4 * 60 * 60
_NODE = struct.Struct("<BBIQQ")


@dataclass(frozen=True)
class ValidatedPotreeOutput:
    run_dir: Path
    metadata_file: Path
    point_count: int
    bounds: dict[str, list[float]]
    rgb_available: bool


def _regular_file(path: Path) -> int:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"Potree 产物缺失：{path.name}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError(f"Potree 产物不是普通文件：{path.name}")
    return info.st_size


def _source_unchanged(source: LasSource) -> None:
    digest = hashlib.sha256()
    with source.path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    if source.path.stat().st_size != source.size or digest.hexdigest() != source.sha256:
        raise ValueError("来源 LAS 在转换期间发生变化，拒绝发布。")


def _bounds(metadata: dict, source: LasSource) -> dict[str, list[float]]:
    raw = metadata.get("boundingBox")
    if not isinstance(raw, dict):
        raise ValueError("Potree 边界缺失。")
    result = {}
    for edge in ("min", "max"):
        values = raw.get(edge)
        if (
            not isinstance(values, list)
            or len(values) != 3
            or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values)
        ):
            raise ValueError("Potree 边界无效。")
        result[edge] = [float(value) for value in values]
    source_span = max(
        max(source.bounds["max"][i] - source.bounds["min"][i] for i in range(3)),
        0.001,
    )
    for i in range(3):
        low, high = result["min"][i], result["max"][i]
        source_low, source_high = source.bounds["min"][i], source.bounds["max"][i]
        tolerance = max(0.001, source_span * 1e-6)
        if (
            low > high
            or low > source_low + tolerance
            or high < source_high - tolerance
            or source_low - low > source_span + tolerance
            or high - source_high > source_span + tolerance
        ):
            raise ValueError("Potree 边界与来源异常不符。")
    return result


def _verify_hierarchy(path: Path, octree_size: int, first_chunk_size: int) -> None:
    size = _regular_file(path)
    if size < _NODE.size or size > _MAX_HIERARCHY or size % _NODE.size:
        raise ValueError("Potree 层级文件大小无效。")
    if first_chunk_size < _NODE.size or first_chunk_size > size or first_chunk_size % _NODE.size:
        raise ValueError("Potree 首层级块无效。")
    geometry_nodes = 0
    with path.open("rb") as handle:
        while record := handle.read(_NODE.size):
            if len(record) != _NODE.size:
                raise ValueError("Potree 层级记录截断。")
            node_type, children, points, offset, byte_size = _NODE.unpack(record)
            empty_inner = node_type == 0 and children != 0 and points == 0 and byte_size == 0
            if node_type not in (0, 1, 2) or (not empty_inner and (points == 0 or byte_size == 0)):
                raise ValueError("Potree 层级节点无效。")
            if node_type == 2:
                if offset % _NODE.size or byte_size % _NODE.size or offset + byte_size > size:
                    raise ValueError("Potree 层级代理偏移越界。")
            else:
                if offset + byte_size > octree_size:
                    raise ValueError("Potree 八叉树数据偏移越界。")
                if byte_size:
                    geometry_nodes += 1
    if geometry_nodes == 0:
        raise ValueError("Potree 层级没有可加载的点数据。")


def _validate_output(source: LasSource, run_dir: Path) -> ValidatedPotreeOutput:
    metadata_file = run_dir / "metadata.json"
    metadata_size = _regular_file(metadata_file)
    octree_size = _regular_file(run_dir / "octree.bin")
    _regular_file(run_dir / "hierarchy.bin")
    if metadata_size == 0 or metadata_size > _MAX_METADATA or octree_size == 0:
        raise ValueError("Potree 元数据或点数据大小无效。")
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Potree 元数据 JSON 无效。") from exc
    if not isinstance(metadata, dict) or metadata.get("version") != "2.0":
        raise ValueError("仅接受 Potree 2.0 元数据。")
    points = metadata.get("points")
    if not isinstance(points, int) or isinstance(points, bool) or points != source.point_count:
        raise ValueError("Potree 点数与来源不符。")
    if metadata.get("encoding") not in ("DEFAULT", "BROTLI"):
        raise ValueError("Potree 编码不受支持。")
    attributes = metadata.get("attributes")
    if not isinstance(attributes, list) or not any(
        isinstance(item, dict) and item.get("name") == "position" for item in attributes
    ):
        raise ValueError("Potree 位置属性缺失。")
    if not any(
        isinstance(item, dict)
        and item.get("name") in ("rgb", "rgba")
        and isinstance(item.get("size"), int)
        and item["size"] >= 3
        for item in attributes
    ):
        raise ValueError("Potree RGB 属性缺失。")
    bounds = _bounds(metadata, source)
    hierarchy = metadata.get("hierarchy")
    if not isinstance(hierarchy, dict):
        raise ValueError("Potree 层级元数据缺失。")
    first_chunk = hierarchy.get("firstChunkSize")
    if not isinstance(first_chunk, int) or isinstance(first_chunk, bool):
        raise ValueError("Potree 首层级块大小无效。")
    _verify_hierarchy(run_dir / "hierarchy.bin", octree_size, first_chunk)
    return ValidatedPotreeOutput(run_dir, metadata_file, points, bounds, True)


def convert_and_validate(
    source: LasSource, converter: Path, run_dir: Path
) -> ValidatedPotreeOutput:
    """只在新目录调用转换器；任一门禁失败均不返回可发布结果。"""

    converter = Path(converter)
    if not converter.is_file():
        raise ValueError("转换器路径无效或文件缺失。")
    run_dir = Path(run_dir)
    if run_dir.exists() or run_dir.is_symlink():
        raise ValueError("运行目录必须是新目录。")
    _source_unchanged(source)
    run_dir.mkdir(parents=True, exist_ok=False)
    command = [str(converter), "-i", str(source.path), "-o", str(run_dir)]
    with (run_dir / "converter.log").open("wb") as log:
        try:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=_CONVERSION_TIMEOUT,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("转换器启动失败或超时；运行目录保留供排查。") from exc
    if result.returncode != 0:
        raise RuntimeError(f"转换器退出码 {result.returncode}；运行目录保留供排查。")
    _source_unchanged(source)
    return _validate_output(source, run_dir)
