"""经核验扫描版本的不可变检索表达。"""

import hashlib
import json
import math
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_audit import (
    complete_operation, ensure_operation_event, load_operation,
    read_verified_operation_snapshot, start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import _record_failure, _require_plain
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_sampling import _canonical_json_bytes, _file_fingerprint, _publish_exact_json
from pc_system.reference_geometry import select_reference_points
from pc_system.reference_review import approved_review_evidence
from pc_system.reference_store import audit_digest, digest, load_bundle, read_json


_FIELDS = frozenset({
    "schema_version", "representation_id", "representation_type", "model_id",
    "source_version_id", "source_manifest_fingerprint", "source_geometry_fingerprint",
    "source_quality_fingerprint", "source_quality_status", "source_review_id",
    "source_review_fingerprint", "geometry_fingerprint", "generation_config",
    "generation_config_fingerprint", "point_count", "coordinate_unit", "artifact_uri",
    "operation_id", "generated_by", "generated_at", "status",
})
_OWNER_FIELDS = frozenset({
    "schema_version", "model_id", "version_id", "representation_id", "operation_id",
    "request_id", "request_fingerprint",
})
_POINTS_FIELDS = frozenset({"schema_version", "coordinate_unit", "point_count", "points"})


def _error(code: str, message: str) -> ModelMatchingError:
    return ModelMatchingError(code, message)


def _root(root: Path, model_id: str, version_id: str, representation_id: str) -> Path:
    return Path(root) / "models" / model_id / "representations" / version_id / "scanned_reference" / representation_id


def _config(config: object) -> dict:
    if type(config) is not dict or config.get("schema_version") != "1.1":
        raise _error("feature_config_invalid", "扫描参考表达需要 1.1 特征配置。")
    value = config.get("scanned_sampling")
    if type(value) is not dict or set(value) != {"algorithm", "point_count", "random_seed"}:
        raise _error("feature_config_invalid", "扫描选点配置无效。")
    if (value.get("algorithm") != "sha256_point_subset_v1" or type(value.get("point_count")) is not int
            or not 1 <= value["point_count"] <= 500_000 or type(value.get("random_seed")) is not int
            or not 0 <= value["random_seed"] < 2**63):
        raise _error("feature_config_invalid", "扫描选点配置无效。")
    return {"algorithm": value["algorithm"], "point_count": value["point_count"],
            "random_seed": value["random_seed"], "coordinate_unit": "m",
            "coordinate_precision_decimals": 12}


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _result(value: dict) -> dict:
    return {"model_id": value["model_id"], "version_id": value["source_version_id"],
            "representation_id": value["representation_id"]}


def _request(value: dict) -> dict:
    return {"model_id": value["model_id"], "version_id": value["source_version_id"],
            "generation_config": value["generation_config"], "representation_id": value["representation_id"]}


def _load(root: Path, model_id: str, version_id: str, representation_id: str) -> dict:
    directory = _root(root, model_id, version_id, representation_id)
    try:
        _require_plain(directory, directory=True)
        owner = read_json(directory / "operation_owner.json")
        value = read_json(directory / "representation.json")
        points = read_json(directory / "sampled_points.json")
        bundle = load_bundle(root, model_id, version_id)
        review = approved_review_evidence(root, model_id, version_id)
        if (set(owner) != _OWNER_FIELDS or set(value) != _FIELDS or set(points) != _POINTS_FIELDS
                or owner.get("schema_version") != "1.0" or value.get("schema_version") != "1.1"
                or value.get("representation_id") != representation_id or value.get("representation_type") != "scanned_reference"
                or value.get("model_id") != model_id or value.get("source_version_id") != version_id
                or value.get("coordinate_unit") != "m" or value.get("artifact_uri") != "sampled_points.json"
                or value.get("status") != "ready" or value.get("operation_id") != owner.get("operation_id")
                or owner.get("model_id") != model_id or owner.get("version_id") != version_id
                or owner.get("representation_id") != representation_id or value.get("source_manifest_fingerprint") != digest(bundle["manifest"])
                or value.get("source_geometry_fingerprint") != bundle["manifest"]["geometry_fingerprint"]
                or value.get("source_quality_fingerprint") != digest(bundle["quality"])
                or value.get("source_quality_status") != bundle["quality"]["status"]
                or value.get("source_review_id") != review["review_id"] or value.get("source_review_fingerprint") != review["review_fingerprint"]
                or value.get("generation_config_fingerprint") != _fingerprint(value.get("generation_config"))
                or representation_id != "scanned-reference-" + value.get("generation_config_fingerprint", "")
                or value.get("geometry_fingerprint") != _file_fingerprint(directory / "sampled_points.json")
                or points.get("schema_version") != "1.0" or points.get("coordinate_unit") != "m"
                or points.get("point_count") != value.get("point_count") or not isinstance(points.get("points"), list)
                or len(points["points"]) != value["point_count"]
                or any(type(point) is not list or len(point) != 3 or any(type(axis) is not float or not math.isfinite(axis) for axis in point) for point in points["points"])):
            raise ValueError("representation evidence differs")
        snapshot = read_verified_operation_snapshot(root, value["operation_id"])
        operation, events = snapshot["operation"], snapshot["events"]
        if (operation["operation_type"] != "reference_representation.build" or operation["status"] != "completed"
                or operation["request_id"] != owner["request_id"]
                or operation["request_fingerprint"] != audit_digest(_request(value))
                or owner["request_fingerprint"] != operation["request_fingerprint"]
                or operation.get("result") != _result(value)
                or events[0]["actor_id"] != value["generated_by"] or events[0]["timestamp"] != value["generated_at"]):
            raise ValueError("representation audit differs")
        return json.loads(json.dumps(value, ensure_ascii=False))
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise _error("model_representation_integrity_error", "扫描参考表达证据无效。") from exc
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise _error("model_representation_integrity_error", "扫描参考表达证据无效。") from exc


def load_reference_representation(root, model_id, version_id, representation_id) -> dict:
    try:
        return _load(Path(root), validate_identifier(model_id, "model_id"), validate_identifier(version_id, "version_id"), validate_identifier(representation_id, "representation_id"))
    except (TypeError, ValueError) as exc:
        raise _error("model_representation_not_found", "扫描参考表达身份无效。") from exc


def load_reference_representation_points(root, representation: dict) -> list[list[float]]:
    value = load_reference_representation(root, representation["model_id"], representation["source_version_id"], representation["representation_id"])
    if value != representation:
        raise _error("model_representation_integrity_error", "扫描参考表达已改变。")
    points = read_json(_root(Path(root), value["model_id"], value["source_version_id"], value["representation_id"]) / "sampled_points.json")["points"]
    return [list(point) for point in points]


def build_reference_representation(root, *, model_id, version_id, config, principal: Principal,
                                   operation_id, request_id, idempotency_key) -> dict:
    root = Path(root)
    model_id, version_id = validate_identifier(model_id, "model_id"), validate_identifier(version_id, "version_id")
    generation = _config(config)
    bundle = load_bundle(root, model_id, version_id)
    review = approved_review_evidence(root, model_id, version_id)
    generation_fingerprint = _fingerprint(generation)
    representation_id = f"scanned-reference-{generation_fingerprint}"
    payload = {"model_id": model_id, "version_id": version_id, "generation_config": generation, "representation_id": representation_id}
    operation, replayed = start_operation(root, operation_id=operation_id, operation_type="reference_representation.build",
                                          principal=principal, request_id=request_id, idempotency_key=idempotency_key, request_payload=payload)
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise _error(error.get("code", "model_representation_integrity_error"), error.get("message", "扫描参考表达生成失败。"))
    try:
        require_any_role(principal, {"expert"})
        directory = _root(root, model_id, version_id, representation_id)
        with model_resource_lock(root, "reference-representation", model_id, version_id, representation_id):
            if (directory / "representation.json").is_file():
                visible = _load(root, model_id, version_id, representation_id)
                if replayed and operation["status"] == "completed":
                    return visible
                ensure_operation_event(root, operation_id, "reference_representation.reused", {**_result(visible), "producer_operation_id": visible["operation_id"]})
                complete_operation(root, operation_id, _result(visible))
                return visible
            selected = select_reference_points(bundle["normalized"]["points"], point_count=generation["point_count"], random_seed=generation["random_seed"])
            points = {"schema_version": "1.0", "coordinate_unit": "m", "point_count": len(selected), "points": selected}
            snapshot = read_verified_operation_snapshot(root, operation_id)
            started = snapshot["events"][0]
            value = {"schema_version": "1.1", "representation_id": representation_id, "representation_type": "scanned_reference",
                     "model_id": model_id, "source_version_id": version_id, "source_manifest_fingerprint": digest(bundle["manifest"]),
                     "source_geometry_fingerprint": bundle["manifest"]["geometry_fingerprint"], "source_quality_fingerprint": digest(bundle["quality"]),
                     "source_quality_status": bundle["quality"]["status"], "source_review_id": review["review_id"],
                     "source_review_fingerprint": review["review_fingerprint"], "generation_config": generation,
                     "generation_config_fingerprint": generation_fingerprint, "point_count": len(selected), "coordinate_unit": "m",
                     "artifact_uri": "sampled_points.json", "operation_id": operation_id, "generated_by": started["actor_id"],
                     "generated_at": started["timestamp"], "status": "ready"}
            directory.mkdir(parents=True, exist_ok=True)
            owner = {"schema_version": "1.0", "model_id": model_id, "version_id": version_id, "representation_id": representation_id,
                     "operation_id": operation_id, "request_id": operation["request_id"], "request_fingerprint": audit_digest(payload)}
            _publish_exact_json(directory / "operation_owner.json", owner, conflict_code="operation_busy", conflict_message="扫描参考表达所有者冲突。")
            _publish_exact_json(directory / "sampled_points.json", points, conflict_code="model_representation_integrity_error", conflict_message="扫描参考点冲突。")
            value["geometry_fingerprint"] = _file_fingerprint(directory / "sampled_points.json")
            _publish_exact_json(directory / "representation.json", value, conflict_code="model_representation_integrity_error", conflict_message="扫描参考表达冲突。")
            ensure_operation_event(root, operation_id, "reference_representation.published", _result(value))
            complete_operation(root, operation_id, _result(value))
            return _load(root, model_id, version_id, representation_id)
    except Exception as exc:
        error = exc if isinstance(exc, ModelMatchingError) else _error("model_representation_integrity_error", "扫描参考表达生成失败。")
        if load_operation(root, operation_id)["status"] == "running" and error.code not in {"operation_busy", "publication_recovery_required"}:
            _record_failure(root, operation_id, error)
        if error is exc:
            raise
        raise error from exc
