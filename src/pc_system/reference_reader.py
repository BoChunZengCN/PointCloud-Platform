"""从已冻结的普通文件读取参考点云；解析与标准化可放入有界子进程。"""

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.reference_geometry import (
    MAXIMUM_POINTS,
    assess_reference_quality,
    normalize_reference_points,
)


MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_RESULT_BYTES = 512 * 1024 * 1024
_MAX_HEADER_BYTES = 64 * 1024
_PLY_TYPES = {
    "char": ("b", -128, 127), "int8": ("b", -128, 127),
    "uchar": ("B", 0, 255), "uint8": ("B", 0, 255),
    "short": ("h", -32768, 32767), "int16": ("h", -32768, 32767),
    "ushort": ("H", 0, 65535), "uint16": ("H", 0, 65535),
    "int": ("i", -(2**31), 2**31 - 1), "int32": ("i", -(2**31), 2**31 - 1),
    "uint": ("I", 0, 2**32 - 1), "uint32": ("I", 0, 2**32 - 1),
    "float": ("f", None, None), "float32": ("f", None, None),
    "double": ("d", None, None), "float64": ("d", None, None),
}
_UNIT_SCALE = {"mm": 0.001, "cm": 0.01, "m": 1.0}


def _invalid(message="扫描文件结构或属性无效。"):
    return ModelMatchingError("reference_format_invalid", message)


def _limit(message="扫描文件超过 limit 限制。"):
    return ModelMatchingError("reference_input_limit", message)


def _validate_options(declared_unit, maximum_points):
    if type(declared_unit) is not str or declared_unit not in _UNIT_SCALE:
        raise ModelMatchingError("reference_unit_unsupported", "请明确声明 mm、cm 或 m。")
    if type(maximum_points) is not int or not 1 <= maximum_points <= MAXIMUM_POINTS:
        raise ModelMatchingError("reference_limit_invalid", "点数上限无效。")


def _ply_header(stream, maximum_points):
    if stream.readline(16).strip() != b"ply":
        raise _invalid("文件签名不是 PLY。")
    consumed, encoding, current, vertex = 4, None, None, None
    elements = set()
    while True:
        raw = stream.readline(4097)
        consumed += len(raw)
        if not raw or len(raw) > 4096 or consumed > _MAX_HEADER_BYTES:
            raise _invalid("PLY 文件头缺失或过大。")
        try:
            fields = raw.decode("ascii").strip().split()
        except UnicodeDecodeError as exc:
            raise _invalid() from exc
        if not fields:
            raise _invalid()
        if fields[0] in {"comment", "obj_info"}:
            continue
        if fields == ["end_header"]:
            break
        if fields[0] == "format":
            if encoding is not None or len(fields) != 3 or fields[2] != "1.0" or fields[1] not in {"ascii", "binary_little_endian", "binary_big_endian"}:
                raise _invalid("不支持的 PLY 编码。")
            encoding = fields[1]
        elif fields[0] == "element":
            if encoding is None or len(fields) != 3 or fields[1] in elements:
                raise _invalid()
            try:
                count = int(fields[2])
            except ValueError as exc:
                raise _invalid() from exc
            if count < 0:
                raise _invalid()
            if fields[1] == "vertex" and count > maximum_points:
                raise _limit()
            if fields[1] != "vertex" and count != 0:
                raise _invalid("扫描入口不接受非空网格面或其他元素。")
            current = {"name": fields[1], "count": count, "properties": []}
            elements.add(fields[1])
            if fields[1] == "vertex":
                vertex = current
        elif fields[0] == "property":
            if current is None:
                raise _invalid()
            if current["name"] != "vertex":
                # 零元素的属性只校验声明，不读取不存在的记录。
                scalar = len(fields) == 3 and fields[1] in _PLY_TYPES
                sequence = len(fields) == 5 and fields[1] == "list" and fields[2] in _PLY_TYPES and fields[3] in _PLY_TYPES
                if not (scalar or sequence):
                    raise _invalid()
                continue
            properties = current["properties"]
            if len(fields) != 3 or fields[1] not in _PLY_TYPES or len(properties) >= 128 or fields[2] in {name for _, name in properties}:
                raise _invalid("PLY 顶点必须使用不重复的标量属性。")
            properties.append((fields[1], fields[2]))
        else:
            raise _invalid("未知的 PLY 文件头声明。")
    if encoding is None or vertex is None or vertex["count"] <= 0 or not {"x", "y", "z"} <= {name for _, name in vertex["properties"]}:
        raise _invalid("PLY 需要非空 XYZ 顶点。")
    return encoding, vertex


def _read_ply(stream, maximum_points):
    encoding, vertex = _ply_header(stream, maximum_points)
    properties = vertex["properties"]
    indices = [next(i for i, (_, name) in enumerate(properties) if name == axis) for axis in ("x", "y", "z")]
    formats = [_PLY_TYPES[kind] for kind, _ in properties]
    unpacker = struct.Struct(("<" if encoding == "binary_little_endian" else ">") + "".join(item[0] for item in formats))
    points = []
    for _ in range(vertex["count"]):
        try:
            if encoding == "ascii":
                line = stream.readline(16385)
                if len(line) > 16384:
                    raise _invalid("PLY 顶点行过长。")
                parts = line.decode("ascii").split()
                if len(parts) != len(properties):
                    raise _invalid("PLY 顶点属性数量不一致。")
                values = []
                for part, (kind, low, high) in zip(parts, formats):
                    value = float(part) if low is None else int(part)
                    if low is not None and not low <= value <= high:
                        raise _invalid("PLY 整数属性超出范围。")
                    # float 属性按其声明类型验证可表示性，不接受溢出文本。
                    if low is None:
                        struct.pack("<" + kind, value)
                    values.append(value)
            else:
                values = unpacker.unpack(stream.read(unpacker.size))
            if not all(math.isfinite(float(value)) for value in values):
                raise _invalid("PLY 存在非有限属性。")
            points.append([float(values[index]) for index in indices])
        except (UnicodeError, ValueError, OverflowError, struct.error) as exc:
            raise _invalid("PLY 顶点损坏或文件被截断。") from exc
    tail = stream.read(1)
    if encoding == "ascii":
        # 限制为仅可有空白尾部，不能静默忽略额外点。
        while tail and tail in b" \t\r\n":
            tail = stream.read(1)
    if tail:
        raise _invalid("PLY 实际顶点数或尾部与声明不一致。")
    return points, {"name": "pc-system-ply", "version": "1", "format": "ply", "encoding": encoding, "crs": None}


def _read_las(stream, suffix, declared_unit, maximum_points, file_size):
    try:
        import laspy
        import pyproj
    except ImportError as exc:
        raise ModelMatchingError("reference_reader_unavailable", "缺少 LAS/LAZ 或 CRS 读取依赖。") from exc
    if stream.read(4) != b"LASF":
        raise _invalid("文件签名不是 LAS/LAZ。")
    stream.seek(0)
    try:
        with laspy.open(stream, closefd=False) as source:
            header = source.header
            compressed = header.are_points_compressed
            if compressed != (suffix == ".laz"):
                raise _invalid("文件扩展名与 LAS 压缩标志不一致。")
            if not 0 < header.point_count <= maximum_points:
                raise _limit()
            if compressed and not laspy.LazBackend.detect_available():
                raise ModelMatchingError("reference_reader_unavailable", "缺少真实 LAZ 解码器。")
            count_validation = "record_bytes_v1"
            if compressed:
                from .reference_laz import validate_layered_count
                count_validation = validate_layered_count(stream, header, file_size, maximum_points)
            if not compressed:
                boundaries = [file_size]
                for position in (header.start_of_waveform_data_packet_record, header.start_of_first_evlr):
                    if position:
                        boundaries.append(position)
                end = min(boundaries)
                if end - header.offset_to_point_data != header.point_count * header.point_format.size:
                    raise _invalid("LAS 点记录字节数与声明不一致。")
            crs = header.parse_crs()
            if crs is not None:
                if crs.is_geographic or not (crs.is_projected or crs.is_engineering):
                    raise ModelMatchingError("reference_crs_unsupported", "地理或非线性坐标须先转换为线性坐标。")
                axes = crs.axis_info
                if len(axes) < 2 or any(not math.isclose(axis.unit_conversion_factor, _UNIT_SCALE[declared_unit], rel_tol=1e-12) for axis in axes):
                    raise ModelMatchingError("reference_unit_conflict", "文件 CRS 单位与声明单位冲突。")
            points = []
            for chunk in source.chunk_iterator(min(65536, maximum_points)):
                if len(points) + len(chunk) > maximum_points:
                    raise _limit()
                points.extend([[float(x), float(y), float(z)] for x, y, z in zip(chunk.x, chunk.y, chunk.z)])
            if len(points) != header.point_count:
                raise _invalid("LAS/LAZ 实际解码点数与声明不一致。")
            return points, {"name": "laspy", "version": laspy.__version__, "format": suffix[1:], "crs": crs.to_wkt() if crs is not None else None, "crs_reader_version": pyproj.__version__, "count_validation": count_validation}
    except ModelMatchingError:
        raise
    except (Exception,) as exc:
        # 第三方解码器的损坏输入异常不向调用方泄露内部栈或路径。
        raise _invalid("LAS/LAZ 解码失败，文件可能损坏。") from exc


def read_reference_file(path: Path, *, declared_unit: str, maximum_points: int = MAXIMUM_POINTS) -> dict:
    """读取器入口只消费导入器已冻结的文件；本函数不承担源复制事务。"""
    _validate_options(declared_unit, maximum_points)
    path = Path(path)
    if path.suffix.lower() not in {".ply", ".las", ".laz"}:
        raise ModelMatchingError("reference_format_unsupported", "仅支持 LAS、LAZ 和点式 PLY。")
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise _invalid("输入必须是普通文件。")
        if info.st_size > MAX_SOURCE_BYTES:
            raise _limit()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise _invalid("源文件身份在读取前改变。")
            if path.suffix.lower() == ".ply":
                points, metadata = _read_ply(stream, maximum_points)
            else:
                points, metadata = _read_las(stream, path.suffix.lower(), declared_unit, maximum_points, info.st_size)
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            after = os.fstat(stream.fileno())
            if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                raise _invalid("冻结源在读取期间被改变。")
    except OSError as exc:
        raise _invalid("无法读取扫描源文件。") from exc
    normalized = normalize_reference_points(points, declared_unit=declared_unit, maximum_points=maximum_points)
    return {"source_fingerprint": digest, "source_bytes": info.st_size, "reader": metadata, "normalized": normalized, "quality": assess_reference_quality(normalized)}


def decode_reference_file(path: Path, *, declared_unit: str, timeout_seconds: float = 120, maximum_points: int = MAXIMUM_POINTS) -> dict:
    """使用本服务自己的 Python 子进程；无 shell，Windows 不创建可见窗口。"""
    _validate_options(declared_unit, maximum_points)
    if type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
        raise ModelMatchingError("reference_limit_invalid", "解析时限无效。")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    command = [sys.executable, "-m", "pc_system.reference_worker", str(Path(path).resolve()), declared_unit, str(maximum_points)]
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_seconds, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except subprocess.TimeoutExpired as exc:
        raise ModelMatchingError("reference_decode_timeout", "扫描解析超时，工作进程已终止。") from exc
    except OSError as exc:
        raise ModelMatchingError("reference_reader_unavailable", "无法启动扫描解析工作进程。") from exc
    if len(process.stdout) > MAX_RESULT_BYTES:
        raise _limit("扫描解析输出超过 limit 限制。")
    try:
        result = json.loads(process.stdout)
        if type(result) is not dict or type(result.get("ok")) is not bool:
            raise ValueError("invalid worker response")
        if not result["ok"]:
            if type(result.get("code")) is not str or not result["code"].startswith("reference_") or type(result.get("message")) is not str:
                raise ValueError("invalid worker error")
            raise ModelMatchingError(result["code"], result["message"])
        if process.returncode != 0 or type(result.get("result")) is not dict:
            raise ValueError("invalid worker result")
        return result["result"]
    except (ValueError, UnicodeError) as exc:
        raise _invalid("扫描工作进程未返回完整结果。") from exc
