"""完整 LAS 预览的只读来源检查。"""

import hashlib
import importlib
import struct
from pathlib import Path

import pytest


def _small_las() -> bytes:
    header = bytearray(227)
    header[:4] = b"LASF"
    header[24:26] = bytes((1, 2))
    struct.pack_into("<H", header, 94, 227)
    struct.pack_into("<I", header, 96, 227)
    header[104] = 2
    struct.pack_into("<H", header, 105, 26)
    struct.pack_into("<I", header, 107, 2)
    struct.pack_into("<3d", header, 131, 0.01, 0.01, 0.01)
    struct.pack_into("<3d", header, 155, 0.0, 0.0, 0.0)
    struct.pack_into("<6d", header, 179, 2.0, 0.0, 3.0, 1.0, 4.0, 2.0)
    return bytes(header) + bytes(52)


def test_large_las_is_inspected_without_whole_file_read(tmp_path, monkeypatch):
    source_path = tmp_path / "machine zoom.las"
    content = _small_las()
    source_path.write_bytes(content)
    module = importlib.import_module("pc_system.full_preview_source")

    original_read_bytes = Path.read_bytes

    def reject_whole_file_read(path):
        if path == source_path:
            raise AssertionError("不允许通过 Path.read_bytes 读取整份 LAS")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_whole_file_read)
    result = module.inspect_las_source(source_path, "machine-zoom")

    assert result.path == source_path.resolve()
    assert result.asset_id == "machine-zoom"
    assert result.size == len(content)
    assert result.point_count == 2
    assert result.point_format == 2
    assert result.source_format == "las"
    assert result.representation_kind == "point_cloud"
    assert result.unit == "m"
    assert result.bounds == {"min": [0.0, 1.0, 2.0], "max": [2.0, 3.0, 4.0]}
    assert result.sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.__setitem__(slice(0, 4), b"NOPE"), "签名"),
        (lambda data: data.__delitem__(slice(230, None)), "范围"),
        (lambda data: data.__setitem__(104, 0x82), "未压缩"),
    ],
)
def test_invalid_las_is_rejected(tmp_path, mutate, message):
    source_path = tmp_path / "bad.las"
    content = bytearray(_small_las())
    mutate(content)
    source_path.write_bytes(content)
    module = importlib.import_module("pc_system.full_preview_source")
    with pytest.raises(ValueError, match=message):
        module.inspect_las_source(source_path, "machine-zoom")


def test_explicit_asset_id_must_be_safe_even_if_file_name_has_spaces(tmp_path):
    source_path = tmp_path / "machine zoom.las"
    source_path.write_bytes(_small_las())
    module = importlib.import_module("pc_system.full_preview_source")
    with pytest.raises(ValueError, match="asset_id"):
        module.inspect_las_source(source_path, "machine zoom")
