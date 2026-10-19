"""网格工作器的版本化请求、结果和普通文件完整性边界。"""

import hashlib
import json
import math
import os
import stat
import struct
from pathlib import Path, PurePosixPath
from typing import Protocol

from pc_system.mesh_reconstruction_config import build_mesh_config
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_release import _require_plain
from pc_system.model_sampling import _canonical_json_bytes, _publish_exact_json
from pc_system.scan_to_mesh_errors import ScanToMeshError


_CONFIG_FIELDS = {
    "schema_version",
    "algorithm",
    "worker_version",
    "normal_estimation",
    "voxel_size_m",
    "poisson",
    "ball_pivoting",
    "repair",
    "preview",
    "limits",
    "random_seed",
}
_REQUEST_FIELDS = {
    "schema_version",
    "source_snapshot_fingerprint",
    "config_id",
    "config_fingerprint",
    "algorithm",
    "worker_version",
    "input",
    "parameters",
    "limits",
    "request_fingerprint",
}
_INPUT_FIELDS = {"path", "bytes", "sha256", "point_count"}
_PARAMETER_FIELDS = {
    "normal_estimation",
    "voxel_size_m",
    "poisson",
    "ball_pivoting",
    "repair",
    "preview",
    "random_seed",
}
_RESULT_FIELDS = {
    "schema_version",
    "status",
    "request_fingerprint",
    "algorithm",
    "worker_version",
    "artifacts",
    "metrics_fingerprint",
}
_PLY_DESCRIPTOR_FIELDS = {
    "path",
    "bytes",
    "sha256",
    "vertex_count",
    "triangle_count",
}
_JSON_DESCRIPTOR_FIELDS = {"path", "bytes", "sha256"}
_ARTIFACT_NAMES = {
    "raw_mesh": "raw_mesh.ply",
    "repaired_mesh": "repaired_mesh.ply",
    "preview_mesh": "preview_mesh.glb",
    "mesh_metrics": "mesh_metrics.json",
}
_METRIC_FIELDS = {
    "schema_version",
    "raw_vertex_count",
    "raw_triangle_count",
    "repaired_vertex_count",
    "repaired_triangle_count",
    "non_finite_vertex_count",
    "non_finite_face_count",
    "duplicate_vertex_count",
    "degenerate_triangle_count",
    "non_manifold_edge_count",
    "boundary_edge_count",
    "component_count",
    "largest_component_fraction",
    "normal_consistency_fraction",
    "point_to_mesh_p50_m",
    "point_to_mesh_p95_m",
    "point_to_mesh_max_m",
    "mesh_to_point_coverage",
    "repair_max_displacement_m",
}
_MAXIMUM_CONTROL_BYTES = 16 * 1024 * 1024


class MeshWorker(Protocol):
    def __call__(self, request: dict, workspace: Path) -> dict:
        """执行固定请求，并返回经过核心验证的结果清单。"""


def _error(code: str, message: str) -> ScanToMeshError:
    return ScanToMeshError(code, message)


def _compact_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _error(
            "mesh_worker_request_invalid", "工作器请求不能规范编码。"
        ) from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_compact_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _is_reparse_point(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(info, "st_file_attributes", 0) & flag)


def _read_plain_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse_point(before)
            or before.st_size > maximum_bytes
        ):
            raise ValueError("unsafe artifact")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > maximum_bytes
                or (opened.st_dev, opened.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                raise ValueError("artifact changed")
            chunks = []
            remaining = maximum_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > maximum_bytes:
                raise ValueError("artifact too large")
            return payload
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as exc:
        raise _error(
            "mesh_worker_artifact_invalid", "工作器工件不是受控普通文件。"
        ) from exc


def _read_json(path: Path, *, maximum_bytes: int, code: str) -> dict:
    payload = _read_plain_bytes(path, maximum_bytes=maximum_bytes)
    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
            object_pairs_hook=_strict_object,
        )
        if type(value) is not dict or payload != _canonical_json_bytes(value):
            raise ValueError("noncanonical JSON")
        return value
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _error(code, "工作器 JSON 工件无效。") from exc


def _fixed_relative_path(value: object, expected: str) -> str:
    if type(value) is not str or value != expected:
        raise _error(
            "mesh_worker_result_invalid", "工作器工件路径必须是固定相对标识。"
        )
    pure = PurePosixPath(value)
    if pure.is_absolute() or len(pure.parts) != 1 or pure.parts[0] in {".", ".."}:
        raise _error(
            "mesh_worker_result_invalid", "工作器工件路径不能逃逸工作目录。"
        )
    return value


def _validated_config(config: object) -> dict:
    if type(config) is not dict or type(config.get("config_id")) is not str:
        raise _error("mesh_worker_request_invalid", "缺少已发布网格配置。")
    try:
        payload = {key: config[key] for key in _CONFIG_FIELDS}
        canonical = build_mesh_config(config["config_id"], payload)
    except (KeyError, ModelMatchingError) as exc:
        raise _error("mesh_worker_request_invalid", "网格配置无效。") from exc
    if any(config.get(key) != value for key, value in canonical.items()):
        raise _error("mesh_worker_request_invalid", "网格配置指纹不一致。")
    return canonical


def _validated_source(source_snapshot: object, maximum_points: int) -> dict:
    if type(source_snapshot) is not dict:
        raise _error("mesh_worker_request_invalid", "缺少来源快照。")
    value = dict(source_snapshot)
    fingerprint = value.pop("fingerprint", None)
    points = value.get("points")
    if (
        not _is_sha256(fingerprint)
        or _digest(value) != fingerprint
        or value.get("schema_version") != "1.0"
        or value.get("coordinate_unit") != "m"
        or type(points) is not list
        or value.get("point_count") != len(points)
        or not 3 <= len(points) <= maximum_points
    ):
        raise _error("mesh_worker_request_invalid", "来源快照无效或超过配置上限。")
    for point in points:
        if (
            type(point) is not list
            or len(point) != 3
            or any(
                type(coordinate) not in {int, float}
                or not math.isfinite(float(coordinate))
                for coordinate in point
            )
        ):
            raise _error("mesh_worker_request_invalid", "来源点坐标无效。")
    return source_snapshot


def prepare_mesh_worker_request(
    workspace: Path, *, source_snapshot: dict, config: dict
) -> dict:
    """写入固定输入工件并返回不可变工作器请求。"""

    workspace = Path(workspace)
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        _require_plain(workspace, directory=True)
    except (ModelMatchingError, OSError) as exc:
        raise _error("mesh_worker_request_invalid", "工作目录无效。") from exc
    canonical_config = _validated_config(config)
    source = _validated_source(
        source_snapshot,
        canonical_config["limits"]["maximum_input_points"],
    )
    points_document = {
        "schema_version": "1.0",
        "coordinate_unit": "m",
        "source_snapshot_fingerprint": source["fingerprint"],
        "points": source["points"],
    }
    points_payload = _canonical_json_bytes(points_document)
    if len(points_payload) > canonical_config["limits"]["maximum_artifact_bytes"]:
        raise _error("mesh_worker_limit_exceeded", "来源点工件超过配置上限。")
    points_path = workspace / "source_points.json"
    _publish_exact_json(
        points_path,
        points_document,
        conflict_code="mesh_worker_request_invalid",
        conflict_message="来源点工件与已有内容冲突。",
    )
    request_payload = {
        "schema_version": "1.0",
        "source_snapshot_fingerprint": source["fingerprint"],
        "config_id": canonical_config["config_id"],
        "config_fingerprint": canonical_config["config_fingerprint"],
        "algorithm": canonical_config["algorithm"],
        "worker_version": canonical_config["worker_version"],
        "input": {
            "path": "source_points.json",
            "bytes": len(points_payload),
            "sha256": hashlib.sha256(points_payload).hexdigest(),
            "point_count": len(source["points"]),
        },
        "parameters": {
            key: canonical_config[key]
            for key in (
                "normal_estimation",
                "voxel_size_m",
                "poisson",
                "ball_pivoting",
                "repair",
                "preview",
                "random_seed",
            )
        },
        "limits": canonical_config["limits"],
    }
    request = {
        **request_payload,
        "request_fingerprint": _digest(request_payload),
    }
    _publish_exact_json(
        workspace / "worker_request.json",
        request,
        conflict_code="mesh_worker_request_invalid",
        conflict_message="工作器请求与已有内容冲突。",
    )
    return request


def load_mesh_worker_input(workspace: Path) -> tuple[dict, dict]:
    """从同一次受控文件读取返回已验证请求及其点数据。"""

    workspace = Path(workspace)
    request = _read_json(
        workspace / "worker_request.json",
        maximum_bytes=_MAXIMUM_CONTROL_BYTES,
        code="mesh_worker_request_invalid",
    )
    if set(request) != _REQUEST_FIELDS:
        raise _error("mesh_worker_request_invalid", "工作器请求字段无效。")
    payload = dict(request)
    fingerprint = payload.pop("request_fingerprint")
    if not _is_sha256(fingerprint) or _digest(payload) != fingerprint:
        raise _error("mesh_worker_request_invalid", "工作器请求指纹无效。")
    input_value = request["input"]
    if type(input_value) is not dict or set(input_value) != _INPUT_FIELDS:
        raise _error("mesh_worker_request_invalid", "工作器输入描述无效。")
    relative = _fixed_relative_path(input_value["path"], "source_points.json")
    if (
        type(input_value["bytes"]) is not int
        or input_value["bytes"] < 0
        or not _is_sha256(input_value["sha256"])
        or type(input_value["point_count"]) is not int
        or input_value["point_count"] < 3
    ):
        raise _error("mesh_worker_request_invalid", "工作器输入摘要无效。")
    parameters = request["parameters"]
    if type(parameters) is not dict or set(parameters) != _PARAMETER_FIELDS:
        raise _error("mesh_worker_request_invalid", "工作器算法参数结构无效。")
    try:
        rebuilt = build_mesh_config(
            request["config_id"],
            {
                "schema_version": "1.0",
                "algorithm": request["algorithm"],
                "worker_version": request["worker_version"],
                "normal_estimation": parameters["normal_estimation"],
                "voxel_size_m": parameters["voxel_size_m"],
                "poisson": parameters["poisson"],
                "ball_pivoting": parameters["ball_pivoting"],
                "repair": parameters["repair"],
                "preview": parameters["preview"],
                "limits": request["limits"],
                "random_seed": parameters["random_seed"],
            },
        )
    except ModelMatchingError as exc:
        raise _error("mesh_worker_request_invalid", "工作器算法配置无效。") from exc
    if rebuilt["config_fingerprint"] != request["config_fingerprint"]:
        raise _error("mesh_worker_request_invalid", "工作器配置指纹不一致。")
    maximum_bytes = request["limits"]["maximum_artifact_bytes"]
    input_payload = _read_plain_bytes(
        workspace / relative, maximum_bytes=maximum_bytes
    )
    if (
        len(input_payload) != input_value["bytes"]
        or hashlib.sha256(input_payload).hexdigest() != input_value["sha256"]
    ):
        raise _error("mesh_worker_request_invalid", "来源点工件大小或哈希不一致。")
    try:
        points_document = json.loads(
            input_payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
            object_pairs_hook=_strict_object,
        )
        points = points_document["points"]
        if (
            type(points_document) is not dict
            or set(points_document)
            != {
                "schema_version",
                "coordinate_unit",
                "source_snapshot_fingerprint",
                "points",
            }
            or input_payload != _canonical_json_bytes(points_document)
            or points_document["schema_version"] != "1.0"
            or points_document["coordinate_unit"] != "m"
            or points_document["source_snapshot_fingerprint"]
            != request["source_snapshot_fingerprint"]
            or type(points) is not list
            or len(points) != input_value["point_count"]
            or len(points) > request["limits"]["maximum_input_points"]
            or any(
                type(point) is not list
                or len(point) != 3
                or any(
                    type(coordinate) not in {int, float}
                    or not math.isfinite(float(coordinate))
                    for coordinate in point
                )
                for point in points
            )
        ):
            raise ValueError("source points differ")
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("mesh_worker_request_invalid", "来源点工件内容无效。") from exc
    return request, points_document


def load_mesh_worker_request(workspace: Path) -> dict:
    request, _points_document = load_mesh_worker_input(workspace)
    return request


def _descriptor_payload(
    workspace: Path, descriptor: object, *, expected_path: str, maximum_bytes: int
) -> bytes:
    expected_fields = (
        _JSON_DESCRIPTOR_FIELDS
        if expected_path.endswith(".json")
        else _PLY_DESCRIPTOR_FIELDS
    )
    if type(descriptor) is not dict or set(descriptor) != expected_fields:
        raise _error("mesh_worker_result_invalid", "工作器工件描述无效。")
    relative = _fixed_relative_path(descriptor["path"], expected_path)
    size = descriptor["bytes"]
    checksum = descriptor["sha256"]
    if type(size) is not int or size < 0 or not _is_sha256(checksum):
        raise _error("mesh_worker_result_invalid", "工作器工件摘要无效。")
    if size > maximum_bytes:
        raise _error("mesh_worker_limit_exceeded", "工作器工件超过字节上限。")
    if not expected_path.endswith(".json"):
        for field in ("vertex_count", "triangle_count"):
            value = descriptor[field]
            if type(value) is not int or value < 0:
                raise _error("mesh_worker_result_invalid", "网格计数无效。")
    payload = _read_plain_bytes(workspace / relative, maximum_bytes=maximum_bytes)
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != checksum:
        raise _error("mesh_worker_artifact_invalid", "工作器工件大小或哈希不一致。")
    return payload


def _parse_ascii_ply(
    payload: bytes,
    *,
    declared_vertices: int,
    declared_triangles: int,
    maximum_vertices: int,
    maximum_triangles: int,
) -> None:
    if declared_vertices > maximum_vertices or declared_triangles > maximum_triangles:
        raise _error("mesh_worker_limit_exceeded", "网格顶点或三角面超过上限。")
    try:
        lines = payload.decode("ascii").splitlines()
        expected_header = [
            "ply",
            "format ascii 1.0",
            f"element vertex {declared_vertices}",
            "property double x",
            "property double y",
            "property double z",
            f"element face {declared_triangles}",
            "property list uchar int vertex_indices",
            "end_header",
        ]
        if lines[: len(expected_header)] != expected_header:
            raise ValueError("PLY header differs")
        body = lines[len(expected_header) :]
        if len(body) != declared_vertices + declared_triangles:
            raise ValueError("PLY body count differs")
        for line in body[:declared_vertices]:
            values = line.split()
            if len(values) != 3 or any(not math.isfinite(float(value)) for value in values):
                raise ValueError("invalid vertex")
        for line in body[declared_vertices:]:
            values = line.split()
            if len(values) != 4 or values[0] != "3":
                raise ValueError("invalid face")
            indices = [int(value) for value in values[1:]]
            if len(set(indices)) != 3 or any(
                index < 0 or index >= declared_vertices for index in indices
            ):
                raise ValueError("invalid face index")
    except (UnicodeError, ValueError, OverflowError) as exc:
        raise _error("mesh_worker_artifact_invalid", "ASCII PLY 网格无效。") from exc


def _parse_glb(
    payload: bytes,
    *,
    declared_vertices: int,
    declared_triangles: int,
    maximum_vertices: int,
    maximum_triangles: int,
) -> None:
    if declared_vertices > maximum_vertices or declared_triangles > maximum_triangles:
        raise _error("mesh_worker_limit_exceeded", "网格顶点或三角面超过上限。")
    try:
        if len(payload) < 28:
            raise ValueError("GLB is too short")
        magic, version, total_length = struct.unpack_from("<4sII", payload, 0)
        if magic != b"glTF" or version != 2 or total_length != len(payload):
            raise ValueError("GLB header differs")
        json_length, json_type = struct.unpack_from("<II", payload, 12)
        json_start = 20
        json_end = json_start + json_length
        if (
            json_type != 0x4E4F534A
            or json_length % 4
            or json_end + 8 > len(payload)
        ):
            raise ValueError("GLB JSON chunk differs")
        binary_length, binary_type = struct.unpack_from("<II", payload, json_end)
        binary_start = json_end + 8
        binary_end = binary_start + binary_length
        if (
            binary_type != 0x004E4942
            or binary_length % 4
            or binary_end != len(payload)
        ):
            raise ValueError("GLB BIN chunk differs")
        document = json.loads(
            payload[json_start:json_end].rstrip(b" ").decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
            object_pairs_hook=_strict_object,
        )
        if type(document) is not dict or set(document) != {
            "asset",
            "buffers",
            "bufferViews",
            "accessors",
            "meshes",
            "nodes",
            "scenes",
            "scene",
        }:
            raise ValueError("GLB document fields differ")
        position_bytes = declared_vertices * 3 * 4
        index_count = declared_triangles * 3
        index_bytes = index_count * 4
        if binary_length != position_bytes + index_bytes:
            raise ValueError("GLB binary size differs")
        expected_document = {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": binary_length}],
            "bufferViews": [
                {"buffer": 0, "byteLength": position_bytes, "byteOffset": 0},
                {
                    "buffer": 0,
                    "byteLength": index_bytes,
                    "byteOffset": position_bytes,
                },
            ],
            "accessors": [
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": declared_vertices,
                    "type": "VEC3",
                },
                {
                    "bufferView": 1,
                    "componentType": 5125,
                    "count": index_count,
                    "type": "SCALAR",
                },
            ],
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {"POSITION": 0},
                            "indices": 1,
                            "mode": 4,
                        }
                    ]
                }
            ],
            "nodes": [{"mesh": 0}],
            "scenes": [{"nodes": [0]}],
            "scene": 0,
        }
        if _compact_bytes(document) != _compact_bytes(expected_document):
            raise ValueError("GLB geometry contract differs")
        binary = payload[binary_start:binary_end]
        for offset in range(0, position_bytes, 12):
            if any(
                not math.isfinite(value)
                for value in struct.unpack_from("<fff", binary, offset)
            ):
                raise ValueError("GLB vertex is non-finite")
        index_offset = position_bytes
        for face_offset in range(0, index_bytes, 12):
            face = struct.unpack_from("<III", binary, index_offset + face_offset)
            if len(set(face)) != 3 or any(index >= declared_vertices for index in face):
                raise ValueError("GLB face is invalid")
    except (KeyError, TypeError, UnicodeError, ValueError, struct.error, json.JSONDecodeError) as exc:
        raise _error("mesh_worker_artifact_invalid", "GLB 预览网格无效。") from exc


def _validate_metrics(value: dict, request: dict) -> None:
    if set(value) != _METRIC_FIELDS or value.get("schema_version") != "1.0":
        raise _error("mesh_worker_result_invalid", "网格指标字段无效。")
    integer_fields = {
        "raw_vertex_count",
        "raw_triangle_count",
        "repaired_vertex_count",
        "repaired_triangle_count",
        "non_finite_vertex_count",
        "non_finite_face_count",
        "duplicate_vertex_count",
        "degenerate_triangle_count",
        "non_manifold_edge_count",
        "boundary_edge_count",
        "component_count",
    }
    fraction_fields = {
        "largest_component_fraction",
        "normal_consistency_fraction",
        "mesh_to_point_coverage",
    }
    for field in integer_fields:
        if type(value[field]) is not int or value[field] < 0:
            raise _error("mesh_worker_result_invalid", "网格整数指标无效。")
    for field in fraction_fields:
        if type(value[field]) not in {int, float} or not 0 <= float(value[field]) <= 1:
            raise _error("mesh_worker_result_invalid", "网格比例指标无效。")
    for field in {
        "point_to_mesh_p50_m",
        "point_to_mesh_p95_m",
        "point_to_mesh_max_m",
        "repair_max_displacement_m",
    }:
        if type(value[field]) not in {int, float} or not math.isfinite(float(value[field])) or float(value[field]) < 0:
            raise _error("mesh_worker_result_invalid", "网格距离指标无效。")
    if (
        value["raw_vertex_count"] > request["limits"]["maximum_vertices"]
        or value["repaired_vertex_count"] > request["limits"]["maximum_vertices"]
        or value["raw_triangle_count"] > request["limits"]["maximum_triangles"]
        or value["repaired_triangle_count"] > request["limits"]["maximum_triangles"]
    ):
        raise _error("mesh_worker_limit_exceeded", "网格指标超过资源上限。")


def validate_mesh_worker_result(workspace: Path, request: dict) -> dict:
    """验证固定结果清单、普通文件、工程 PLY、预览 GLB 和有限指标。"""

    workspace = Path(workspace)
    persisted_request = load_mesh_worker_request(workspace)
    if persisted_request != request:
        raise _error("mesh_worker_request_invalid", "调用请求与持久化请求不一致。")
    result = _read_json(
        workspace / "worker_result.json",
        maximum_bytes=_MAXIMUM_CONTROL_BYTES,
        code="mesh_worker_result_invalid",
    )
    if (
        set(result) != _RESULT_FIELDS
        or result.get("schema_version") != "1.0"
        or result.get("status") != "completed"
        or result.get("request_fingerprint") != request["request_fingerprint"]
        or result.get("algorithm") != request["algorithm"]
        or result.get("worker_version") != request["worker_version"]
        or not _is_sha256(result.get("metrics_fingerprint"))
    ):
        raise _error("mesh_worker_result_invalid", "工作器结果身份不一致。")
    artifacts = result["artifacts"]
    if type(artifacts) is not dict or set(artifacts) != set(_ARTIFACT_NAMES):
        raise _error("mesh_worker_result_invalid", "工作器工件集合无效。")
    maximum_bytes = request["limits"]["maximum_artifact_bytes"]
    for key in ("raw_mesh", "repaired_mesh"):
        descriptor = artifacts[key]
        if type(descriptor) is not dict or set(descriptor) != _PLY_DESCRIPTOR_FIELDS:
            raise _error("mesh_worker_result_invalid", "网格工件描述无效。")
        vertices = descriptor["vertex_count"]
        triangles = descriptor["triangle_count"]
        if type(vertices) is not int or type(triangles) is not int:
            raise _error("mesh_worker_result_invalid", "网格计数无效。")
        if (
            vertices > request["limits"]["maximum_vertices"]
            or triangles > request["limits"]["maximum_triangles"]
        ):
            raise _error("mesh_worker_limit_exceeded", "网格顶点或三角面超过上限。")
        payload = _descriptor_payload(
            workspace,
            descriptor,
            expected_path=_ARTIFACT_NAMES[key],
            maximum_bytes=maximum_bytes,
        )
        _parse_ascii_ply(
            payload,
            declared_vertices=vertices,
            declared_triangles=triangles,
            maximum_vertices=request["limits"]["maximum_vertices"],
            maximum_triangles=request["limits"]["maximum_triangles"],
        )
    preview = artifacts["preview_mesh"]
    if type(preview) is not dict or set(preview) != _PLY_DESCRIPTOR_FIELDS:
        raise _error("mesh_worker_result_invalid", "预览网格工件描述无效。")
    preview_vertices = preview["vertex_count"]
    preview_triangles = preview["triangle_count"]
    if type(preview_vertices) is not int or type(preview_triangles) is not int:
        raise _error("mesh_worker_result_invalid", "预览网格计数无效。")
    preview_payload = _descriptor_payload(
        workspace,
        preview,
        expected_path=_ARTIFACT_NAMES["preview_mesh"],
        maximum_bytes=maximum_bytes,
    )
    _parse_glb(
        preview_payload,
        declared_vertices=preview_vertices,
        declared_triangles=preview_triangles,
        maximum_vertices=request["limits"]["maximum_vertices"],
        maximum_triangles=request["limits"]["maximum_triangles"],
    )
    metrics_payload = _descriptor_payload(
        workspace,
        artifacts["mesh_metrics"],
        expected_path="mesh_metrics.json",
        maximum_bytes=maximum_bytes,
    )
    try:
        metrics = json.loads(
            metrics_payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
            object_pairs_hook=_strict_object,
        )
        if (
            type(metrics) is not dict
            or metrics_payload != _canonical_json_bytes(metrics)
        ):
            raise ValueError("noncanonical metrics")
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("mesh_worker_result_invalid", "网格指标 JSON 无效。") from exc
    _validate_metrics(metrics, request)
    if result["metrics_fingerprint"] != _digest(metrics):
        raise _error("mesh_worker_result_invalid", "网格指标指纹不一致。")
    if (
        artifacts["raw_mesh"]["vertex_count"] != metrics["raw_vertex_count"]
        or artifacts["raw_mesh"]["triangle_count"] != metrics["raw_triangle_count"]
        or artifacts["repaired_mesh"]["vertex_count"]
        != metrics["repaired_vertex_count"]
        or artifacts["repaired_mesh"]["triangle_count"]
        != metrics["repaired_triangle_count"]
    ):
        raise _error("mesh_worker_result_invalid", "网格清单与指标计数不一致。")
    return result
