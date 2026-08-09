import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_audit import (
    append_operation_event,
    complete_operation,
    ensure_operation_event,
    fail_operation,
    load_operation,
    read_verified_operation_events,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.request_canonicalization import (
    FieldSpec,
    FrozenRequest,
    FrozenRequestValueError,
    RequestSchema,
    freeze_request,
)


_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "model_id",
        "display_name",
        "category_id",
        "manufacturer",
        "model_number",
        "keywords",
        "tags",
        "lifecycle_status",
        "created_by",
        "operation_id",
        "created_at",
    }
)

MODEL_ASSET_CREATE_SCHEMA = RequestSchema(
    schema_id="model_asset.create",
    schema_version="1.0",
    fields=(
        FieldSpec("model_id", "identifier"),
        FieldSpec("display_name", "text"),
        FieldSpec("category_id", "identifier"),
        FieldSpec("manufacturer", "text"),
        FieldSpec("model_number", "text"),
        FieldSpec("keywords", "term_list"),
        FieldSpec("tags", "term_list"),
    ),
)


def model_asset_path(project_root: Path, model_id: str) -> Path:
    model_id = validate_identifier(model_id, "model_id")
    return Path(project_root) / "models" / model_id / "model_asset.json"


def model_version_dir(
    project_root: Path, model_id: str, version_id: str
) -> Path:
    model_id = validate_identifier(model_id, "model_id")
    version_id = validate_identifier(version_id, "version_id")
    return (
        Path(project_root)
        / "models"
        / model_id
        / "versions"
        / version_id
    )


def _terms(values: tuple[str, ...], label: str) -> list[str]:
    normalized = sorted(
        {value.strip().lower() for value in values if value.strip()}
    )
    if any(len(value) > 128 for value in normalized):
        raise ValueError(
            f"{label} entries must not exceed 128 characters."
        )
    return normalized


def _manifest_terms(values: object, label: str) -> list[str]:
    if type(values) is not list or any(
        type(value) is not str for value in values
    ):
        raise ValueError(f"{label} must be a list of strings.")
    return _terms(tuple(values), label)


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_model_asset(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    )
    temporary_path: Path | None = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.parent.name}.model-asset-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise ModelMatchingError(
                "model_exists", "Model asset identity already exists."
            ) from exc
        published = True
        _fsync_directory(path.parent)
    except ModelMatchingError:
        raise
    except OSError as exc:
        if published:
            raise ModelMatchingError(
                "publication_recovery_required",
                "Model asset is visible but its publication durability "
                "could not be confirmed.",
            ) from exc
        raise ModelMatchingError(
            "model_asset_persistence_error",
            "Model asset could not be published durably.",
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                if not published:
                    pass


def _model_error(exc: Exception) -> ModelMatchingError:
    if isinstance(exc, ModelMatchingError):
        return exc
    if isinstance(exc, ValueError):
        return ModelMatchingError("invalid_model_asset", str(exc))
    return ModelMatchingError(
        "model_asset_create_failed", "Model asset creation failed."
    )


def _audit_error(exc: Exception) -> ModelMatchingError:
    if (
        isinstance(exc, ModelMatchingError)
        and exc.code.startswith("audit_")
    ):
        return exc
    return ModelMatchingError(
        "audit_persistence_error",
        "Model asset was published but its audit operation could not be "
        "finalized.",
    )


def _record_failure(
    project_root: Path,
    operation_id: str,
    error: ModelMatchingError,
) -> None:
    try:
        fail_operation(project_root, operation_id, error.code, str(error))
    except Exception as exc:
        raise ModelMatchingError(
            "audit_persistence_error",
            "Model asset failure could not be recorded durably.",
        ) from exc


def _normalize_model_request(
    frozen_request: FrozenRequest,
) -> dict[str, object]:
    try:
        normalized_model_id = validate_identifier(
            frozen_request.require_identifier_text("model_id"),
            "model_id",
        )
        normalized_category_id = validate_identifier(
            frozen_request.require_identifier_text("category_id"),
            "category_id",
        )
        normalized_display_name = frozen_request.require_text(
            "display_name"
        ).strip()
        if not normalized_display_name:
            raise ValueError("display_name must not be empty.")
        normalized_display_name.encode("utf-8")
        normalized_manufacturer = frozen_request.require_text(
            "manufacturer"
        ).strip()
        normalized_model_number = frozen_request.require_text(
            "model_number"
        ).strip()
        normalized_keywords = _terms(
            frozen_request.require_term_texts("keywords"), "keywords"
        )
        normalized_tags = _terms(
            frozen_request.require_term_texts("tags"), "tags"
        )
    except (FrozenRequestValueError, TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "invalid_model_asset", str(exc)
        ) from exc
    return {
        "model_id": normalized_model_id,
        "display_name": normalized_display_name,
        "category_id": normalized_category_id,
        "manufacturer": normalized_manufacturer,
        "model_number": normalized_model_number,
        "keywords": normalized_keywords,
        "tags": normalized_tags,
    }


def _operation_result(project_root: Path, model_id: str) -> dict:
    path = model_asset_path(project_root, model_id)
    return {
        "model_id": model_id,
        "artifact_path": path.relative_to(project_root).as_posix(),
    }


def _canonical_created_at(
    project_root: Path, operation_id: str
) -> str:
    events = read_verified_operation_events(project_root, operation_id)
    started = [
        event
        for event in events
        if event["event_type"] == "operation.started"
    ]
    if (
        len(started) != 1
        or not events
        or events[0]["event_type"] != "operation.started"
    ):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Canonical model operation has no unique first start event.",
        )
    return started[0]["timestamp"]


def _require_matching_manifest(
    manifest: dict,
    operation: dict,
    normalized: dict[str, object],
    canonical_created_at: str,
) -> None:
    expected = {
        "schema_version": "1.0",
        **normalized,
        "lifecycle_status": "active",
        "created_by": operation["actor_id"],
        "operation_id": operation["operation_id"],
        "created_at": canonical_created_at,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Published model asset does not match its canonical operation.",
        )


def _created_event_details(manifest: dict) -> dict:
    return {
        "model_id": manifest["model_id"],
        "manifest_fingerprint": _canonical_hash(manifest),
    }


def _require_created_event(
    project_root: Path, operation_id: str, manifest: dict
) -> None:
    expected = _created_event_details(manifest)
    existing = [
        event
        for event in read_verified_operation_events(
            project_root, operation_id
        )
        if event["event_type"] == "model_asset.created"
    ]
    if len(existing) != 1 or existing[0]["details"] != expected:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Terminal model operation has no matching creation event.",
        )


def _ensure_created_event(
    project_root: Path, operation_id: str, manifest: dict
) -> None:
    details = _created_event_details(manifest)
    try:
        ensure_operation_event(
            project_root,
            operation_id,
            "model_asset.created",
            details,
        )
    except ModelMatchingError as exc:
        if exc.code == "operation_immutable":
            raise ModelMatchingError(
                "audit_integrity_error",
                "Terminal model operation is missing its creation event.",
            ) from exc
        raise


def _complete_recovered_operation(
    project_root: Path, operation_id: str, result: dict
) -> None:
    try:
        complete_operation(project_root, operation_id, result)
        return
    except Exception as exc:
        try:
            current = load_operation(project_root, operation_id)
        except Exception:
            raise _audit_error(exc) from exc
        if (
            current["status"] == "completed"
            and current.get("result") == result
        ):
            return
        raise _audit_error(exc) from exc


def _replay_model_asset(
    project_root: Path,
    operation: dict,
    frozen_request: FrozenRequest,
) -> dict:
    if operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(
            str(error.get("code") or "model_asset_create_failed"),
            str(error.get("message") or "Model asset creation failed."),
        )
    try:
        normalized = _normalize_model_request(frozen_request)
    except ModelMatchingError as error:
        if operation["status"] == "running":
            _record_failure(
                project_root, operation["operation_id"], error
            )
        raise

    model_id = normalized["model_id"]
    if not isinstance(model_id, str):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Frozen model request has no model identity.",
        )
    path = model_asset_path(project_root, model_id)
    result = _operation_result(project_root, model_id)
    if operation["status"] == "running" and not path.is_file():
        raise ModelMatchingError(
            "operation_busy",
            "The original model asset operation is still running.",
        )
    if operation["status"] == "completed" and operation.get(
        "result"
    ) != result:
        raise ModelMatchingError(
            "audit_integrity_error",
            "Completed model operation result does not match its asset.",
        )
    manifest = load_model_asset(project_root, model_id)
    canonical_created_at = _canonical_created_at(
        project_root, operation["operation_id"]
    )
    _require_matching_manifest(
        manifest, operation, normalized, canonical_created_at
    )
    if operation["status"] == "running":
        _ensure_created_event(
            project_root, operation["operation_id"], manifest
        )
        _complete_recovered_operation(
            project_root, operation["operation_id"], result
        )
    else:
        _require_created_event(
            project_root, operation["operation_id"], manifest
        )
    return manifest


def _record_replay_failure(
    project_root: Path,
    *,
    principal: Principal,
    request_id: str,
    original_operation_id: str,
    requested_operation_id: str,
    error: ModelMatchingError,
) -> None:
    audit_operation_id = f"audit-replay-{uuid.uuid4()}"
    details = {
        "attempt_id": audit_operation_id,
        "attempted_mutation": "model_asset.create",
        "failure_code": error.code,
        "original_operation_id": original_operation_id,
        "requested_operation_id": requested_operation_id,
    }
    started = False
    try:
        _, replayed = start_operation(
            project_root,
            operation_id=audit_operation_id,
            operation_type="model_asset.replay_failure",
            principal=principal,
            request_id=request_id,
            idempotency_key=audit_operation_id,
            request_payload=details,
        )
        if replayed:
            raise ModelMatchingError(
                "audit_integrity_error",
                "Replay failure audit identity was unexpectedly replayed.",
            )
        started = True
        append_operation_event(
            project_root,
            audit_operation_id,
            "model_asset.replay_failed",
            details,
        )
        fail_operation(
            project_root,
            audit_operation_id,
            error.code,
            str(error),
        )
    except Exception as exc:
        if started:
            try:
                fail_operation(
                    project_root,
                    audit_operation_id,
                    "audit_persistence_error",
                    "Replay failure audit could not be made durable.",
                )
            except Exception:
                pass
        raise ModelMatchingError(
            "audit_persistence_error",
            "Replay failure audit could not be made durable.",
        ) from exc


def create_model_asset(
    project_root: Path,
    *,
    model_id: str,
    display_name: str,
    category_id: str,
    manufacturer: str,
    model_number: str,
    keywords: list[str],
    tags: list[str],
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    project_root = Path(project_root)
    operation_id = validate_identifier(operation_id, "operation_id")
    frozen_request = freeze_request(
        MODEL_ASSET_CREATE_SCHEMA,
        {
            "model_id": model_id,
            "display_name": display_name,
            "category_id": category_id,
            "manufacturer": manufacturer,
            "model_number": model_number,
            "keywords": keywords,
            "tags": tags,
        },
    )
    operation, replayed = start_operation(
        project_root,
        operation_id=operation_id,
        operation_type="model_asset.create",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=frozen_request.to_audit_payload(),
    )
    if replayed:
        try:
            require_any_role(principal, {"expert"})
            return _replay_model_asset(
                project_root, operation, frozen_request
            )
        except Exception as exc:
            error = _model_error(exc)
            _record_replay_failure(
                project_root,
                principal=principal,
                request_id=request_id,
                original_operation_id=operation["operation_id"],
                requested_operation_id=operation_id,
                error=error,
            )
            if error is exc:
                raise
            raise error from exc

    audited_operation_id = operation["operation_id"]
    try:
        require_any_role(principal, {"expert"})
        normalized = _normalize_model_request(frozen_request)
        normalized_model_id = normalized["model_id"]
        if type(normalized_model_id) is not str:
            raise ModelMatchingError(
                "audit_integrity_error",
                "Frozen model request has no model identity.",
            )

        canonical_created_at = _canonical_created_at(
            project_root, audited_operation_id
        )
        manifest = {
            "schema_version": "1.0",
            **normalized,
            "lifecycle_status": "active",
            "created_by": principal.actor_id,
            "operation_id": audited_operation_id,
            "created_at": canonical_created_at,
        }
        path = model_asset_path(project_root, normalized_model_id)
        _publish_model_asset(path, manifest)
    except Exception as exc:
        error = _model_error(exc)
        if error.code == "publication_recovery_required":
            if error is exc:
                raise
            raise error from exc
        _record_failure(project_root, audited_operation_id, error)
        if error is exc:
            raise
        raise error from exc
    try:
        append_operation_event(
            project_root,
            audited_operation_id,
            "model_asset.created",
            {
                "model_id": normalized_model_id,
                "manifest_fingerprint": _canonical_hash(manifest),
            },
        )
        complete_operation(
            project_root,
            audited_operation_id,
            _operation_result(project_root, normalized_model_id),
        )
    except Exception as exc:
        raise _audit_error(exc) from exc
    return manifest


def _validate_manifest(manifest: object, expected_model_id: str) -> dict:
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
        raise ModelMatchingError(
            "model_asset_integrity_error",
            "Model asset manifest has an invalid structure.",
        )
    if (
        manifest["schema_version"] != "1.0"
        or manifest["model_id"] != expected_model_id
        or manifest["lifecycle_status"] != "active"
    ):
        raise ModelMatchingError(
            "model_asset_integrity_error",
            "Model asset manifest identity is invalid.",
        )
    for field in (
        "display_name",
        "category_id",
        "manufacturer",
        "model_number",
        "created_by",
        "operation_id",
        "created_at",
    ):
        if not isinstance(manifest[field], str):
            raise ModelMatchingError(
                "model_asset_integrity_error",
                "Model asset manifest contains invalid metadata.",
            )
    try:
        validate_identifier(manifest["model_id"], "model_id")
        validate_identifier(manifest["category_id"], "category_id")
        if not manifest["display_name"].strip():
            raise ValueError("Model display name is empty.")
        validate_identifier(manifest["created_by"], "created_by")
        validate_identifier(manifest["operation_id"], "operation_id")
        created_at = datetime.fromisoformat(manifest["created_at"])
        if (
            created_at.tzinfo is None
            or created_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("Model creation timestamp is not UTC.")
        if manifest["keywords"] != _manifest_terms(
            manifest["keywords"], "keywords"
        ) or manifest["tags"] != _manifest_terms(
            manifest["tags"], "tags"
        ):
            raise ValueError("Model terms are not canonical.")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_asset_integrity_error",
            "Model asset manifest contains invalid metadata.",
        ) from exc
    return dict(manifest)


def load_model_asset(project_root: Path, model_id: str) -> dict:
    normalized_model_id = validate_identifier(model_id, "model_id")
    path = model_asset_path(project_root, normalized_model_id)
    try:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except FileNotFoundError as exc:
        raise ModelMatchingError(
            "model_not_found", "Model asset does not exist."
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelMatchingError(
            "model_asset_integrity_error",
            "Model asset manifest could not be read.",
        ) from exc
    return _validate_manifest(manifest, normalized_model_id)


def list_model_assets(project_root: Path) -> list[dict]:
    models_root = Path(project_root) / "models"
    assets: list[dict] = []
    try:
        if not models_root.exists():
            return []
        candidates = sorted(
            models_root.iterdir(), key=lambda path: path.name
        )
        for candidate in candidates:
            if (
                not candidate.is_dir()
                or candidate.name.startswith(".")
                or not (candidate / "model_asset.json").is_file()
            ):
                continue
            try:
                assets.append(
                    load_model_asset(project_root, candidate.name)
                )
            except ValueError:
                continue
    except OSError as exc:
        raise ModelMatchingError(
            "model_asset_integrity_error",
            "Model catalog could not be read.",
        ) from exc
    return sorted(assets, key=lambda asset: asset["model_id"])
