"""使用实际格式字节和真实子进程验证扫描读取边界。"""

import importlib
import struct
import sys

import numpy as np
import pytest

from pc_system.model_matching_errors import ModelMatchingError


def reader():
    assert importlib.util.find_spec("pc_system.reference_reader") is not None, "缺少扫描文件读取模块"
    return importlib.import_module("pc_system.reference_reader")


def make_ply(path, *, encoding="ascii", count=64, extra_header="", truncate=False):
    points = [[x, y, z] for x in range(4) for y in range(4) for z in range(4)]
    header = f"ply\nformat {encoding} 1.0\nelement vertex {count}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\n{extra_header}end_header\n".encode("ascii")
    if encoding == "ascii":
        body = "".join(f"{x} {y} {z} 255\n" for x, y, z in points).encode("ascii")
    else:
        endian = "<" if encoding == "binary_little_endian" else ">"
        body = b"".join(struct.pack(endian + "fffB", *point, 255) for point in points)
    path.write_bytes(header + (body[:-7] if truncate else body))
    return path


def make_las(path, *, crs=None, point_format=None):
    import laspy
    point_format = (6 if path.suffix == ".laz" else 0) if point_format is None else point_format
    header = laspy.LasHeader(point_format=point_format, version="1.4" if point_format >= 6 else "1.2")
    header.scales = np.array([0.01, 0.01, 0.01])
    header.offsets = np.array([1000, 2000, 3000])
    if crs is not None:
        from pyproj import CRS
        header.add_crs(CRS.from_epsg(crs))
    data = laspy.LasData(header)
    points = np.array([[x + 1000, y + 2000, z + 3000] for x in range(4) for y in range(4) for z in range(4)])
    data.x, data.y, data.z = points[:, 0], points[:, 1], points[:, 2]
    data.write(path)
    return path


@pytest.mark.parametrize("encoding", ["ascii", "binary_little_endian", "binary_big_endian"])
def test_real_ply_encodings_produce_the_same_xyz_without_using_color(tmp_path, encoding):
    result = reader().read_reference_file(make_ply(tmp_path / "box.ply", encoding=encoding), declared_unit="m", maximum_points=64)
    assert result["normalized"]["points"][0] == [-1.5, -1.5, -1.5]
    assert result["normalized"]["point_count"] == 64
    assert result["quality"]["status"] == "passed"
    assert result["reader"]["format"] == "ply"


@pytest.mark.parametrize("extension", ["las", "laz"])
def test_real_las_laz_decodes_scale_offset_and_declared_unit_once(tmp_path, extension):
    result = reader().read_reference_file(make_las(tmp_path / f"box.{extension}"), declared_unit="mm", maximum_points=64)
    normal = result["normalized"]
    assert normal["source_origin_m"] == pytest.approx([1.0015, 2.0015, 3.0015])
    assert normal["points"][0] == [-0.0015, -0.0015, -0.0015]
    assert normal["dimensions_m"] == [0.003, 0.003, 0.003]
    assert result["reader"]["format"] == extension
    assert result["reader"]["name"] == "laspy"


@pytest.mark.parametrize("encoding", ["ascii", "binary_little_endian", "binary_big_endian"])
def test_truncated_ply_is_rejected(tmp_path, encoding):
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(make_ply(tmp_path / "bad.ply", encoding=encoding, truncate=True), declared_unit="m", maximum_points=64)
    assert caught.value.code == "reference_format_invalid"


@pytest.mark.parametrize("count", [63, 65, 1_000_001])
def test_false_ply_count_or_point_limit_cannot_silently_drop_data(tmp_path, count):
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(make_ply(tmp_path / "bad.ply", count=count), declared_unit="m", maximum_points=64)
    assert caught.value.code in {"reference_format_invalid", "reference_input_limit"}


@pytest.mark.parametrize("replacement", [
    (b"property float z", b"property float height"),
    (b"property float z", b"property float x"),
    (b"property float z", b"property list uchar float z"),
    (b"end_header", b"element face 1\nproperty list uchar int vertex_indices\nend_header"),
    (b"0 0 0 255", b"nan 0 0 255"),
    (b"0 0 0 255", b"0 0 0 999"),
])
def test_invalid_ply_properties_or_mesh_data_are_not_accepted_as_a_scan(tmp_path, replacement):
    path = make_ply(tmp_path / "bad.ply")
    path.write_bytes(path.read_bytes().replace(*replacement))
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(path, declared_unit="m", maximum_points=64)
    assert caught.value.code in {"reference_format_invalid", "reference_points_invalid"}


@pytest.mark.parametrize("crs,unit,code", [(4326, "m", "reference_crs_unsupported"), (32650, "cm", "reference_unit_conflict")])
def test_crs_is_not_silently_reinterpreted_as_declared_linear_units(tmp_path, crs, unit, code):
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(make_las(tmp_path / "crs.las", crs=crs), declared_unit=unit, maximum_points=64)
    assert caught.value.code == code


def test_projected_meter_crs_is_accepted_without_projection(tmp_path):
    result = reader().read_reference_file(make_las(tmp_path / "crs.las", crs=32650), declared_unit="m", maximum_points=64)
    assert result["normalized"]["source_origin_m"] == [1001.5, 2001.5, 3001.5]


def test_uncompressed_las_low_count_cannot_hide_extra_point_records(tmp_path):
    path = make_las(tmp_path / "bad.las")
    payload = bytearray(path.read_bytes())
    struct.pack_into("<I", payload, 107, 63)
    path.write_bytes(payload)
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(path, declared_unit="m", maximum_points=64)
    assert caught.value.code == "reference_format_invalid"


def test_fixed_chunk_laz_underreported_count_cannot_be_claimed_independently_verified(tmp_path):
    """规格缺口回归：压缩末块缺少独立点数，不能以请求数证明完整性。"""
    import laspy
    data = laspy.LasData(laspy.LasHeader(point_format=0, version="1.2"))
    points = np.array([[x, y, z] for x in range(5) for y in range(5) for z in range(5)])
    data.x, data.y, data.z = points[:, 0], points[:, 1], points[:, 2]
    path = tmp_path / "underreported.laz"
    data.write(path)
    payload = bytearray(path.read_bytes())
    struct.pack_into("<I", payload, 107, 124)
    path.write_bytes(payload)
    with pytest.raises(ModelMatchingError):
        reader().read_reference_file(path, declared_unit="m", maximum_points=125)


@pytest.mark.parametrize("point_format", [6, 7, 8])
def test_layered_laz_records_independent_count_validation(tmp_path, point_format):
    path = make_las(tmp_path / "box.laz", point_format=point_format)
    result = reader().read_reference_file(path, declared_unit="m")
    assert result["reader"]["count_validation"] == "layered_chunks_v1"
    assert result["normalized"]["point_count"] == 64


def test_legacy_laz_is_explicitly_rejected_with_conversion_guidance(tmp_path):
    path = make_las(tmp_path / "legacy.laz", point_format=0)
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(path, declared_unit="m")
    assert caught.value.code == "reference_laz_variant_unsupported"
    assert "LAS" in str(caught.value)


@pytest.mark.parametrize("field", ["header_count", "chunk_count", "layer_bytes", "table_position", "table_count", "truncated"])
def test_layered_laz_rejects_disagreement_between_header_chunks_and_table(tmp_path, field):
    path = make_las(tmp_path / "bad.laz")
    payload = bytearray(path.read_bytes())
    start = struct.unpack_from("<I", payload, 96)[0]
    table = struct.unpack_from("<q", payload, start)[0]
    positions = {"header_count": ("<Q", 247, 63), "chunk_count": ("<I", start + 8 + 30, 63),
                 "layer_bytes": ("<I", start + 8 + 30 + 4, 0),
                 "table_position": ("<q", start, len(payload) + 100),
                 "table_count": ("<I", table + 4, 1_000_001)}
    if field == "truncated":
        payload = payload[:table + 9]
    else:
        struct.pack_into(*positions[field][:1], payload, *positions[field][1:])
    path.write_bytes(payload)
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(path, declared_unit="m")
    assert caught.value.code in {"reference_format_invalid", "reference_input_limit"}


def test_layered_laz_checks_each_chunk_including_partial_last_chunk(tmp_path):
    import laspy
    path = tmp_path / "multi.laz"
    data = laspy.LasData(laspy.LasHeader(point_format=6, version="1.4"))
    values = np.arange(50_064)
    data.x, data.y, data.z = values % 64, values // 64, values % 17
    data.write(path)
    result = reader().read_reference_file(path, declared_unit="m")
    assert result["normalized"]["input_point_count"] == 50_064
    payload = bytearray(path.read_bytes())
    struct.pack_into("<Q", payload, 247, 50_063)
    path.write_bytes(payload)
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(path, declared_unit="m")
    assert caught.value.code == "reference_format_invalid"


def test_laz_chunk_table_must_not_consume_evlr_header_bytes(tmp_path):
    path = make_las(tmp_path / "overlap.laz")
    payload = bytearray(path.read_bytes())
    start = struct.unpack_from("<I", payload, 96)[0]
    table = struct.unpack_from("<q", payload, start)[0]
    struct.pack_into("<Q", payload, 235, table + 8)
    struct.pack_into("<I", payload, 243, 1)
    payload.extend(bytes(60))
    path.write_bytes(payload)
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(path, declared_unit="m")
    assert caught.value.code == "reference_format_invalid"


def test_missing_decoder_dependency_has_a_stable_failure(tmp_path, monkeypatch):
    path = make_las(tmp_path / "box.las")
    monkeypatch.setitem(sys.modules, "laspy", None)
    with pytest.raises(ModelMatchingError) as caught:
        reader().read_reference_file(path, declared_unit="m", maximum_points=64)
    assert caught.value.code == "reference_reader_unavailable"


def test_source_file_size_is_checked_before_parsing(tmp_path, monkeypatch):
    module = reader()
    path = make_ply(tmp_path / "box.ply")
    monkeypatch.setattr(module, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(ModelMatchingError) as caught:
        module.read_reference_file(path, declared_unit="m", maximum_points=64)
    assert caught.value.code == "reference_input_limit"


def test_real_worker_process_returns_same_frozen_geometry(tmp_path):
    module = reader()
    path = make_ply(tmp_path / "box.ply")
    direct = module.read_reference_file(path, declared_unit="m", maximum_points=64)
    assert module.decode_reference_file(path, declared_unit="m", maximum_points=64) == direct


def test_real_worker_timeout_terminates_without_a_partial_success(tmp_path):
    path = make_ply(tmp_path / "box.ply")
    with pytest.raises(ModelMatchingError) as caught:
        reader().decode_reference_file(path, declared_unit="m", maximum_points=64, timeout_seconds=0.00001)
    assert caught.value.code == "reference_decode_timeout"


def test_worker_preserves_domain_rejection_instead_of_a_success_payload(tmp_path):
    path = make_ply(tmp_path / "bad.ply", truncate=True)
    with pytest.raises(ModelMatchingError) as caught:
        reader().decode_reference_file(path, declared_unit="m", maximum_points=64)
    assert caught.value.code == "reference_format_invalid"
