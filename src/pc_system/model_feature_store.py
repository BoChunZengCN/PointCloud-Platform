import hashlib
import json
import math
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_features import extract_geometric_features, feature_vector_fingerprint
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    load_operation,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import _load_json, _record_failure, _require_plain, list_model_releases
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_retrieval_config import load_retrieval_config
from pc_system.model_retrieval_input import _reload_retrieval_object, load_retrieval_object
from pc_system.model_sampling import (
    _file_fingerprint,
    _publish_exact_json,
    load_sampled_representation,
)


_OWNER_FIELDS = {
    "schema_version",
    "feature_id",
    "feature_type",
    "operation_id",
    "request_id",
    "request_fingerprint",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "feature_id",
    "feature_type",
    "source",
    "feature_config_id",
    "config_fingerprint",
    "algorithm_version",
    "features",
    "feature_vector_fingerprint",
    "operation_id",
    "generated_by",
    "generated_at",
    "status",
}
_MAX_FEATURE_BYTES = 16 * 1024 * 1024


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
        raise ModelMatchingError(
            "feature_integrity_error", "Feature artifact is not canonical JSON."
        ) from exc


def _feature_id(payload: dict) -> str:
    return f"f-{hashlib.sha256(_canonical_bytes(payload)).hexdigest()[:32]}"


def _identifier(value: object, label: str) -> str:
    try:
        return validate_identifier(value, label)
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("feature_integrity_error", "Feature identity is invalid.") from exc


def _model_base(root: Path, source: dict) -> Path:
    return (
        root
        / "models"
        / source["model_id"]
        / "features"
        / source["version_id"]
        / source["representation_id"]
    )


def _object_base(root: Path, source: dict) -> Path:
    return (
        root
        / "reports"
        / "model_retrieval_features"
        / source["asset_id"]
        / source["source_id"]
        / source["instance_id"]
    )


def _base(root: Path, feature_type: str, source: dict) -> Path:
    if feature_type == "model":
        return _model_base(root, source)
    if feature_type == "object":
        return _object_base(root, source)
    raise ModelMatchingError("feature_integrity_error", "Feature type is invalid.")


def _read_json(path: Path) -> dict:
    try:
        _require_plain(path, directory=False)
        if path.stat().st_size > _MAX_FEATURE_BYTES:
            raise ValueError("feature artifact too large")
        value = _load_json(path)
    except (FileNotFoundError, OSError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "feature_integrity_error", "Feature artifact could not be read."
        ) from exc
    if type(value) is not dict:
        raise ModelMatchingError("feature_integrity_error", "Feature artifact is invalid.")
    return value


def _sampled_points(root: Path, representation: dict) -> list[dict]:
    path = (
        root
        / "models"
        / representation["model_id"]
        / "representations"
        / representation["source_version_id"]
        / "cad_sampled"
        / representation["representation_id"]
        / "sampled_points.json"
    )
    value = _read_json(path)
    points = value.get("points")
    if type(points) is not list or len(points) != representation["point_count"]:
        raise ModelMatchingError(
            "feature_integrity_error", "Sampled model points are invalid."
        )
    normalized = []
    for point in points:
        if (
            type(point) is not list
            or len(point) != 3
            or any(type(item) not in {int, float} or not math.isfinite(float(item)) for item in point)
        ):
            raise ModelMatchingError(
                "feature_integrity_error", "Sampled model point is invalid."
            )
        normalized.append({"x": float(point[0]), "y": float(point[1]), "z": float(point[2])})
    return normalized


def _model_source(
    root: Path, model_id: str, version_id: str, representation_id: str
) -> tuple[dict, list[dict]]:
    representation = load_sampled_representation(
        root, model_id, version_id, representation_id
    )
    if not any(
        release["version_id"] == version_id
        for release in list_model_releases(root, model_id)
    ):
        raise ModelMatchingError(
            "feature_not_found", "Model version has no verified release."
        )
    representation_path = (
        root
        / "models"
        / model_id
        / "representations"
        / version_id
        / "cad_sampled"
        / representation_id
        / "representation.json"
    )
    source = {
        "model_id": model_id,
        "version_id": version_id,
        "representation_id": representation_id,
        "source_manifest_fingerprint": representation["source_manifest_fingerprint"],
        "source_geometry_fingerprint": representation["source_geometry_fingerprint"],
        "representation_geometry_fingerprint": representation["geometry_fingerprint"],
        "representation_fingerprint": _file_fingerprint(representation_path),
    }
    return source, _sampled_points(root, representation)


def _object_source(value: dict) -> tuple[dict, list[dict]]:
    source = {
        key: value[key]
        for key in (
            "source_kind",
            "asset_id",
            "source_id",
            "instance_id",
            "class_id",
            "category_trust",
            "classification_source",
            "coordinate_unit",
            "source_fingerprint",
            "object_fingerprint",
            "point_count",
        )
    }
    return source, list(value["points"])


def _expected_manifest(
    *, feature_type: str, source: dict, config: dict, features: dict, operation: dict
) -> dict:
    identity_payload = {
        "schema_version": "1.0",
        "feature_type": feature_type,
        "source": source,
        "config_fingerprint": config["config_fingerprint"],
        "algorithm_version": config["feature_config"]["algorithm_version"],
    }
    feature_id = _feature_id(identity_payload)
    snapshot = read_verified_operation_snapshot(Path(operation["project_root"]), operation["operation_id"])
    started = snapshot["events"][0]
    return {
        "schema_version": "1.0",
        "feature_id": feature_id,
        "feature_type": feature_type,
        "source": source,
        "feature_config_id": config["config_id"],
        "config_fingerprint": config["config_fingerprint"],
        "algorithm_version": config["feature_config"]["algorithm_version"],
        "features": features,
        "feature_vector_fingerprint": feature_vector_fingerprint(features),
        "operation_id": operation["operation_id"],
        "generated_by": started["actor_id"],
        "generated_at": started["timestamp"],
        "status": "ready",
    }


def _result(manifest: dict) -> dict:
    return {
        "feature_id": manifest["feature_id"],
        "feature_vector_fingerprint": manifest["feature_vector_fingerprint"],
    }


def _owner(operation: dict, feature_type: str, feature_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "feature_id": feature_id,
        "feature_type": feature_type,
        "operation_id": operation["operation_id"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
    }


def _read_optional_owner(path: Path) -> dict | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    value = _read_json(path)
    if set(value) != _OWNER_FIELDS:
        raise ModelMatchingError("feature_integrity_error", "Feature owner is invalid.")
    return value


def _source_identity(feature_type: str, identity: dict) -> dict:
    if type(identity) is not dict:
        raise ModelMatchingError("feature_integrity_error", "Feature identity is invalid.")
    fields = (
        ("model_id", "version_id", "representation_id")
        if feature_type == "model"
        else ("asset_id", "source_id", "instance_id")
        if feature_type == "object"
        else ()
    )
    if not fields or not set(fields).issubset(identity) or set(identity) - {*fields, "feature_id"}:
        raise ModelMatchingError("feature_integrity_error", "Feature identity is invalid.")
    return {field: _identifier(identity[field], field) for field in fields}


def _reload_source(root: Path, feature_type: str, source: dict) -> tuple[dict, list[dict]]:
    if feature_type == "model":
        return _model_source(
            root, source["model_id"], source["version_id"], source["representation_id"]
        )
    query = _reload_retrieval_object(
        root,
        source_kind=source["source_kind"],
        asset_id=source["asset_id"],
        source_id=source["source_id"],
        instance_id=source["instance_id"],
    )
    return _object_source(query)


def _validate_audit(root: Path, manifest: dict, owner: dict) -> None:
    try:
        snapshot = read_verified_operation_snapshot(root, manifest["operation_id"])
        operation = snapshot["operation"]
        events = snapshot["events"]
        event_type = f"{manifest['feature_type']}_feature.published"
        matching = [event for event in events if event["event_type"] == event_type]
        if (
            operation["operation_type"] != f"{manifest['feature_type']}_feature.publish"
            or operation["status"] != "completed"
            or operation.get("result") != _result(manifest)
            or operation["request_id"] != owner["request_id"]
            or operation["request_fingerprint"] != owner["request_fingerprint"]
            or not events
            or events[0]["event_type"] != "operation.started"
            or events[0]["actor_id"] != manifest["generated_by"]
            or events[0]["timestamp"] != manifest["generated_at"]
            or len(matching) != 1
            or matching[0]["details"] != _result(manifest)
        ):
            raise ValueError("feature audit differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "feature_integrity_error", "Feature audit evidence is invalid."
        ) from exc


def load_feature(
    project_root: Path, *, feature_type: str, identity: dict
) -> dict:
    root = Path(project_root)
    base_identity = _source_identity(feature_type, identity)
    if "feature_id" not in identity:
        raise ModelMatchingError("feature_not_found", "Feature identity is incomplete.")
    feature_id = _identifier(identity["feature_id"], "feature_id")
    feature_root = _base(root, feature_type, base_identity) / feature_id
    try:
        _require_plain(feature_root, directory=True)
    except FileNotFoundError as exc:
        raise ModelMatchingError("feature_not_found", "Feature does not exist.") from exc
    owner = _read_json(feature_root / "operation_owner.json")
    manifest = _read_json(feature_root / "feature.json")
    if (
        set(owner) != _OWNER_FIELDS
        or set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != "1.0"
        or manifest.get("feature_id") != feature_id
        or manifest.get("feature_type") != feature_type
        or manifest.get("status") != "ready"
        or owner.get("schema_version") != "1.0"
        or owner.get("feature_id") != feature_id
        or owner.get("feature_type") != feature_type
        or owner.get("operation_id") != manifest.get("operation_id")
    ):
        raise ModelMatchingError("feature_integrity_error", "Feature manifest is invalid.")
    try:
        config = load_retrieval_config(root, manifest["feature_config_id"])
        current_source, points = _reload_source(root, feature_type, manifest["source"])
        features = extract_geometric_features(points, config["feature_config"])
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "feature_integrity_error", "Feature source evidence is invalid."
        ) from exc
    identity_payload = {
        "schema_version": "1.0",
        "feature_type": feature_type,
        "source": current_source,
        "config_fingerprint": config["config_fingerprint"],
        "algorithm_version": config["feature_config"]["algorithm_version"],
    }
    if (
        current_source != manifest["source"]
        or config["config_fingerprint"] != manifest["config_fingerprint"]
        or config["feature_config"]["algorithm_version"] != manifest["algorithm_version"]
        or _feature_id(identity_payload) != feature_id
        or features != manifest["features"]
        or feature_vector_fingerprint(features) != manifest["feature_vector_fingerprint"]
    ):
        raise ModelMatchingError("feature_integrity_error", "Feature evidence differs.")
    _validate_audit(root, manifest, owner)
    return json.loads(json.dumps(manifest, ensure_ascii=False))


def list_features(
    project_root: Path, *, feature_type: str, identity: dict
) -> list[dict]:
    root = Path(project_root)
    source_identity = _source_identity(feature_type, identity)
    parent = _base(root, feature_type, source_identity)
    try:
        candidates = sorted(parent.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        return []
    result = []
    for candidate in candidates:
        if not (candidate / "feature.json").is_file():
            continue
        item_identity = {**source_identity, "feature_id": candidate.name}
        try:
            result.append(load_feature(root, feature_type=feature_type, identity=item_identity))
        except ModelMatchingError as exc:
            if exc.code != "feature_integrity_error":
                raise
            try:
                manifest = _read_json(candidate / "feature.json")
                if load_operation(root, manifest["operation_id"])["status"] == "running":
                    continue
            except (KeyError, ModelMatchingError):
                pass
            raise
    return result


def _publish(
    root: Path,
    *,
    feature_type: str,
    source: dict,
    points: list[dict],
    config: dict,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    features = extract_geometric_features(points, config["feature_config"])
    identity_payload = {
        "schema_version": "1.0",
        "feature_type": feature_type,
        "source": source,
        "config_fingerprint": config["config_fingerprint"],
        "algorithm_version": config["feature_config"]["algorithm_version"],
    }
    feature_id = _feature_id(identity_payload)
    request_payload = {
        "feature_id": feature_id,
        "feature_type": feature_type,
        "source": source,
        "config_fingerprint": config["config_fingerprint"],
    }
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type=f"{feature_type}_feature.publish",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    operation = {**operation, "project_root": root}
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(error.get("code", "feature_integrity_error"), error.get("message", "Feature publication failed."))
    identity = {
        **{key: source[key] for key in (("model_id", "version_id", "representation_id") if feature_type == "model" else ("asset_id", "source_id", "instance_id"))},
        "feature_id": feature_id,
    }
    try:
        require_any_role(principal, {"expert"} if feature_type == "model" else {"expert", "operator"})
        if replayed and operation["status"] == "completed":
            return load_feature(root, feature_type=feature_type, identity=identity)
        manifest = _expected_manifest(
            feature_type=feature_type,
            source=source,
            config=config,
            features=features,
            operation=operation,
        )
        candidate = _base(root, feature_type, source) / feature_id
        with model_resource_lock(root, "feature", feature_type, feature_id):
            candidate.mkdir(parents=True, exist_ok=True)
            expected_owner = _owner(operation, feature_type, feature_id)
            actual_owner = _read_optional_owner(candidate / "operation_owner.json")
            if (candidate / "feature.json").is_file() and actual_owner != expected_owner:
                visible = load_feature(root, feature_type=feature_type, identity=identity)
                if (
                    visible["source"] != source
                    or visible["config_fingerprint"] != config["config_fingerprint"]
                    or visible["features"] != features
                ):
                    raise ModelMatchingError(
                        "feature_integrity_error", "Feature identity collision detected."
                    )
                ensure_operation_event(
                    root,
                    operation["operation_id"],
                    f"{feature_type}_feature.reused",
                    {**_result(visible), "producer_operation_id": visible["operation_id"]},
                )
                complete_operation(root, operation["operation_id"], _result(visible))
                return visible
            if actual_owner is not None and actual_owner != expected_owner:
                raise ModelMatchingError("operation_busy", "Feature candidate has another owner.")
            _publish_exact_json(
                candidate / "operation_owner.json",
                expected_owner,
                conflict_code="operation_busy",
                conflict_message="Feature owner conflicts.",
            )
            _publish_exact_json(
                candidate / "feature.json",
                manifest,
                conflict_code="feature_integrity_error",
                conflict_message="Feature content conflicts.",
            )
            ensure_operation_event(
                root,
                operation["operation_id"],
                f"{feature_type}_feature.published",
                _result(manifest),
            )
            complete_operation(root, operation["operation_id"], _result(manifest))
            return load_feature(root, feature_type=feature_type, identity=identity)
    except Exception as exc:
        error = exc if isinstance(exc, ModelMatchingError) else ModelMatchingError(
            "feature_integrity_error", "Feature publication failed."
        )
        current = load_operation(root, operation["operation_id"])
        if current["status"] == "running" and error.code not in {"operation_busy", "publication_recovery_required"}:
            _record_failure(root, operation["operation_id"], error)
        if error is exc:
            raise
        raise error from exc


def publish_model_feature(
    project_root: Path,
    *,
    model_id: str,
    version_id: str,
    representation_id: str,
    config_id: str,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    model_id = _identifier(model_id, "model_id")
    version_id = _identifier(version_id, "version_id")
    representation_id = _identifier(representation_id, "representation_id")
    source, points = _model_source(root, model_id, version_id, representation_id)
    config = load_retrieval_config(root, config_id)
    return _publish(
        root,
        feature_type="model",
        source=source,
        points=points,
        config=config,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


def publish_object_feature(
    project_root: Path,
    *,
    source_kind: str,
    asset_id: str,
    source_id: str,
    instance_id: str,
    config_id: str,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    query = load_retrieval_object(
        root,
        source_kind=source_kind,
        asset_id=asset_id,
        source_id=source_id,
        instance_id=instance_id,
        principal=principal,
    )
    source, points = _object_source(query)
    config = load_retrieval_config(root, config_id)
    return _publish(
        root,
        feature_type="object",
        source=source,
        points=points,
        config=config,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
