"""首版 LAZ 子集：固定分层块、点格式 6/7/8、无附加字节。"""

import io
import struct

from .model_matching_errors import ModelMatchingError


def _invalid():
    return ModelMatchingError("reference_format_invalid", "LAZ 文件头、分层块与块表不一致。")


def validate_layered_count(stream, header, file_size, maximum_points):
    """在真实解码前独立核对每块点数和字节边界，不以请求解码数充当证据。"""
    try:
        import lazrs
    except ImportError as exc:
        raise ModelMatchingError("reference_reader_unavailable", "缺少 LAZ 块表读取依赖。") from exc
    unsupported = ModelMatchingError(
        "reference_laz_variant_unsupported",
        "首版仅支持无附加字节的点格式 6/7/8 固定分层 LAZ；请先转换为 LAS。",
    )
    layouts = {6: (30, [(10, 30, 3)], 9), 7: (36, [(10, 30, 3), (11, 6, 3)], 10),
               8: (38, [(10, 30, 3), (12, 8, 3)], 11)}
    if header.point_format.id not in layouts or str(header.version) != "1.4":
        raise unsupported
    size, expected_items, layers = layouts[header.point_format.id]
    records = header.vlrs.get("LasZipVlr")
    if len(records) != 1:
        raise _invalid()
    raw = records[0].record_data
    if len(raw) < 34:
        raise _invalid()
    compressor, coder = struct.unpack_from("<HH", raw)
    options, chunk_size = struct.unpack_from("<II", raw, 8)
    item_count = struct.unpack_from("<H", raw, 32)[0]
    if len(raw) != 34 + 6 * item_count:
        raise _invalid()
    items = list(struct.iter_unpack("<HHH", raw[34:]))
    if (compressor, coder, options) != (3, 0, 0) or items != expected_items or header.point_format.size != size or chunk_size in (0, 0xFFFFFFFF):
        raise unsupported
    vlr = lazrs.LazVlr(raw)
    start = header.offset_to_point_data
    if bool(header.start_of_first_evlr) != bool(header.number_of_evlrs):
        raise _invalid()
    end = min([file_size] + [value for value in (header.start_of_first_evlr, header.start_of_waveform_data_packet_record) if value])
    stream.seek(start)
    table = struct.unpack("<q", stream.read(8))[0]
    # 暂不接受 EOF 回填表指针；明确拒绝，而不是猜测块边界。
    if table == -1:
        raise unsupported
    if not start + 8 < table <= end - 8:
        raise _invalid()
    stream.seek(table)
    version, count = struct.unpack("<II", stream.read(8))
    expected_count = (header.point_count + chunk_size - 1) // chunk_size
    if version != 0 or not 0 < count <= maximum_points or count != expected_count:
        raise _invalid()
    stream.seek(table)
    # 原生块表解码只拿到点数据区尾部，不能把 EVLR/波形字节当作表内容。
    chunks = lazrs.read_chunk_table_only(io.BytesIO(stream.read(end - table)), vlr)
    if len(chunks) != count:
        raise _invalid()
    position, total = start + 8, 0
    layer_header_size = size + 4 * (layers + 1)
    for index, (_, byte_count) in enumerate(chunks):
        if byte_count < layer_header_size or position + byte_count > table:
            raise _invalid()
        stream.seek(position + size)
        values = struct.unpack("<" + "I" * (layers + 1), stream.read(4 * (layers + 1)))
        points, *layer_sizes = values
        if not 0 < points <= chunk_size or (index < count - 1 and points != chunk_size):
            raise _invalid()
        if layer_header_size + sum(layer_sizes) != byte_count:
            raise _invalid()
        total += points
        if total > maximum_points:
            raise ModelMatchingError("reference_input_limit", "LAZ 实际块点数超过 limit 限制。")
        position += byte_count
    if position != table or total != header.point_count:
        raise _invalid()
    # laspy 还未创建解码器，恢复其预期的点数据位置。
    stream.seek(start)
    return "layered_chunks_v1"
