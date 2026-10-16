"""版本化网格重建配置与质量策略。"""

import hashlib
import json
import math
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    load_operation,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import _record_failure, _require_plain
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_sampling import (
    _canonical_json_bytes,
    _file_fingerprint,
    _publish_exact_json,
)
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
_NORMAL_FIELDS = {"radius_m", "max_nn", "orientation", "orientation_k"}
_POISSON_FIELDS = {"depth", "density_quantile"}
_BALL_PIVOTING_FIELDS = {"radii_m"}
_REPAIR_FIELDS = {
    "remove_duplicate_vertices",
    "remove_degenerate_triangles",
    "remove_non_manifold_duplicates",
    "minimum_component_triangles",
    "maximum_removed_component_fraction",
    "fill_holes",
}
_PREVIEW_FIELDS = {"target_triangles"}
_LIMIT_FIELDS = {
    "maximum_input_points",
    "maximum_vertices",
    "maximum_triangles",
    "maximum_artifact_bytes",
    "timeout_seconds",
}
_POLICY_FIELDS = {
    "schema_version",
    "policy_kind",
    "production_context",
    "requires_watertight",
    "hard_limits",
    "review_limits",
    "decisions",
}
_PRODUCTION_CONTEXT_FIELDS = {
    "scanner_model",
    "nominal_point_spacing_m",
    "preprocessing",
    "object_families",
    "approved_by",
}
_HARD_FIELDS = {
    "maximum_non_finite_vertices",
    "maximum_non_finite_faces",
    "maximum_degenerate_face_fraction",
    "maximum_non_manifold_edge_fraction",
    "maximum_component_count",
    "minimum_largest_component_fraction",
    "minimum_point_coverage",
    "maximum_point_to_mesh_p95_m",
    "maximum_repair_displacement_m",
    "maximum_triangles",
}
_REVIEW_FIELDS = {
    "maximum_boundary_edge_fraction",
    "maximum_component_count",
    "minimum_largest_component_fraction",
    "minimum_point_coverage",
    "maximum_point_to_mesh_p95_m",
    "maximum_repair_displacement_m",
}
_DECISION_FIELDS = {
    "hard_failure",
    "review_threshold",
    "all_passed",
    "auto_publish_passed",
}
_DOCUMENT_METADATA_FIELDS = {
    "operation_id",
    "created_by",
    "created_at",
    "status",
}
_OWNER_FIELDS = {
    "schema_version",
    "artifact_kind",
    "artifact_id",
    "operation_id",
    "request_id",
    "request_fingerprint",
}
_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


def _error(code: str, message: str) -> ScanToMeshError:
    return ScanToMeshError(code, message)


def _invalid(kind: str, message: str) -> ScanToMeshError:
    code = "mesh_config_invalid" if kind == "config" else "mesh_policy_invalid"
    return _error(code, message)


def _integrity(kind: str, message: str) -> ScanToMeshError:
    code = (
        "mesh_config_integrity_error"
        if kind == "config"
        else "mesh_policy_integrity_error"
    )
    return _error(code, message)


def _canonical_bytes(value: object, kind: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _invalid(kind, "配置不能规范编码。") from exc


def _fingerprint(value: object, kind: str) -> str:
    return hashlib.sha256(_canonical_bytes(value, kind)).hexdigest()


def _identifier(value: object, label: str, kind: str) -> str:
    try:
        if type(value) is not str:
            raise ValueError("identifier must be a string")
        return validate_identifier(value, label)
    except (TypeError, ValueError) as exc:
        raise _invalid(kind, f"{label} 无效。") from exc


def _exact_dict(value: object, fields: set[str], label: str, kind: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise _invalid(kind, f"{label} 结构无效。")
    return dict(value)


def _boolean(value: object, label: str, kind: str) -> bool:
    if type(value) is not bool:
        raise _invalid(kind, f"{label} 必须是布尔值。")
    return value


def _integer(
    value: object, minimum: int, maximum: int, label: str, kind: str
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid(kind, f"{label} 超出允许范围。")
    return value


def _number(
    value: object,
    minimum: float,
    maximum: float,
    label: str,
    kind: str,
    *,
    minimum_inclusive: bool = True,
) -> float:
    if type(value) not in {int, float}:
        raise _invalid(kind, f"{label} 必须是有限数值。")
    normalized = float(value)
    valid_minimum = (
        normalized >= minimum if minimum_inclusive else normalized > minimum
    )
    if not math.isfinite(normalized) or not valid_minimum or normalized > maximum:
        raise _invalid(kind, f"{label} 超出允许范围。")
    return normalized


def _text(value: object, maximum: int, label: str, kind: str) -> str:
    if type(value) is not str or not value.strip() or "\0" in value:
        raise _invalid(kind, f"{label} 必须是非空文本。")
    normalized = value.strip()
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeError as exc:
        raise _invalid(kind, f"{label} 不是有效 UTF-8 文本。") from exc
    if len(encoded) > maximum:
        raise _invalid(kind, f"{label} 超过长度限制。")
    return normalized


def build_mesh_config(config_id: str, value: object) -> dict:
    """规范化重建配置；算法分支互斥且禁止隐式回退。"""

    kind = "config"
    config_id = _identifier(config_id, "config_id", kind)
    item = _exact_dict(value, _CONFIG_FIELDS, "网格配置", kind)
    if item["schema_version"] != "1.0":
        raise _invalid(kind, "网格配置版本无效。")
    algorithm = item["algorithm"]
    if algorithm not in {"poisson", "ball_pivoting"}:
        raise _invalid(kind, "网格算法必须是 poisson 或 ball_pivoting。")
    normal = _exact_dict(
        item["normal_estimation"], _NORMAL_FIELDS, "法向量配置", kind
    )
    orientation = normal["orientation"]
    if orientation not in {"consistent_tangent_plane", "towards_sensor"}:
        raise _invalid(kind, "法向量方向策略无效。")
    normalized_normal = {
        "radius_m": _number(
            normal["radius_m"], 0.0, 1000.0, "radius_m", kind,
            minimum_inclusive=False,
        ),
        "max_nn": _integer(normal["max_nn"], 3, 10_000, "max_nn", kind),
        "orientation": orientation,
        "orientation_k": _integer(
            normal["orientation_k"], 3, 10_000, "orientation_k", kind
        ),
    }
    poisson = item["poisson"]
    pivoting = item["ball_pivoting"]
    if algorithm == "poisson":
        poisson = _exact_dict(poisson, _POISSON_FIELDS, "Poisson 配置", kind)
        if pivoting is not None:
            raise _invalid(kind, "Poisson 配置不能混入 Ball Pivoting 参数。")
        normalized_poisson = {
            "depth": _integer(poisson["depth"], 4, 14, "depth", kind),
            "density_quantile": _number(
                poisson["density_quantile"],
                0.0,
                0.5,
                "density_quantile",
                kind,
            ),
        }
        normalized_pivoting = None
    else:
        pivoting = _exact_dict(
            pivoting, _BALL_PIVOTING_FIELDS, "Ball Pivoting 配置", kind
        )
        if poisson is not None:
            raise _invalid(kind, "Ball Pivoting 配置不能混入 Poisson 参数。")
        radii = pivoting["radii_m"]
        if type(radii) is not list or not 1 <= len(radii) <= 16:
            raise _invalid(kind, "Ball Pivoting 半径列表无效。")
        normalized_radii = [
            _number(
                radius,
                0.0,
                1000.0,
                "radii_m",
                kind,
                minimum_inclusive=False,
            )
            for radius in radii
        ]
        if any(left >= right for left, right in zip(normalized_radii, normalized_radii[1:])):
            raise _invalid(kind, "Ball Pivoting 半径必须严格递增。")
        normalized_poisson = None
        normalized_pivoting = {"radii_m": normalized_radii}
    repair = _exact_dict(item["repair"], _REPAIR_FIELDS, "修复配置", kind)
    normalized_repair = {
        field: _boolean(repair[field], field, kind)
        for field in (
            "remove_duplicate_vertices",
            "remove_degenerate_triangles",
            "remove_non_manifold_duplicates",
        )
    }
    if _boolean(repair["fill_holes"], "fill_holes", kind):
        raise _invalid(kind, "第一版禁止自动填洞。")
    normalized_repair.update(
        {
            "minimum_component_triangles": _integer(
                repair["minimum_component_triangles"],
                0,
                1_000_000,
                "minimum_component_triangles",
                kind,
            ),
            "maximum_removed_component_fraction": _number(
                repair["maximum_removed_component_fraction"],
                0.0,
                0.1,
                "maximum_removed_component_fraction",
                kind,
            ),
            "fill_holes": False,
        }
    )
    preview = _exact_dict(item["preview"], _PREVIEW_FIELDS, "预览配置", kind)
    limits = _exact_dict(item["limits"], _LIMIT_FIELDS, "资源上限", kind)
    normalized_limits = {
        "maximum_input_points": _integer(
            limits["maximum_input_points"], 3, 1_000_000,
            "maximum_input_points", kind,
        ),
        "maximum_vertices": _integer(
            limits["maximum_vertices"], 3, 2_000_000,
            "maximum_vertices", kind,
        ),
        "maximum_triangles": _integer(
            limits["maximum_triangles"], 1, 4_000_000,
            "maximum_triangles", kind,
        ),
        "maximum_artifact_bytes": _integer(
            limits["maximum_artifact_bytes"], 1_048_576, 1_073_741_824,
            "maximum_artifact_bytes", kind,
        ),
        "timeout_seconds": _integer(
            limits["timeout_seconds"], 1, 3600, "timeout_seconds", kind
        ),
    }
    target_triangles = _integer(
        preview["target_triangles"],
        100,
        normalized_limits["maximum_triangles"],
        "target_triangles",
        kind,
    )
    canonical = {
        "schema_version": "1.0",
        "config_id": config_id,
        "algorithm": algorithm,
        "worker_version": _identifier(
            item["worker_version"], "worker_version", kind
        ),
        "normal_estimation": normalized_normal,
        "voxel_size_m": _number(
            item["voxel_size_m"], 0.0, 1000.0, "voxel_size_m", kind
        ),
        "poisson": normalized_poisson,
        "ball_pivoting": normalized_pivoting,
        "repair": normalized_repair,
        "preview": {"target_triangles": target_triangles},
        "limits": normalized_limits,
        "random_seed": _integer(
            item["random_seed"], 0, 9_223_372_036_854_775_807,
            "random_seed", kind,
        ),
    }
    return {**canonical, "config_fingerprint": _fingerprint(canonical, kind)}


def build_mesh_quality_policy(policy_id: str, value: object) -> dict:
    """规范化三态质量门禁，硬失败只能进入 rejected。"""

    kind = "policy"
    policy_id = _identifier(policy_id, "policy_id", kind)
    item = _exact_dict(value, _POLICY_FIELDS, "网格质量策略", kind)
    if item["schema_version"] != "1.0" or item["policy_kind"] not in {
        "engineering_fixture",
        "production",
    }:
        raise _invalid(kind, "网格质量策略版本或类型无效。")
    raw_context = item["production_context"]
    if item["policy_kind"] == "engineering_fixture":
        if raw_context is not None:
            raise _invalid(kind, "工程夹具策略不能伪装生产审批上下文。")
        production_context = None
    else:
        context = _exact_dict(
            raw_context,
            _PRODUCTION_CONTEXT_FIELDS,
            "生产策略上下文",
            kind,
        )
        families = context["object_families"]
        if type(families) is not list or not 1 <= len(families) <= 32:
            raise _invalid(kind, "生产策略对象族无效。")
        normalized_families = [
            _identifier(value, "object_family", kind) for value in families
        ]
        if len(normalized_families) != len(set(normalized_families)):
            raise _invalid(kind, "生产策略对象族不能重复。")
        production_context = {
            "scanner_model": _text(
                context["scanner_model"], 256, "scanner_model", kind
            ),
            "nominal_point_spacing_m": _number(
                context["nominal_point_spacing_m"],
                0.0,
                10.0,
                "nominal_point_spacing_m",
                kind,
                minimum_inclusive=False,
            ),
            "preprocessing": _text(
                context["preprocessing"], 2000, "preprocessing", kind
            ),
            "object_families": sorted(normalized_families),
            "approved_by": _identifier(
                context["approved_by"], "approved_by", kind
            ),
        }
    hard = _exact_dict(item["hard_limits"], _HARD_FIELDS, "硬门禁", kind)
    review = _exact_dict(
        item["review_limits"], _REVIEW_FIELDS, "复核门禁", kind
    )
    decisions = _exact_dict(
        item["decisions"], _DECISION_FIELDS, "门禁决定", kind
    )
    if (
        decisions["hard_failure"] != "rejected"
        or decisions["review_threshold"] != "review_required"
        or decisions["all_passed"] != "passed"
    ):
        raise _invalid(kind, "三态门禁决定不可改写。")
    normalized_hard = {
        "maximum_non_finite_vertices": _integer(
            hard["maximum_non_finite_vertices"], 0, 0,
            "maximum_non_finite_vertices", kind,
        ),
        "maximum_non_finite_faces": _integer(
            hard["maximum_non_finite_faces"], 0, 0,
            "maximum_non_finite_faces", kind,
        ),
        "maximum_degenerate_face_fraction": _number(
            hard["maximum_degenerate_face_fraction"], 0.0, 1.0,
            "maximum_degenerate_face_fraction", kind,
        ),
        "maximum_non_manifold_edge_fraction": _number(
            hard["maximum_non_manifold_edge_fraction"], 0.0, 1.0,
            "maximum_non_manifold_edge_fraction", kind,
        ),
        "maximum_component_count": _integer(
            hard["maximum_component_count"], 1, 1_000_000,
            "maximum_component_count", kind,
        ),
        "minimum_largest_component_fraction": _number(
            hard["minimum_largest_component_fraction"], 0.0, 1.0,
            "minimum_largest_component_fraction", kind,
        ),
        "minimum_point_coverage": _number(
            hard["minimum_point_coverage"], 0.0, 1.0,
            "minimum_point_coverage", kind,
        ),
        "maximum_point_to_mesh_p95_m": _number(
            hard["maximum_point_to_mesh_p95_m"], 0.0, 1000.0,
            "maximum_point_to_mesh_p95_m", kind,
            minimum_inclusive=False,
        ),
        "maximum_repair_displacement_m": _number(
            hard["maximum_repair_displacement_m"], 0.0, 1000.0,
            "maximum_repair_displacement_m", kind,
        ),
        "maximum_triangles": _integer(
            hard["maximum_triangles"], 1, 4_000_000,
            "maximum_triangles", kind,
        ),
    }
    normalized_review = {
        "maximum_boundary_edge_fraction": _number(
            review["maximum_boundary_edge_fraction"], 0.0, 1.0,
            "maximum_boundary_edge_fraction", kind,
        ),
        "maximum_component_count": _integer(
            review["maximum_component_count"], 1,
            normalized_hard["maximum_component_count"],
            "maximum_component_count", kind,
        ),
        "minimum_largest_component_fraction": _number(
            review["minimum_largest_component_fraction"], 0.0, 1.0,
            "minimum_largest_component_fraction", kind,
        ),
        "minimum_point_coverage": _number(
            review["minimum_point_coverage"], 0.0, 1.0,
            "minimum_point_coverage", kind,
        ),
        "maximum_point_to_mesh_p95_m": _number(
            review["maximum_point_to_mesh_p95_m"], 0.0,
            normalized_hard["maximum_point_to_mesh_p95_m"],
            "maximum_point_to_mesh_p95_m", kind,
            minimum_inclusive=False,
        ),
        "maximum_repair_displacement_m": _number(
            review["maximum_repair_displacement_m"], 0.0,
            normalized_hard["maximum_repair_displacement_m"],
            "maximum_repair_displacement_m", kind,
        ),
    }
    if (
        normalized_review["minimum_largest_component_fraction"]
        < normalized_hard["minimum_largest_component_fraction"]
        or normalized_review["minimum_point_coverage"]
        < normalized_hard["minimum_point_coverage"]
    ):
        raise _invalid(kind, "复核门槛必须严于硬门槛。")
    canonical = {
        "schema_version": "1.0",
        "policy_id": policy_id,
        "policy_kind": item["policy_kind"],
        "production_context": production_context,
        "requires_watertight": _boolean(
            item["requires_watertight"], "requires_watertight", kind
        ),
        "hard_limits": normalized_hard,
        "review_limits": normalized_review,
        "decisions": {
            "hard_failure": "rejected",
            "review_threshold": "review_required",
            "all_passed": "passed",
            "auto_publish_passed": _boolean(
                decisions["auto_publish_passed"],
                "auto_publish_passed",
                kind,
            ),
        },
    }
    return {**canonical, "policy_fingerprint": _fingerprint(canonical, kind)}


def _root(project_root: Path, kind: str, artifact_id: str) -> Path:
    parent = "mesh_configs" if kind == "config" else "mesh_quality_policies"
    return Path(project_root) / "scan_to_cad" / parent / artifact_id


def _filename(kind: str) -> str:
    return "config.json" if kind == "config" else "policy.json"


def _id_field(kind: str) -> str:
    return "config_id" if kind == "config" else "policy_id"


def _fingerprint_field(kind: str) -> str:
    return "config_fingerprint" if kind == "config" else "policy_fingerprint"


def _operation_type(kind: str) -> str:
    return "mesh.config.publish" if kind == "config" else "mesh.quality_policy.publish"


def _event_type(kind: str) -> str:
    return "mesh.config_published" if kind == "config" else "mesh.quality_policy_published"


def _build(kind: str, artifact_id: str, value: object) -> dict:
    if kind == "config":
        return build_mesh_config(artifact_id, value)
    return build_mesh_quality_policy(artifact_id, value)


def _result(kind: str, document: dict) -> dict:
    return {
        _id_field(kind): document[_id_field(kind)],
        _fingerprint_field(kind): document[_fingerprint_field(kind)],
    }


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_canonical_json(path: Path, kind: str) -> dict:
    try:
        _require_plain(path, directory=False)
        raw = path.read_bytes()
        if len(raw) > _MAX_DOCUMENT_BYTES:
            raise ValueError("document too large")
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
            object_pairs_hook=_strict_object,
        )
        if type(value) is not dict or raw != _canonical_json_bytes(value):
            raise ValueError("document is not canonical")
        return value
    except (ModelMatchingError, OSError, UnicodeError, ValueError) as exc:
        raise _integrity(kind, "已发布配置工件无效或已改变。") from exc


def _expected_owner(operation: dict, kind: str, artifact_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "artifact_kind": kind,
        "artifact_id": artifact_id,
        "operation_id": operation["operation_id"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
    }


def _read_optional_owner(path: Path, kind: str) -> dict | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    owner = _read_canonical_json(path, kind)
    if set(owner) != _OWNER_FIELDS:
        raise _integrity(kind, "配置所有者工件无效。")
    return owner


def _validate_audit(
    project_root: Path, kind: str, document: dict, owner: dict
) -> None:
    try:
        snapshot = read_verified_operation_snapshot(
            project_root, document["operation_id"]
        )
        operation = snapshot["operation"]
        events = snapshot["events"]
        matching = [
            event for event in events if event["event_type"] == _event_type(kind)
        ]
        if (
            operation["operation_type"] != _operation_type(kind)
            or operation["status"] != "completed"
            or operation.get("result") != _result(kind, document)
            or owner["operation_id"] != operation["operation_id"]
            or owner["request_id"] != operation["request_id"]
            or owner["request_fingerprint"] != operation["request_fingerprint"]
            or not events
            or events[0]["event_type"] != "operation.started"
            or events[0]["actor_id"] != document["created_by"]
            or events[0]["timestamp"] != document["created_at"]
            or len(matching) != 1
            or matching[0]["details"] != _result(kind, document)
        ):
            raise ValueError("audit binding differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise _integrity(kind, "配置审计证据无效。") from exc


def _load(project_root: Path, kind: str, artifact_id: str) -> dict:
    try:
        artifact_id = validate_identifier(artifact_id, _id_field(kind))
    except (TypeError, ValueError) as exc:
        raise _integrity(kind, "配置身份无效。") from exc
    directory = _root(project_root, kind, artifact_id)
    try:
        _require_plain(directory, directory=True)
        document = _read_canonical_json(directory / _filename(kind), kind)
        owner = _read_canonical_json(directory / "owner.json", kind)
    except FileNotFoundError as exc:
        code = "mesh_config_not_found" if kind == "config" else "mesh_policy_not_found"
        raise _error(code, "指定网格配置或质量策略不存在。") from exc
    identity = _id_field(kind)
    fingerprint = _fingerprint_field(kind)
    payload_fields = (
        (_CONFIG_FIELDS | {identity, fingerprint})
        if kind == "config"
        else (_POLICY_FIELDS | {identity, fingerprint})
    )
    expected_fields = payload_fields | _DOCUMENT_METADATA_FIELDS
    if set(document) != expected_fields or set(owner) != _OWNER_FIELDS:
        raise _integrity(kind, "已发布配置清单结构无效。")
    raw = {
        key: document[key]
        for key in (_CONFIG_FIELDS if kind == "config" else _POLICY_FIELDS)
    }
    canonical = _build(kind, artifact_id, raw)
    if (
        any(document[key] != canonical[key] for key in canonical)
        or document["status"] != "published"
        or owner
        != {
            "schema_version": "1.0",
            "artifact_kind": kind,
            "artifact_id": artifact_id,
            "operation_id": document["operation_id"],
            "request_id": owner["request_id"],
            "request_fingerprint": owner["request_fingerprint"],
        }
        or owner["operation_id"] != document["operation_id"]
    ):
        raise _integrity(kind, "已发布配置内容或所有者无效。")
    try:
        validate_identifier(document["operation_id"], "operation_id")
        validate_identifier(document["created_by"], "created_by")
        validate_identifier(owner["request_id"], "request_id")
        if type(owner["request_fingerprint"]) is not str or len(owner["request_fingerprint"]) != 64:
            raise ValueError("request fingerprint")
    except (TypeError, ValueError) as exc:
        raise _integrity(kind, "已发布配置来源身份无效。") from exc
    _validate_audit(Path(project_root), kind, document, owner)
    return dict(document)


def load_mesh_config(project_root: Path, config_id: str) -> dict:
    return _load(project_root, "config", config_id)


def load_mesh_quality_policy(project_root: Path, policy_id: str) -> dict:
    return _load(project_root, "policy", policy_id)


def _list(project_root: Path, kind: str) -> list[dict]:
    parent = _root(project_root, kind, "placeholder").parent
    try:
        candidates = list(parent.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for candidate in sorted(candidates, key=lambda path: path.name):
        if (candidate / _filename(kind)).exists():
            result.append(_load(project_root, kind, candidate.name))
    return result


def list_mesh_configs(project_root: Path) -> list[dict]:
    return _list(project_root, "config")


def list_mesh_quality_policies(project_root: Path) -> list[dict]:
    return _list(project_root, "policy")


def _record_reuse(
    project_root: Path, kind: str, operation_id: str, document: dict
) -> dict:
    details = {
        **_result(kind, document),
        "producer_operation_id": document["operation_id"],
        "artifact_fingerprint": _file_fingerprint(
            _root(project_root, kind, document[_id_field(kind)]) / _filename(kind)
        ),
    }
    ensure_operation_event(
        project_root, operation_id, f"{_event_type(kind)}.reused", details
    )
    complete_operation(project_root, operation_id, _result(kind, document))
    return document


def _publish(
    project_root: Path,
    *,
    kind: str,
    artifact_id: str,
    value: object,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    canonical = _build(kind, artifact_id, value)
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type=_operation_type(kind),
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=_result(kind, canonical),
    )
    if replayed and operation["status"] == "failed":
        failure = operation.get("error") or {}
        raise _error(
            failure.get("code", "audit_integrity_error"),
            failure.get("message", "配置发布失败。"),
        )
    try:
        try:
            require_any_role(principal, {"expert"})
        except ModelMatchingError as exc:
            raise _error("permission_denied", "只有专家可以发布网格配置。") from exc
        if replayed and operation["status"] == "completed":
            document = _load(root, kind, canonical[_id_field(kind)])
            if operation.get("result") != _result(kind, document):
                raise _integrity(kind, "配置重放结果不一致。")
            return document
        artifact_id = canonical[_id_field(kind)]
        directory = _root(root, kind, artifact_id)
        with model_resource_lock(root, f"mesh-{kind}", artifact_id):
            if (directory / _filename(kind)).is_file():
                document = _load(root, kind, artifact_id)
                if document[_fingerprint_field(kind)] != canonical[_fingerprint_field(kind)]:
                    raise _integrity(kind, "同一配置 ID 已存在不同内容。")
                return _record_reuse(
                    root, kind, operation["operation_id"], document
                )
            directory.mkdir(parents=True, exist_ok=True)
            owner_path = directory / "owner.json"
            expected_owner = _expected_owner(operation, kind, artifact_id)
            actual_owner = _read_optional_owner(owner_path, kind)
            if actual_owner is not None and actual_owner != expected_owner:
                raise _error("operation_busy", "配置目录已由其他操作持有。")
            _publish_exact_json(
                owner_path,
                expected_owner,
                conflict_code="operation_busy",
                conflict_message="配置所有者冲突。",
            )
            snapshot = read_verified_operation_snapshot(
                root, operation["operation_id"]
            )
            started = snapshot["events"][0]
            document = {
                **canonical,
                "operation_id": operation["operation_id"],
                "created_by": started["actor_id"],
                "created_at": started["timestamp"],
                "status": "published",
            }
            _publish_exact_json(
                directory / _filename(kind),
                document,
                conflict_code=(
                    "mesh_config_integrity_error"
                    if kind == "config"
                    else "mesh_policy_integrity_error"
                ),
                conflict_message="配置内容冲突。",
            )
            ensure_operation_event(
                root,
                operation["operation_id"],
                _event_type(kind),
                _result(kind, document),
            )
            complete_operation(
                root, operation["operation_id"], _result(kind, document)
            )
            return _load(root, kind, artifact_id)
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, ModelMatchingError)
            else _integrity(kind, "配置发布失败。")
        )
        current = load_operation(root, operation["operation_id"])
        if (
            current["status"] == "running"
            and error.code
            not in {"operation_busy", "publication_recovery_required"}
        ):
            _record_failure(root, operation["operation_id"], error)
        if error is exc:
            raise
        raise error from exc


def publish_mesh_config(
    project_root: Path,
    *,
    config_id: str,
    config: object,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    return _publish(
        project_root,
        kind="config",
        artifact_id=config_id,
        value=config,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


def publish_mesh_quality_policy(
    project_root: Path,
    *,
    policy_id: str,
    policy: object,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    return _publish(
        project_root,
        kind="policy",
        artifact_id=policy_id,
        value=policy,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
