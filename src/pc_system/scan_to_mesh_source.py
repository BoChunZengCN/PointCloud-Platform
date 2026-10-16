"""把已审查单对象点云冻结为确定性的米制网格来源快照。"""

import hashlib
import json
import math
import os
import stat
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.reference_review import approved_review_evidence
from pc_system.reference_store import digest, load_bundle
from pc_system.scan_to_mesh_errors import ScanToMeshError
from pc_system.segmentation_correction_releases import load_correction_release
from pc_system.segmentation_corrections import CorrectionError
from pc_system.segmentation_review_evidence import load_object_review_evidence


MAXIMUM_SOURCE_POINTS = 1_000_000
_MAXIMUM_JSON_BYTES = 64 * 1024 * 1024
_SOURCE_KINDS = frozenset(
    {"phase14_correction_release", "phase15e_scanned_reference"}
)


def _error(code: str, message: str) -> ScanToMeshError:
    return ScanToMeshError(code, message)


def _canonical_bytes(value: object) -> bytes:
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
            "mesh_source_integrity_error", "来源快照不能规范编码。"
        ) from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_reparse_point(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(info, "st_file_attributes", 0) & flag)


def _read_json_object(path: Path) -> dict:
    """从已知工件名读取有界普通文件，不跟随链接或重解析点。"""

    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse_point(before)
            or before.st_size > _MAXIMUM_JSON_BYTES
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
                or opened.st_size > _MAXIMUM_JSON_BYTES
                or (opened.st_dev, opened.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                raise ValueError("artifact changed")
            payload = bytearray()
            remaining = _MAXIMUM_JSON_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                payload.extend(chunk)
                remaining -= len(chunk)
            if len(payload) > _MAXIMUM_JSON_BYTES:
                raise ValueError("artifact too large")
        finally:
            os.close(descriptor)
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
        )
        if type(value) is not dict:
            raise ValueError("artifact is not an object")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _error(
            "mesh_source_integrity_error", "来源发布工件无效或已改变。"
        ) from exc


def _identifier(value: object, field: str) -> str:
    try:
        if type(value) is not str:
            raise ValueError("identifier must be a string")
        return validate_identifier(value, field)
    except (TypeError, ValueError) as exc:
        raise _error(
            "mesh_source_object_invalid", f"来源字段 {field} 无效。"
        ) from exc


def _source_ref(value: object, fields: set[str]) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise _error(
            "mesh_source_object_invalid", "来源身份字段不完整或包含未知字段。"
        )
    return {field: _identifier(value[field], field) for field in sorted(fields)}


def _finite_coordinate(value: object) -> float:
    if type(value) not in {int, float}:
        raise _error(
            "mesh_source_integrity_error", "来源点坐标必须是有限数值。"
        )
    result = float(value)
    if not math.isfinite(result):
        raise _error(
            "mesh_source_integrity_error", "来源点坐标必须是有限数值。"
        )
    return result


def _rounded(value: float) -> float:
    result = round(value, 12)
    return 0.0 if result == 0 else result


def _ensure_meshable(points: list[list[float]]) -> None:
    if len(points) > MAXIMUM_SOURCE_POINTS:
        raise _error(
            "mesh_source_limit_exceeded", "单对象点数超过网格来源上限。"
        )
    unique = sorted(set(tuple(point) for point in points))
    if len(unique) < 3:
        raise _error(
            "mesh_source_object_invalid", "单对象至少需要三个唯一点。"
        )
    origin = unique[0]
    first = None
    for point in unique[1:]:
        vector = tuple(point[axis] - origin[axis] for axis in range(3))
        if any(component != 0 for component in vector):
            first = vector
            break
    if first is None:
        raise _error(
            "mesh_source_object_invalid", "单对象点集没有有效跨度。"
        )
    scale = max(abs(value) for point in unique for value in point)
    tolerance = max(scale * scale, 1.0) * 1e-24
    for point in unique[1:]:
        second = tuple(point[axis] - origin[axis] for axis in range(3))
        cross = (
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        )
        if sum(value * value for value in cross) > tolerance:
            return
    raise _error(
        "mesh_source_object_invalid", "单对象点集共线，不能形成三角网格。"
    )


def _local_geometry(
    points_m: list[list[float]], *, preserve_order: bool
) -> tuple[list[list[float]], dict, list[list[float]]]:
    _ensure_meshable(points_m)
    minima = [min(point[axis] for point in points_m) for axis in range(3)]
    maxima = [max(point[axis] for point in points_m) for axis in range(3)]
    origin = [
        minima[axis] / 2.0 + maxima[axis] / 2.0 for axis in range(3)
    ]
    local = [
        [_rounded(point[axis] - origin[axis]) for axis in range(3)]
        for point in points_m
    ]
    if not preserve_order:
        local.sort()
    matrix = [
        [1.0, 0.0, 0.0, _rounded(-origin[0])],
        [0.0, 1.0, 0.0, _rounded(-origin[1])],
        [0.0, 0.0, 1.0, _rounded(-origin[2])],
        [0.0, 0.0, 0.0, 1.0],
    ]
    bounds = {
        "min": [min(point[axis] for point in local) for axis in range(3)],
        "max": [max(point[axis] for point in local) for axis in range(3)],
    }
    return local, bounds, matrix


def _snapshot(payload: dict) -> dict:
    return {**payload, "fingerprint": _fingerprint(payload)}


def _phase14_snapshot(root: Path, source_ref: dict) -> dict:
    values = _source_ref(
        source_ref, {"asset_id", "release_id", "instance_id"}
    )
    try:
        release = load_correction_release(
            root, values["asset_id"], values["release_id"]
        )
    except CorrectionError as exc:
        code = (
            "mesh_source_not_found"
            if exc.code in {"release_not_found", "session_not_found"}
            else "mesh_source_integrity_error"
        )
        raise _error(code, "Phase 14 纠正发布不存在或无法验证。") from exc
    try:
        if (
            release.get("status") != "published"
            or release.get("asset_id") != values["asset_id"]
            or release.get("release_id") != values["release_id"]
            or not _is_sha256(release.get("source_fingerprint"))
        ):
            raise ValueError("release identity")
        artifacts = release.get("artifacts")
        expected = {
            "labels": "labels.json",
            "provenance": "provenance.json",
            "object_review_evidence": "object_review_evidence.json",
        }
        if type(artifacts) is not dict or any(
            artifacts.get(key) != name for key, name in expected.items()
        ):
            raise ValueError("artifact identity")
    except (TypeError, ValueError) as exc:
        raise _error(
            "mesh_source_integrity_error", "Phase 14 发布身份或工件清单无效。"
        ) from exc
    directory = (
        root
        / "reports"
        / "segmentation_correction_releases"
        / values["asset_id"]
        / values["release_id"]
    )
    labels = _read_json_object(directory / "labels.json")
    provenance = _read_json_object(directory / "provenance.json")
    try:
        evidence = load_object_review_evidence(
            root, values["asset_id"], values["release_id"]
        )
    except CorrectionError as exc:
        raise _error(
            "mesh_source_integrity_error", "Phase 14 对象核验证据无效。"
        ) from exc
    if evidence is None:
        raise _error(
            "mesh_source_not_reviewed", "Phase 14 发布缺少对象核验证据。"
        )
    if (
        evidence.get("source_fingerprint") != release["source_fingerprint"]
        or provenance.get("source_fingerprint") != release["source_fingerprint"]
        or provenance.get("asset_id") != values["asset_id"]
    ):
        raise _error(
            "mesh_source_integrity_error", "Phase 14 来源指纹不一致。"
        )
    review_items = [
        item
        for item in evidence["objects"]
        if item["instance_id"] == values["instance_id"]
    ]
    if len(review_items) != 1:
        raise _error(
            "mesh_source_object_invalid", "指定对象不存在或仅包含噪声点。"
        )
    review = review_items[0]
    if review["review_state"] != "confirmed":
        raise _error(
            "mesh_source_not_reviewed", "指定对象尚未由人员确认。"
        )
    point_labels = labels.get("point_labels")
    if labels.get("schema_version") != "1.0" or type(point_labels) is not list:
        raise _error(
            "mesh_source_integrity_error", "Phase 14 点标签结构无效。"
        )
    parsed = []
    seen_indices: set[int] = set()
    for item in point_labels:
        try:
            if type(item) is not dict:
                raise ValueError("label is not an object")
            index = item["point_index"]
            if type(index) is not int or index < 0 or index in seen_indices:
                raise ValueError("point index")
            seen_indices.add(index)
            instance_id = _identifier(item["instance_id"], "instance_id")
            class_id = _identifier(item["class_id"], "class_id")
            is_noise = item["is_noise"]
            if type(is_noise) is not bool:
                raise ValueError("noise flag")
            parsed.append(
                {
                    "source_point_index": index,
                    "x": _finite_coordinate(item["x"]),
                    "y": _finite_coordinate(item["y"]),
                    "z": _finite_coordinate(item["z"]),
                    "instance_id": instance_id,
                    "class_id": class_id,
                    "is_noise": is_noise,
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _error(
                "mesh_source_integrity_error", "Phase 14 点标签值无效。"
            ) from exc
    if sorted(seen_indices) != list(range(len(parsed))):
        raise _error(
            "mesh_source_integrity_error", "Phase 14 点索引不完整。"
        )
    selected = sorted(
        [item for item in parsed if item["instance_id"] == values["instance_id"]],
        key=lambda item: item["source_point_index"],
    )
    if (
        not selected
        or any(item["is_noise"] for item in selected)
        or any(item["class_id"] != review["class_id"] for item in selected)
        or review["point_count"] != len(selected)
        or review["object_fingerprint"] != _fingerprint(selected)
    ):
        raise _error(
            "mesh_source_integrity_error", "Phase 14 对象成员与核验证据不一致。"
        )
    points_m = [[item[axis] for axis in ("x", "y", "z")] for item in selected]
    points, bounds, matrix = _local_geometry(points_m, preserve_order=True)
    payload = {
        "schema_version": "1.0",
        "source_kind": "phase14_correction_release",
        "source_identity": {
            **values,
            "class_id": review["class_id"],
        },
        "coordinate_unit": "m",
        "points": points,
        "point_count": len(points),
        "bounds_m": bounds,
        "source_to_local_4x4": matrix,
        "source_evidence": {
            "source_fingerprint": release["source_fingerprint"],
            "release_fingerprint": _fingerprint(release),
            "provenance_fingerprint": _fingerprint(provenance),
            "review_evidence_fingerprint": _fingerprint(evidence),
            "object_fingerprint": review["object_fingerprint"],
            "review_state": review["review_state"],
        },
    }
    return _snapshot(payload)


def _phase15e_snapshot(root: Path, source_ref: dict) -> dict:
    values = _source_ref(source_ref, {"model_id", "version_id"})
    try:
        bundle = load_bundle(root, values["model_id"], values["version_id"])
    except ModelMatchingError as exc:
        code = (
            "mesh_source_not_found"
            if exc.code == "reference_version_not_ready"
            else "mesh_source_integrity_error"
        )
        raise _error(code, "Phase 15E 扫描版本不存在或无法验证。") from exc
    try:
        review = approved_review_evidence(
            root, values["model_id"], values["version_id"]
        )
    except ModelMatchingError as exc:
        code = (
            "mesh_source_not_reviewed"
            if exc.code == "reference_review_required"
            else "mesh_source_integrity_error"
        )
        raise _error(code, "Phase 15E 扫描版本尚未通过专家核验。") from exc
    normalized = bundle["normalized"]
    raw_points = normalized.get("points")
    if (
        normalized.get("coordinate_unit") != "m"
        or type(raw_points) is not list
        or normalized.get("point_count") != len(raw_points)
    ):
        raise _error(
            "mesh_source_integrity_error", "Phase 15E 米制点集结构无效。"
        )
    points = []
    for point in raw_points:
        if type(point) is not list or len(point) != 3:
            raise _error(
                "mesh_source_integrity_error", "Phase 15E 点坐标结构无效。"
            )
        points.append([_finite_coordinate(value) for value in point])
    _ensure_meshable(points)
    if len(points) > MAXIMUM_SOURCE_POINTS:
        raise _error(
            "mesh_source_limit_exceeded", "单对象点数超过网格来源上限。"
        )
    bounds = {
        "min": [min(point[axis] for point in points) for axis in range(3)],
        "max": [max(point[axis] for point in points) for axis in range(3)],
    }
    matrix = normalized.get("source_m_to_reference_4x4")
    if (
        type(matrix) is not list
        or len(matrix) != 4
        or any(type(row) is not list or len(row) != 4 for row in matrix)
        or any(
            type(value) not in {int, float} or not math.isfinite(float(value))
            for row in matrix
            for value in row
        )
    ):
        raise _error(
            "mesh_source_integrity_error", "Phase 15E 坐标变换无效。"
        )
    payload = {
        "schema_version": "1.0",
        "source_kind": "phase15e_scanned_reference",
        "source_identity": values,
        "coordinate_unit": "m",
        "points": points,
        "point_count": len(points),
        "bounds_m": bounds,
        "source_to_local_4x4": matrix,
        "source_evidence": {
            "source_fingerprint": bundle["source_frozen"]["source_fingerprint"],
            "manifest_fingerprint": digest(bundle["manifest"]),
            "geometry_fingerprint": normalized["geometry_fingerprint"],
            **review,
        },
    }
    return _snapshot(payload)


def freeze_mesh_source(
    root: Path, *, source_kind: str, source_ref: dict
) -> dict:
    """验证可信来源并返回无副作用的确定性单对象点集快照。"""

    if type(source_kind) is not str or source_kind not in _SOURCE_KINDS:
        raise _error(
            "mesh_source_object_invalid", "不支持的网格来源类型。"
        )
    root = Path(root)
    if source_kind == "phase14_correction_release":
        return _phase14_snapshot(root, source_ref)
    return _phase15e_snapshot(root, source_ref)
