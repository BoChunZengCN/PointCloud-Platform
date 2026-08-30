import hashlib
import json
import math
import unicodedata
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
from pc_system.model_release import _load_json, _record_failure, _require_plain
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_sampling import _file_fingerprint, _publish_exact_json


_FEATURE_FIELDS = {
    "schema_version",
    "config_id",
    "algorithm_version",
    "sampling",
    "radial_bins",
    "voxel_grid_size",
    "minimum_points",
    "maximum_points",
    "degenerate_eigenvalue_ratio",
    "ambiguous_axis_relative_gap",
}
_SAMPLING_FIELDS = {"algorithm", "point_count", "random_seed"}
_SCORING_FIELDS = {
    "schema_version",
    "config_id",
    "top_k_default",
    "top_k_maximum",
    "production_minimum_coverage",
    "weights",
    "dimension_penalties",
}
_WEIGHT_FIELDS = {
    "category",
    "terms",
    "manufacturer_model",
    "dimensions",
    "shape",
    "occupancy",
}
_PENALTY_FIELDS = {"model_smaller_multiplier", "model_larger_multiplier"}
_MAPPING_FIELDS = {"schema_version", "config_id", "mappings"}
_MANIFEST_FIELDS = {
    "schema_version",
    "config_id",
    "feature_config",
    "scoring_config",
    "category_mapping",
    "config_fingerprint",
    "operation_id",
    "created_by",
    "created_at",
    "status",
}
_OWNER_FIELDS = {
    "schema_version",
    "config_id",
    "operation_id",
    "request_id",
    "request_fingerprint",
}
_MAX_CONFIG_BYTES = 16 * 1024 * 1024


def _invalid(message: str) -> ModelMatchingError:
    return ModelMatchingError("feature_config_invalid", message)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _invalid("Retrieval configuration is not canonical JSON.") from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_dict(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise _invalid(f"{label} structure is invalid.")
    return dict(value)


def _exact_int(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid(f"{label} is invalid.")
    return value


def _finite_number(value: object, *, positive: bool, label: str) -> float:
    if type(value) not in {int, float}:
        raise _invalid(f"{label} is invalid.")
    normalized = float(value)
    if not math.isfinite(normalized) or (positive and normalized <= 0):
        raise _invalid(f"{label} is invalid.")
    return normalized


def _config_identity(values: list[object]) -> str:
    if any(type(value) is not str for value in values):
        raise _invalid("Retrieval config identity is invalid.")
    try:
        normalized = [validate_identifier(value, "config_id") for value in values]
    except ValueError as exc:
        raise _invalid("Retrieval config identity is invalid.") from exc
    if len(set(normalized)) != 1:
        raise _invalid("Retrieval config identities do not match.")
    return normalized[0]


def _normalize_feature(value: object) -> dict:
    feature = _exact_dict(value, _FEATURE_FIELDS, "Feature config")
    sampling = _exact_dict(feature["sampling"], _SAMPLING_FIELDS, "Sampling config")
    config_id = _config_identity([feature["config_id"]])
    if (
        feature["schema_version"] != "1.0"
        or feature["algorithm_version"] != "phase15b2-feature-v1"
        or sampling["algorithm"] != "sha256_area_weighted_v1"
    ):
        raise _invalid("Feature config version is invalid.")
    minimum_points = _exact_int(
        feature["minimum_points"], 16, 500_000, "minimum_points"
    )
    point_count = _exact_int(
        sampling["point_count"], minimum_points, 500_000, "point_count"
    )
    maximum_points = _exact_int(
        feature["maximum_points"], point_count, 2_000_000, "maximum_points"
    )
    random_seed = _exact_int(
        sampling["random_seed"], 0, 9_223_372_036_854_775_807, "random_seed"
    )
    return {
        "schema_version": "1.0",
        "config_id": config_id,
        "algorithm_version": "phase15b2-feature-v1",
        "sampling": {
            "algorithm": "sha256_area_weighted_v1",
            "point_count": point_count,
            "random_seed": random_seed,
        },
        "radial_bins": _exact_int(feature["radial_bins"], 4, 64, "radial_bins"),
        "voxel_grid_size": _exact_int(
            feature["voxel_grid_size"], 2, 16, "voxel_grid_size"
        ),
        "minimum_points": minimum_points,
        "maximum_points": maximum_points,
        "degenerate_eigenvalue_ratio": _finite_number(
            feature["degenerate_eigenvalue_ratio"],
            positive=True,
            label="degenerate_eigenvalue_ratio",
        ),
        "ambiguous_axis_relative_gap": _finite_number(
            feature["ambiguous_axis_relative_gap"],
            positive=True,
            label="ambiguous_axis_relative_gap",
        ),
    }


def _normalize_scoring(value: object) -> dict:
    scoring = _exact_dict(value, _SCORING_FIELDS, "Scoring config")
    weights = _exact_dict(scoring["weights"], _WEIGHT_FIELDS, "Scoring weights")
    penalties = _exact_dict(
        scoring["dimension_penalties"], _PENALTY_FIELDS, "Dimension penalties"
    )
    config_id = _config_identity([scoring["config_id"]])
    if scoring["schema_version"] != "1.0":
        raise _invalid("Scoring config version is invalid.")
    normalized_weights = {
        key: _finite_number(weights[key], positive=False, label=f"weight {key}")
        for key in sorted(_WEIGHT_FIELDS)
    }
    if any(value < 0 for value in normalized_weights.values()) or not math.isclose(
        sum(normalized_weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-12
    ):
        raise _invalid("Scoring weights must sum to one.")
    normalized_weights = {
        key: normalized_weights[key] / sum(normalized_weights.values())
        for key in sorted(normalized_weights)
    }
    top_k_maximum = _exact_int(
        scoring["top_k_maximum"], 1, 50, "top_k_maximum"
    )
    top_k_default = _exact_int(
        scoring["top_k_default"], 1, top_k_maximum, "top_k_default"
    )
    coverage = _finite_number(
        scoring["production_minimum_coverage"],
        positive=False,
        label="production_minimum_coverage",
    )
    if not 0 <= coverage <= 1:
        raise _invalid("production_minimum_coverage is invalid.")
    return {
        "schema_version": "1.0",
        "config_id": config_id,
        "top_k_default": top_k_default,
        "top_k_maximum": top_k_maximum,
        "production_minimum_coverage": coverage,
        "weights": normalized_weights,
        "dimension_penalties": {
            key: _finite_number(penalties[key], positive=True, label=key)
            for key in sorted(_PENALTY_FIELDS)
        },
    }


def _normalize_mapping(value: object) -> dict:
    mapping = _exact_dict(value, _MAPPING_FIELDS, "Category mapping")
    config_id = _config_identity([mapping["config_id"]])
    if mapping["schema_version"] != "1.0" or type(mapping["mappings"]) is not dict:
        raise _invalid("Category mapping structure is invalid.")
    normalized: dict[str, str] = {}
    for raw_source, raw_target in mapping["mappings"].items():
        if type(raw_source) is not str or type(raw_target) is not str:
            raise _invalid("Category mapping values are invalid.")
        source = unicodedata.normalize("NFKC", raw_source)
        target = unicodedata.normalize("NFKC", raw_target)
        try:
            source = validate_identifier(source, "class_id")
            target = validate_identifier(target, "category_id")
        except ValueError as exc:
            raise _invalid("Category mapping values are invalid.") from exc
        if source in normalized:
            raise _invalid("Category mapping normalization is ambiguous.")
        normalized[source] = target
    return {
        "schema_version": "1.0",
        "config_id": config_id,
        "mappings": {key: normalized[key] for key in sorted(normalized)},
    }


def build_retrieval_config(
    feature: object, scoring: object, category_mapping: object
) -> dict:
    normalized_feature = _normalize_feature(feature)
    normalized_scoring = _normalize_scoring(scoring)
    normalized_mapping = _normalize_mapping(category_mapping)
    config_id = _config_identity(
        [
            normalized_feature["config_id"],
            normalized_scoring["config_id"],
            normalized_mapping["config_id"],
        ]
    )
    value = {
        "schema_version": "1.0",
        "config_id": config_id,
        "feature_config": normalized_feature,
        "scoring_config": normalized_scoring,
        "category_mapping": normalized_mapping,
    }
    return {**value, "config_fingerprint": _fingerprint(value)}


def _config_root(project_root: Path, config_id: str) -> Path:
    return Path(project_root) / "models" / "retrieval_configs" / config_id


def _read_bounded_json(path: Path) -> dict:
    try:
        _require_plain(path, directory=False)
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError("config file exceeds size limit")
        value = _load_json(path)
    except (ModelMatchingError, OSError, ValueError) as exc:
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval configuration could not be read."
        ) from exc
    if type(value) is not dict:
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval configuration is invalid."
        )
    return value


def _result(value: dict) -> dict:
    return {
        "config_id": value["config_id"],
        "config_fingerprint": value["config_fingerprint"],
    }


def _validate_audit(project_root: Path, manifest: dict) -> None:
    try:
        snapshot = read_verified_operation_snapshot(
            project_root, manifest["operation_id"]
        )
        operation = snapshot["operation"]
        events = snapshot["events"]
        matching = [
            event
            for event in events
            if event["event_type"] == "model_retrieval_config.published"
        ]
        if (
            operation["operation_type"] != "model_retrieval_config.publish"
            or operation["status"] != "completed"
            or operation.get("result") != _result(manifest)
            or not events
            or events[0]["event_type"] != "operation.started"
            or events[0]["actor_id"] != manifest["created_by"]
            or events[0]["timestamp"] != manifest["created_at"]
            or len(matching) != 1
            or matching[0]["details"] != _result(manifest)
            or matching[0]["actor_id"] != manifest["created_by"]
        ):
            raise ValueError("config audit binding differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval configuration audit is invalid."
        ) from exc


def load_retrieval_config(project_root: Path, config_id: str) -> dict:
    try:
        config_id = validate_identifier(config_id, "config_id")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval configuration identity is invalid."
        ) from exc
    root = _config_root(project_root, config_id)
    try:
        _require_plain(root, directory=True)
        feature = _read_bounded_json(root / "feature_config.json")
        scoring = _read_bounded_json(root / "scoring_config.json")
        mapping = _read_bounded_json(root / "category_mapping.json")
        manifest = _read_bounded_json(root / "retrieval_config.json")
    except FileNotFoundError as exc:
        raise ModelMatchingError(
            "feature_not_found", "Retrieval configuration does not exist."
        ) from exc
    try:
        canonical = build_retrieval_config(feature, scoring, mapping)
    except ModelMatchingError as exc:
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval configuration is invalid."
        ) from exc
    if (
        set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != "1.0"
        or manifest.get("config_id") != config_id
        or {key: manifest[key] for key in canonical} != canonical
        or manifest.get("status") != "ready"
    ):
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval configuration manifest is invalid."
        )
    try:
        validate_identifier(manifest["operation_id"], "operation_id")
        validate_identifier(manifest["created_by"], "created_by")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval configuration provenance is invalid."
        ) from exc
    _validate_audit(Path(project_root), manifest)
    return dict(manifest)


def list_retrieval_configs(project_root: Path) -> list[dict]:
    parent = Path(project_root) / "models" / "retrieval_configs"
    try:
        candidates = list(parent.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for candidate in sorted(candidates, key=lambda path: path.name):
        if (candidate / "retrieval_config.json").is_file():
            result.append(load_retrieval_config(project_root, candidate.name))
    return result


def _expected_owner(operation: dict, config_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "config_id": config_id,
        "operation_id": operation["operation_id"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
    }


def _read_optional_owner(path: Path) -> dict | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    value = _read_bounded_json(path)
    if set(value) != _OWNER_FIELDS:
        raise ModelMatchingError(
            "feature_integrity_error", "Retrieval config owner is invalid."
        )
    return value


def _record_reuse(project_root: Path, operation_id: str, manifest: dict) -> dict:
    details = {
        **_result(manifest),
        "producer_operation_id": manifest["operation_id"],
        "manifest_fingerprint": _file_fingerprint(
            _config_root(project_root, manifest["config_id"])
            / "retrieval_config.json"
        ),
    }
    ensure_operation_event(
        project_root,
        operation_id,
        "model_retrieval_config.reused",
        details,
    )
    complete_operation(project_root, operation_id, _result(manifest))
    return manifest


def publish_retrieval_config(
    project_root: Path,
    *,
    config_id: str,
    feature: object,
    scoring: object,
    category_mapping: object,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    canonical = build_retrieval_config(feature, scoring, category_mapping)
    try:
        config_id = validate_identifier(config_id, "config_id")
    except (TypeError, ValueError) as exc:
        raise _invalid("Retrieval config identity is invalid.") from exc
    if canonical["config_id"] != config_id:
        raise _invalid("Retrieval config identity does not match its content.")
    request_payload = {
        "config_id": config_id,
        "config_fingerprint": canonical["config_fingerprint"],
    }
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type="model_retrieval_config.publish",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(
            error.get("code", "feature_integrity_error"),
            error.get("message", "Retrieval config publication failed."),
        )
    try:
        require_any_role(principal, {"expert"})
        if replayed and operation["status"] == "completed":
            manifest = load_retrieval_config(root, config_id)
            if operation.get("result") != _result(manifest):
                raise ModelMatchingError(
                    "feature_integrity_error", "Config replay result is invalid."
                )
            return manifest
        candidate = _config_root(root, config_id)
        with model_resource_lock(root, "retrieval-config", config_id):
            if (candidate / "retrieval_config.json").is_file():
                manifest = load_retrieval_config(root, config_id)
                if manifest["config_fingerprint"] != canonical["config_fingerprint"]:
                    raise ModelMatchingError(
                        "feature_integrity_error",
                        "Retrieval config identity already has different content.",
                    )
                return _record_reuse(root, operation["operation_id"], manifest)
            candidate.mkdir(parents=True, exist_ok=True)
            expected_owner = _expected_owner(operation, config_id)
            owner_path = candidate / "operation_owner.json"
            actual_owner = _read_optional_owner(owner_path)
            if actual_owner is not None and actual_owner != expected_owner:
                raise ModelMatchingError(
                    "operation_busy", "Retrieval config candidate has another owner."
                )
            _publish_exact_json(
                owner_path,
                expected_owner,
                conflict_code="operation_busy",
                conflict_message="Retrieval config owner conflicts.",
            )
            snapshot = read_verified_operation_snapshot(root, operation["operation_id"])
            start = snapshot["events"][0]
            manifest = {
                **canonical,
                "operation_id": operation["operation_id"],
                "created_by": start["actor_id"],
                "created_at": start["timestamp"],
                "status": "ready",
            }
            for name, value in (
                ("feature_config.json", canonical["feature_config"]),
                ("scoring_config.json", canonical["scoring_config"]),
                ("category_mapping.json", canonical["category_mapping"]),
                ("retrieval_config.json", manifest),
            ):
                _publish_exact_json(
                    candidate / name,
                    value,
                    conflict_code="feature_integrity_error",
                    conflict_message="Retrieval config content conflicts.",
                )
            ensure_operation_event(
                root,
                operation["operation_id"],
                "model_retrieval_config.published",
                _result(manifest),
            )
            complete_operation(root, operation["operation_id"], _result(manifest))
            return load_retrieval_config(root, config_id)
    except Exception as exc:
        if isinstance(exc, ModelMatchingError):
            error = exc
        else:
            error = ModelMatchingError(
                "feature_integrity_error", "Retrieval config publication failed."
            )
        current = load_operation(root, operation["operation_id"])
        if (
            current["status"] == "running"
            and error.code not in {"operation_busy", "publication_recovery_required"}
        ):
            _record_failure(root, operation["operation_id"], error)
        if error is exc:
            raise
        raise error from exc
