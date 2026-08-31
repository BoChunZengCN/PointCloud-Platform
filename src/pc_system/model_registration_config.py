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


_CONFIG_FIELDS = {
    "schema_version",
    "engine_name",
    "preprocessing",
    "initial_hypotheses",
    "coarse_registration",
    "fine_registration",
    "transform_validation",
    "residual_metrics",
    "quality_gates",
    "category_overrides",
}
_PREPROCESSING_FIELDS = {
    "voxel_sizes_m",
    "normal_radius_multiplier",
    "fpfh_radius_multiplier",
    "normal_max_nn",
    "fpfh_max_nn",
    "minimum_points",
    "maximum_points",
}
_HYPOTHESIS_FIELDS = {
    "include_identity",
    "include_principal_axes",
    "maximum_hypotheses",
    "rotation_dedup_tolerance_rad",
    "translation_dedup_tolerance_m",
}
_COARSE_FIELDS = {
    "method",
    "fgr_enabled",
    "ransac_n",
    "maximum_iterations",
    "confidence",
    "distance_multiplier",
    "edge_length_ratio",
    "normal_angle_rad",
    "top_n",
    "random_seed",
}
_FINE_FIELDS = {"levels", "relative_fitness", "relative_rmse"}
_FINE_LEVEL_FIELDS = {
    "voxel_size_m",
    "max_correspondence_distance_m",
    "maximum_iterations",
}
_TRANSFORM_FIELDS = {
    "homogeneous_tolerance",
    "orthogonality_tolerance",
    "determinant_tolerance",
    "singular_value_tolerance",
    "maximum_translation_m",
    "maximum_rotation_rad",
}
_RESIDUAL_FIELDS = {"inlier_distance_m", "normal_consistency_minimum"}
_QUALITY_FIELDS = {
    "passed_observed_coverage",
    "passed_model_coverage",
    "review_observed_coverage",
    "review_model_coverage",
    "maximum_inlier_rmse_m",
    "maximum_chamfer_m",
    "maximum_dimension_relative_error",
    "minimum_pose_score_margin",
    "maximum_fine_regression_ratio",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "config_id",
    "engine_name",
    "preprocessing",
    "initial_hypotheses",
    "coarse_registration",
    "fine_registration",
    "transform_validation",
    "residual_metrics",
    "quality_gates",
    "category_overrides",
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
_MAX_CONFIG_BYTES = 4 * 1024 * 1024


def _invalid(message: str) -> ModelMatchingError:
    return ModelMatchingError("registration_config_invalid", message)


def _integrity(message: str) -> ModelMatchingError:
    return ModelMatchingError("artifact_integrity_failed", message)


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
        raise _invalid("Registration configuration is not canonical JSON.") from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_dict(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise _invalid(f"{label} structure is invalid.")
    return dict(value)


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise _invalid(f"{label} is invalid.")
    return value


def _integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid(f"{label} is invalid.")
    return value


def _number(
    value: object,
    minimum: float,
    maximum: float,
    label: str,
    *,
    minimum_inclusive: bool = True,
) -> float:
    if type(value) not in {int, float}:
        raise _invalid(f"{label} is invalid.")
    normalized = float(value)
    valid_minimum = (
        normalized >= minimum if minimum_inclusive else normalized > minimum
    )
    if not math.isfinite(normalized) or not valid_minimum or normalized > maximum:
        raise _invalid(f"{label} is invalid.")
    return normalized


def _identifier(value: object, label: str) -> str:
    try:
        return validate_identifier(value, label)
    except (TypeError, ValueError) as exc:
        raise _invalid(f"{label} is invalid.") from exc


def _normalize_preprocessing(value: object) -> dict:
    item = _exact_dict(value, _PREPROCESSING_FIELDS, "Preprocessing config")
    raw_sizes = item["voxel_sizes_m"]
    if type(raw_sizes) is not list or not 1 <= len(raw_sizes) <= 8:
        raise _invalid("voxel_sizes_m is invalid.")
    sizes = [
        _number(raw, 0.0, 1000.0, "voxel_sizes_m", minimum_inclusive=False)
        for raw in raw_sizes
    ]
    if any(left <= right for left, right in zip(sizes, sizes[1:])):
        raise _invalid("voxel_sizes_m must be strictly descending.")
    minimum_points = _integer(item["minimum_points"], 3, 500_000, "minimum_points")
    maximum_points = _integer(
        item["maximum_points"], minimum_points, 2_000_000, "maximum_points"
    )
    return {
        "voxel_sizes_m": sizes,
        "normal_radius_multiplier": _number(
            item["normal_radius_multiplier"],
            0.0,
            1000.0,
            "normal_radius_multiplier",
            minimum_inclusive=False,
        ),
        "fpfh_radius_multiplier": _number(
            item["fpfh_radius_multiplier"],
            0.0,
            1000.0,
            "fpfh_radius_multiplier",
            minimum_inclusive=False,
        ),
        "normal_max_nn": _integer(item["normal_max_nn"], 3, 10_000, "normal_max_nn"),
        "fpfh_max_nn": _integer(item["fpfh_max_nn"], 3, 10_000, "fpfh_max_nn"),
        "minimum_points": minimum_points,
        "maximum_points": maximum_points,
    }


def _normalize_hypotheses(value: object) -> dict:
    item = _exact_dict(value, _HYPOTHESIS_FIELDS, "Initial hypothesis config")
    return {
        "include_identity": _boolean(item["include_identity"], "include_identity"),
        "include_principal_axes": _boolean(
            item["include_principal_axes"], "include_principal_axes"
        ),
        "maximum_hypotheses": _integer(
            item["maximum_hypotheses"], 1, 96, "maximum_hypotheses"
        ),
        "rotation_dedup_tolerance_rad": _number(
            item["rotation_dedup_tolerance_rad"],
            0.0,
            math.pi,
            "rotation_dedup_tolerance_rad",
            minimum_inclusive=False,
        ),
        "translation_dedup_tolerance_m": _number(
            item["translation_dedup_tolerance_m"],
            0.0,
            1000.0,
            "translation_dedup_tolerance_m",
            minimum_inclusive=False,
        ),
    }


def _normalize_coarse(value: object, maximum_hypotheses: int) -> dict:
    item = _exact_dict(value, _COARSE_FIELDS, "Coarse registration config")
    if item["method"] != "ransac":
        raise _invalid("Coarse registration method is invalid.")
    return {
        "method": "ransac",
        "fgr_enabled": _boolean(item["fgr_enabled"], "fgr_enabled"),
        "ransac_n": _integer(item["ransac_n"], 3, 8, "ransac_n"),
        "maximum_iterations": _integer(
            item["maximum_iterations"], 1, 10_000_000, "maximum_iterations"
        ),
        "confidence": _number(
            item["confidence"], 0.0, 1.0, "confidence", minimum_inclusive=False
        ),
        "distance_multiplier": _number(
            item["distance_multiplier"],
            0.0,
            1000.0,
            "distance_multiplier",
            minimum_inclusive=False,
        ),
        "edge_length_ratio": _number(
            item["edge_length_ratio"],
            0.0,
            1.0,
            "edge_length_ratio",
            minimum_inclusive=False,
        ),
        "normal_angle_rad": _number(
            item["normal_angle_rad"],
            0.0,
            math.pi,
            "normal_angle_rad",
            minimum_inclusive=False,
        ),
        "top_n": _integer(item["top_n"], 1, maximum_hypotheses, "top_n"),
        "random_seed": _integer(
            item["random_seed"], 0, 9_223_372_036_854_775_807, "random_seed"
        ),
    }


def _normalize_fine(value: object) -> dict:
    item = _exact_dict(value, _FINE_FIELDS, "Fine registration config")
    raw_levels = item["levels"]
    if type(raw_levels) is not list or not 1 <= len(raw_levels) <= 8:
        raise _invalid("Fine registration levels are invalid.")
    levels = []
    for raw_level in raw_levels:
        level = _exact_dict(raw_level, _FINE_LEVEL_FIELDS, "Fine registration level")
        levels.append(
            {
                "voxel_size_m": _number(
                    level["voxel_size_m"],
                    0.0,
                    1000.0,
                    "voxel_size_m",
                    minimum_inclusive=False,
                ),
                "max_correspondence_distance_m": _number(
                    level["max_correspondence_distance_m"],
                    0.0,
                    1000.0,
                    "max_correspondence_distance_m",
                    minimum_inclusive=False,
                ),
                "maximum_iterations": _integer(
                    level["maximum_iterations"], 1, 1_000_000, "maximum_iterations"
                ),
            }
        )
    sizes = [level["voxel_size_m"] for level in levels]
    if any(left <= right for left, right in zip(sizes, sizes[1:])):
        raise _invalid("Fine registration voxel sizes must be strictly descending.")
    return {
        "levels": levels,
        "relative_fitness": _number(
            item["relative_fitness"],
            0.0,
            1.0,
            "relative_fitness",
            minimum_inclusive=False,
        ),
        "relative_rmse": _number(
            item["relative_rmse"],
            0.0,
            1.0,
            "relative_rmse",
            minimum_inclusive=False,
        ),
    }


def _normalize_transform(value: object) -> dict:
    item = _exact_dict(value, _TRANSFORM_FIELDS, "Transform validation config")
    tolerances = {
        field: _number(
            item[field], 0.0, 0.1, field, minimum_inclusive=False
        )
        for field in (
            "homogeneous_tolerance",
            "orthogonality_tolerance",
            "determinant_tolerance",
            "singular_value_tolerance",
        )
    }
    return {
        **tolerances,
        "maximum_translation_m": _number(
            item["maximum_translation_m"],
            0.0,
            1_000_000_000.0,
            "maximum_translation_m",
            minimum_inclusive=False,
        ),
        "maximum_rotation_rad": _number(
            item["maximum_rotation_rad"],
            0.0,
            math.pi,
            "maximum_rotation_rad",
            minimum_inclusive=False,
        ),
    }


def _normalize_residual(value: object) -> dict:
    item = _exact_dict(value, _RESIDUAL_FIELDS, "Residual metric config")
    return {
        "inlier_distance_m": _number(
            item["inlier_distance_m"],
            0.0,
            1000.0,
            "inlier_distance_m",
            minimum_inclusive=False,
        ),
        "normal_consistency_minimum": _number(
            item["normal_consistency_minimum"],
            0.0,
            1.0,
            "normal_consistency_minimum",
        ),
    }


def _normalize_quality(value: object) -> dict:
    item = _exact_dict(value, _QUALITY_FIELDS, "Quality gate config")
    result = {
        field: _number(item[field], 0.0, 1.0, field)
        for field in (
            "passed_observed_coverage",
            "passed_model_coverage",
            "review_observed_coverage",
            "review_model_coverage",
            "minimum_pose_score_margin",
        )
    }
    result.update(
        {
            "maximum_inlier_rmse_m": _number(
                item["maximum_inlier_rmse_m"],
                0.0,
                1000.0,
                "maximum_inlier_rmse_m",
                minimum_inclusive=False,
            ),
            "maximum_chamfer_m": _number(
                item["maximum_chamfer_m"],
                0.0,
                1000.0,
                "maximum_chamfer_m",
                minimum_inclusive=False,
            ),
            "maximum_dimension_relative_error": _number(
                item["maximum_dimension_relative_error"],
                0.0,
                10.0,
                "maximum_dimension_relative_error",
            ),
            "maximum_fine_regression_ratio": _number(
                item["maximum_fine_regression_ratio"],
                1.0,
                100.0,
                "maximum_fine_regression_ratio",
            ),
        }
    )
    if (
        result["passed_observed_coverage"]
        < result["review_observed_coverage"]
        or result["passed_model_coverage"] < result["review_model_coverage"]
    ):
        raise _invalid("Quality gate coverage ordering is invalid.")
    return result


def _normalize_overrides(value: object) -> dict:
    if type(value) is not dict:
        raise _invalid("Category overrides structure is invalid.")
    result = {}
    for raw_category, raw_quality in value.items():
        category = _identifier(raw_category, "category_id")
        if category in result:
            raise _invalid("Category override identity is ambiguous.")
        result[category] = _normalize_quality(raw_quality)
    return {key: result[key] for key in sorted(result)}


def build_registration_config(config_id: str, value: object) -> dict:
    config_id = _identifier(config_id, "config_id")
    item = _exact_dict(value, _CONFIG_FIELDS, "Registration config")
    if item["schema_version"] != "1.0":
        raise _invalid("Registration config schema version is invalid.")
    engine_name = _identifier(item["engine_name"], "engine_name")
    preprocessing = _normalize_preprocessing(item["preprocessing"])
    hypotheses = _normalize_hypotheses(item["initial_hypotheses"])
    canonical = {
        "schema_version": "1.0",
        "config_id": config_id,
        "engine_name": engine_name,
        "preprocessing": preprocessing,
        "initial_hypotheses": hypotheses,
        "coarse_registration": _normalize_coarse(
            item["coarse_registration"], hypotheses["maximum_hypotheses"]
        ),
        "fine_registration": _normalize_fine(item["fine_registration"]),
        "transform_validation": _normalize_transform(item["transform_validation"]),
        "residual_metrics": _normalize_residual(item["residual_metrics"]),
        "quality_gates": _normalize_quality(item["quality_gates"]),
        "category_overrides": _normalize_overrides(item["category_overrides"]),
    }
    return {**canonical, "config_fingerprint": _fingerprint(canonical)}


def _config_root(project_root: Path, config_id: str) -> Path:
    return Path(project_root) / "models" / "registration_configs" / config_id


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_canonical_json(path: Path) -> dict:
    try:
        _require_plain(path, directory=False)
        raw = path.read_bytes()
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ValueError("configuration exceeds size limit")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
        if type(value) is not dict or raw != _canonical_json_bytes(value):
            raise ValueError("configuration is not canonical")
        return value
    except (ModelMatchingError, OSError, UnicodeError, ValueError) as exc:
        raise _integrity("Registration configuration artifact is invalid.") from exc


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
        published = [
            event
            for event in events
            if event["event_type"] == "model_registration_config.published"
        ]
        if (
            operation["operation_type"] != "model_registration_config.publish"
            or operation["status"] != "completed"
            or operation.get("result") != _result(manifest)
            or not events
            or events[0]["event_type"] != "operation.started"
            or events[0]["actor_id"] != manifest["created_by"]
            or events[0]["timestamp"] != manifest["created_at"]
            or len(published) != 1
            or published[0]["details"] != _result(manifest)
        ):
            raise ValueError("registration config audit differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise _integrity("Registration configuration audit is invalid.") from exc


def load_registration_config(project_root: Path, config_id: str) -> dict:
    try:
        config_id = validate_identifier(config_id, "config_id")
    except (TypeError, ValueError) as exc:
        raise _integrity("Registration configuration identity is invalid.") from exc
    root = _config_root(project_root, config_id)
    try:
        _require_plain(root, directory=True)
        manifest = _read_canonical_json(root / "registration_config.json")
    except FileNotFoundError as exc:
        raise ModelMatchingError(
            "registration_config_not_found",
            "Registration configuration does not exist.",
        ) from exc
    if set(manifest) != _MANIFEST_FIELDS:
        raise _integrity("Registration configuration manifest is invalid.")
    payload = {
        key: manifest[key]
        for key in _CONFIG_FIELDS
        if key != "schema_version"
    }
    payload["schema_version"] = manifest["schema_version"]
    try:
        canonical = build_registration_config(config_id, payload)
    except ModelMatchingError as exc:
        raise _integrity("Registration configuration values are invalid.") from exc
    if (
        {key: manifest[key] for key in canonical} != canonical
        or manifest.get("status") != "ready"
    ):
        raise _integrity("Registration configuration manifest is invalid.")
    try:
        validate_identifier(manifest["operation_id"], "operation_id")
        validate_identifier(manifest["created_by"], "created_by")
    except (TypeError, ValueError) as exc:
        raise _integrity("Registration configuration provenance is invalid.") from exc
    _validate_audit(Path(project_root), manifest)
    return dict(manifest)


def list_registration_configs(project_root: Path) -> list[dict]:
    parent = Path(project_root) / "models" / "registration_configs"
    try:
        candidates = list(parent.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for candidate in sorted(candidates, key=lambda path: path.name):
        if (candidate / "registration_config.json").is_file():
            result.append(load_registration_config(project_root, candidate.name))
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
    value = _read_canonical_json(path)
    if set(value) != _OWNER_FIELDS:
        raise _integrity("Registration configuration owner is invalid.")
    return value


def _record_reuse(project_root: Path, operation_id: str, manifest: dict) -> dict:
    details = {
        **_result(manifest),
        "producer_operation_id": manifest["operation_id"],
        "manifest_fingerprint": _file_fingerprint(
            _config_root(project_root, manifest["config_id"])
            / "registration_config.json"
        ),
    }
    ensure_operation_event(
        project_root,
        operation_id,
        "model_registration_config.reused",
        details,
    )
    complete_operation(project_root, operation_id, _result(manifest))
    return manifest


def publish_registration_config(
    project_root: Path,
    *,
    config_id: str,
    config: object,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    canonical = build_registration_config(config_id, config)
    request_payload = {
        "config_id": canonical["config_id"],
        "config_fingerprint": canonical["config_fingerprint"],
    }
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type="model_registration_config.publish",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(
            error.get("code", "artifact_integrity_failed"),
            error.get("message", "Registration config publication failed."),
        )
    try:
        require_any_role(principal, {"expert"})
        if replayed and operation["status"] == "completed":
            manifest = load_registration_config(root, canonical["config_id"])
            if operation.get("result") != _result(manifest):
                raise _integrity("Registration config replay result is invalid.")
            return manifest
        candidate = _config_root(root, canonical["config_id"])
        with model_resource_lock(root, "registration-config", canonical["config_id"]):
            if (candidate / "registration_config.json").is_file():
                manifest = load_registration_config(root, canonical["config_id"])
                if manifest["config_fingerprint"] != canonical["config_fingerprint"]:
                    raise _integrity(
                        "Registration config identity already has different content."
                    )
                return _record_reuse(root, operation["operation_id"], manifest)
            candidate.mkdir(parents=True, exist_ok=True)
            owner_path = candidate / "operation_owner.json"
            expected_owner = _expected_owner(operation, canonical["config_id"])
            actual_owner = _read_optional_owner(owner_path)
            if actual_owner is not None and actual_owner != expected_owner:
                raise ModelMatchingError(
                    "operation_busy",
                    "Registration config candidate has another owner.",
                )
            _publish_exact_json(
                owner_path,
                expected_owner,
                conflict_code="operation_busy",
                conflict_message="Registration config owner conflicts.",
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
            _publish_exact_json(
                candidate / "registration_config.json",
                manifest,
                conflict_code="artifact_integrity_failed",
                conflict_message="Registration config content conflicts.",
            )
            ensure_operation_event(
                root,
                operation["operation_id"],
                "model_registration_config.published",
                _result(manifest),
            )
            complete_operation(root, operation["operation_id"], _result(manifest))
            return load_registration_config(root, canonical["config_id"])
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, ModelMatchingError)
            else _integrity("Registration config publication failed.")
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
