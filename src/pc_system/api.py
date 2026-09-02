import json
import os
import stat as stat_module
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect

from pc_system.config import ProjectConfig
from pc_system.model_match_decision import decide_model_match, supersede_model_binding, restore_model_binding
from pc_system.model_decision_queue import (
    crop_model_binding, list_model_decision_items, load_model_decision_item, load_model_bindings,
)
from pc_system.identifiers import validate_identifier
from pc_system.job_runner import JOB_STATUSES, create_job_from_plan, load_job, mark_step_status, read_job_events, write_job, write_job_event
from pc_system.model_import import import_model_version, list_model_versions
from pc_system.model_feature_index import (
    build_model_feature_index,
    list_model_feature_indexes,
)
from pc_system.model_index_release import (
    list_model_feature_index_releases,
    release_model_feature_index,
)
from pc_system.model_library import (
    create_model_asset,
    list_model_assets,
    load_model_asset,
)
from pc_system.model_matching_audit import (
    read_verified_operation_snapshot,
    record_denied_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import (
    ALLOWED_ROLES,
    Principal,
    require_any_role,
    resolve_principal,
)
from pc_system.model_release import (
    list_model_releases,
    load_current_model_release,
    release_model_version,
)
from pc_system.model_registration import (
    load_model_registration,
    register_model_candidate,
)
from pc_system.model_registration_config import (
    list_registration_configs,
    publish_registration_config,
)
from pc_system.model_registration_open3d import resolve_registration_engine
from pc_system.model_retrieval import (
    load_model_retrieval,
    retrieve_model_candidates,
)
from pc_system.model_retrieval_config import (
    list_retrieval_configs,
    publish_retrieval_config,
)
from pc_system.phase11_report_center import build_report_center
from pc_system.segmentation_correction_events import (
    apply_correction_event,
    read_correction_events,
)
from pc_system.segmentation_correction_releases import (
    list_correction_releases,
    load_correction_release,
    publish_correction_release,
    retry_publication_tasks,
    transition_correction_session,
)
from pc_system.segmentation_corrections import (
    CorrectionError,
    _session_dir,
    create_correction_session,
    list_correction_sessions,
    load_correction_objects,
    load_correction_points,
    load_correction_session,
)


def _registry_path(project_root: Path) -> Path:
    """返回项目资产索引路径。"""

    return ProjectConfig(project_root=project_root).paths()["assets"] / "asset_index.json"


def _load_registry(project_root: Path) -> dict:
    """读取资产索引；缺失时返回空 registry，便于前端先启动。"""

    path = _registry_path(project_root)
    if not path.exists():
        return {"schema_version": "1.0", "asset_count": 0, "assets": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _asset_or_404(project_root: Path, asset_id: str) -> dict:
    """从 registry 查找资产；缺失时返回 404。"""

    _validate_api_identifier(asset_id, "asset_id")
    registry = _load_registry(project_root)
    for asset in registry["assets"]:
        if asset["asset_id"] == asset_id:
            return asset
    raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")


def _read_json_or_404(path: Path, label: str) -> dict:
    """读取 JSON 文件；缺失时返回 API 404。"""

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{label} not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _production_dir(project_root: Path, asset_id: str) -> Path:
    """生产运行报告目录。"""

    return project_root / "reports" / "production_runs" / _validate_api_identifier(asset_id, "asset_id")


def _segmentation_runs_dir(project_root: Path, asset_id: str) -> Path:
    """返回资产的版本化分割运行目录。"""

    return project_root / "reports" / "segmentation_runs" / _validate_api_identifier(asset_id, "asset_id")


def _validate_api_identifier(value: str, label: str) -> str:
    """把核心标识符校验错误转换为稳定的 API 400。"""

    try:
        return validate_identifier(value, label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _file_kind(path: Path) -> str:
    """根据扩展名判断交付物类型。"""

    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".json" and "manifest" in path.name.lower():
        return "manifest"
    if suffix == ".json":
        return "json"
    if suffix in {".md", ".pdf"}:
        return "report"
    return "file"


def _delivery_output(project_root: Path, relative_path: str | None) -> dict:
    """返回单个交付物的真实存在性与类型。"""

    if not relative_path:
        return {"path": "", "exists": False, "kind": "missing"}
    path = project_root / relative_path
    return {"path": relative_path, "exists": path.exists(), "kind": _file_kind(path)}




def _jobs_dir(project_root: Path, asset_id: str) -> Path:
    """返回资产 job 状态目录，保持 API 与 CLI 的目录约定一致。"""

    return ProjectConfig(project_root=project_root).paths()["reports"] / "jobs" / _validate_api_identifier(asset_id, "asset_id")


def _job_path(project_root: Path, asset_id: str, job_id: str) -> Path:
    """返回单个 job JSON 文件路径。"""

    return _jobs_dir(project_root, asset_id) / f"{_validate_api_identifier(job_id, 'job_id')}.json"

def _summarize_jobs(jobs: list[dict]) -> dict:
    """汇总 job 列表，给前端提供可直接渲染的只读状态。"""

    status_summary: dict[str, int] = {}
    for job in jobs:
        status = job.get("status", "unknown")
        status_summary[status] = status_summary.get(status, 0) + 1
    return {
        "job_count": len(jobs),
        "latest_job": jobs[-1] if jobs else None,
        "status_summary": status_summary,
    }


def _correction_http_error(exc: CorrectionError) -> HTTPException:
    if exc.code in {"session_locked", "session_busy"}:
        status_code = 423
    elif exc.code.endswith("_not_found") or exc.code in {
        "release_not_found",
        "session_not_found",
    }:
        status_code = 404
    elif exc.code in {
        "stale_revision",
        "session_exists",
        "active_session_exists",
        "release_exists",
        "derived_benchmark_exists",
        "feedback_release_exists",
        "invalid_session_state",
        "invalid_session_transition",
        "session_immutable",
    }:
        status_code = 409
    else:
        status_code = 400
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


_PHASE15_NOT_FOUND = {
    "decision_not_found", "decision_item_not_found", "binding_not_found",
    "feature_not_found",
    "model_not_found",
    "model_index_release_not_found",
    "model_release_not_found",
    "model_version_not_found",
    "operation_not_found",
    "retrieval_object_not_found",
    "registration_config_not_found",
    "model_registration_not_found",
}
_PHASE15_CONFLICT = {
    "decision_conflict", "binding_exists", "binding_stale", "binding_chain_invalid",
    "idempotency_conflict",
    "model_exists",
    "model_version_exists",
    "model_release_exists",
    "model_index_coverage_rejected",
    "model_index_not_ready",
    "model_index_release_conflict",
    "model_index_stale",
    "operation_busy",
    "operation_exists",
    "stale_model_release",
    "object_fingerprint_stale",
    "artifact_integrity_failed",
}
_PHASE15_SERVICE_UNAVAILABLE = {
    "audit_persistence_error",
    "mesh_engine_unavailable",
    "model_asset_persistence_error",
    "model_staging_quota_exceeded",
    "model_version_cleanup_required",
    "model_version_reservation_error",
    "model_version_reservation_integrity_error",
    "operation_persistence_failed",
    "publication_recovery_required",
    "registration_engine_unavailable",
    "registration_engine_failed",
    "non_rigid_transform",
}
_PHASE15_BAD_REQUEST = {
    "decision_not_allowed", "decision_reason_invalid", "registration_not_eligible",
    "feature_config_invalid",
    "invalid_audit_request",
    "invalid_model_asset",
    "invalid_model_format",
    "invalid_model_geometry",
    "invalid_model_path",
    "invalid_model_release",
    "invalid_model_unit",
    "invalid_model_version",
    "invalid_request_body",
    "invalid_retrieval_input",
    "invalid_staged_source",
    "model_file_error",
    "model_source_not_found",
    "model_source_read_error",
    "model_source_too_large",
    "no_candidate_models",
    "registration_config_invalid",
    "registration_input_incomplete",
}
_REPARSE_POINT = 0x400
MAX_PHASE15_REQUEST_BODY_BYTES = 1024 * 1024


def _phase15_http_error(exc: ModelMatchingError) -> HTTPException:
    if exc.code == "permission_denied":
        status_code = 403
    elif exc.code in _PHASE15_NOT_FOUND:
        status_code = 404
    elif exc.code in _PHASE15_CONFLICT:
        status_code = 409
    elif exc.code in _PHASE15_SERVICE_UNAVAILABLE:
        status_code = 503
    elif exc.code in _PHASE15_BAD_REQUEST:
        status_code = 400
    else:
        status_code = 500
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _invalid_request(message: str) -> ModelMatchingError:
    return ModelMatchingError("invalid_request_body", message)


def _freeze_principal_bindings(raw: object) -> dict[str, Principal]:
    if raw is None:
        configured = os.environ.get("PC_SYSTEM_PRINCIPALS_JSON")
        if configured is None:
            raw = {}
        else:
            try:
                raw = json.loads(configured)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError(
                    "PC_SYSTEM_PRINCIPALS_JSON must be valid JSON."
                ) from exc
    if type(raw) is not dict:
        raise ValueError("Principal bindings must be an exact mapping.")

    frozen: dict[str, Principal] = {}
    for token, value in tuple(raw.items()):
        if type(token) is not str or not token:
            raise ValueError("Principal binding tokens must be exact strings.")
        if type(value) is Principal:
            principal_state = (
                value.actor_id,
                value.roles,
                value.source,
            )
            actor_id, roles, source = principal_state
            if (
                type(actor_id) is not str
                or type(roles) is not frozenset
                or not roles
                or any(
                    type(role) is not str or role not in ALLOWED_ROLES
                    for role in roles
                )
                or type(source) is not str
                or source != "configured_token"
            ):
                raise ValueError(
                    "Configured principal contains untrusted values."
                )
            if principal_state != (
                value.actor_id,
                value.roles,
                value.source,
            ):
                raise ValueError(
                    "Configured principal changed while being captured."
                )
            principal = Principal(
                actor_id=actor_id,
                roles=frozenset(tuple(roles)),
                source=source,
            )
        else:
            if type(value) is not dict or set(value) not in (
                {"actor_id", "roles"},
                {"actor_id", "roles", "source"},
            ):
                raise ValueError("Principal binding has an invalid structure.")
            binding_items = tuple(value.items())
            binding = dict(binding_items)
            if binding_items != tuple(value.items()):
                raise ValueError(
                    "Principal binding changed while being captured."
                )
            actor_id = binding["actor_id"]
            roles = binding["roles"]
            source = binding.get("source", "configured_token")
            if type(roles) is not list:
                raise ValueError("Principal binding contains untrusted values.")
            role_snapshot = tuple(roles)
            if role_snapshot != tuple(roles):
                raise ValueError(
                    "Principal roles changed while being captured."
                )
            if (
                type(actor_id) is not str
                or not role_snapshot
                or any(
                    type(role) is not str or role not in ALLOWED_ROLES
                    for role in role_snapshot
                )
                or type(source) is not str
                or source != "configured_token"
            ):
                raise ValueError("Principal binding contains untrusted values.")
            principal = Principal(
                actor_id=actor_id,
                roles=frozenset(role_snapshot),
                source=source,
            )
        frozen[token] = principal
    return frozen


def _path_entry_is_link_or_reparse(info: os.stat_result) -> bool:
    return stat_module.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _staged_model_source(project_root: Path, relative: object) -> Path:
    if type(relative) is not str:
        raise ModelMatchingError(
            "invalid_staged_source", "Staged model source must be an exact string."
        )
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or "\0" in relative
    ):
        raise ModelMatchingError(
            "invalid_staged_source", "Staged model source path is not canonical."
        )
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ModelMatchingError(
            "invalid_staged_source", "Staged model source path is not canonical."
        )

    root = Path(project_root)
    candidate = root.joinpath(*parts)
    staging = root / "imports" / "models"
    try:
        current = root
        for index, part in enumerate(parts):
            current = current / part
            info = current.lstat()
            final = index == len(parts) - 1
            if _path_entry_is_link_or_reparse(info):
                raise OSError("Path contains a link or reparse point.")
            if final:
                if not stat_module.S_ISREG(info.st_mode):
                    raise OSError("Staged source is not a regular file.")
            elif not stat_module.S_ISDIR(info.st_mode):
                raise OSError("Staged source parent is not a directory.")
        resolved_staging = staging.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(resolved_staging)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ModelMatchingError(
            "invalid_staged_source",
            "Model source must be a regular file inside imports/models.",
        ) from exc
    return candidate


async def _phase15_json_object(request: Request, *, reject_duplicate_fields: bool = False) -> dict:
    def reject_nonstandard_number(value: str) -> None:
        raise ValueError(f"Non-standard JSON number: {value}")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if reject_duplicate_fields and key in result:
                raise ValueError("Duplicate JSON field.")
            result[key] = value
        return result

    raw_content_length = request.headers.get("content-length")
    try:
        content_length = (
            int(raw_content_length)
            if raw_content_length is not None
            else None
        )
    except ValueError:
        content_length = None
    if (
        content_length is not None
        and content_length > MAX_PHASE15_REQUEST_BODY_BYTES
    ):
        raise HTTPException(
            status_code=413,
            detail={
                "code": "request_body_too_large",
                "message": "Request body exceeds the Phase 15 limit.",
            },
        )

    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_PHASE15_REQUEST_BODY_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "request_body_too_large",
                        "message": (
                            "Request body exceeds the Phase 15 limit."
                        ),
                    },
                )
            body.extend(chunk)
    except (HTTPException, ModelMatchingError):
        raise
    except ClientDisconnect as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "request_body_interrupted",
                "message": "Request body transfer was interrupted.",
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "request_stream_error",
                "message": "Request body stream could not be read.",
            },
        ) from exc

    try:
        payload = json.loads(
            bytes(body), parse_constant=reject_nonstandard_number, object_pairs_hook=object_pairs
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _phase15_http_error(
            _invalid_request("Request body must contain valid JSON.")
        ) from exc
    if type(payload) is not dict:
        raise _phase15_http_error(
            _invalid_request("Request body must be a JSON object.")
        )
    return payload


def _capture_payload(payload: dict, allowed: set[str]) -> dict:
    if set(payload) - allowed:
        raise _phase15_http_error(
            _invalid_request("Request body fields do not match the route schema.")
        )
    return {field: value for field, value in payload.items()}


def _require_payload_shape(
    payload: dict,
    *,
    text_fields: set[str],
    list_fields: set[str] = frozenset(),
    object_fields: set[str] = frozenset(),
    optional_text_fields: set[str] = frozenset(),
    integer_fields: set[str] = frozenset(),
    nullable_object_list_fields: set[str] = frozenset(),
) -> dict:
    allowed = (
        text_fields
        | list_fields
        | object_fields
        | optional_text_fields
        | integer_fields
        | nullable_object_list_fields
    )
    missing = (
        text_fields
        | list_fields
        | object_fields
        | integer_fields
    ) - set(payload)
    if missing or set(payload) - allowed:
        raise _phase15_http_error(
            _invalid_request("Request body fields do not match the route schema.")
        )
    if any(type(payload[field]) is not str for field in text_fields):
        raise _phase15_http_error(
            _invalid_request("Request text fields must be exact strings.")
        )
    for field in optional_text_fields:
        if (
            field in payload
            and payload[field] is not None
            and type(payload[field]) is not str
        ):
            raise _phase15_http_error(
                _invalid_request("Optional request text fields must be exact strings.")
            )
    if any(
        type(payload[field]) is not list
        or any(type(item) is not str for item in payload[field])
        for field in list_fields
    ):
        raise _phase15_http_error(
            _invalid_request("Request list fields must contain exact strings.")
        )
    if any(type(payload[field]) is not dict for field in object_fields):
        raise _phase15_http_error(
            _invalid_request("Request object fields must be JSON objects.")
        )
    if any(type(payload[field]) is not int for field in integer_fields):
        raise _phase15_http_error(
            _invalid_request("Request integer fields must be exact integers.")
        )
    if any(
        payload[field] is not None
        and (
            type(payload[field]) is not list
            or any(type(item) is not dict for item in payload[field])
        )
        for field in nullable_object_list_fields
    ):
        raise _phase15_http_error(
            _invalid_request(
                "Request object-list fields must contain JSON objects or null."
            )
        )
    return {field: payload.get(field) for field in allowed}


_PHASE15_IGNORED_IDENTITY_FIELDS = {"actor", "roles", "source"}


def _capture_phase15b2_payload(payload: dict, allowed: set[str]) -> dict:
    captured = _capture_payload(
        payload, allowed | _PHASE15_IGNORED_IDENTITY_FIELDS
    )
    return {
        field: value
        for field, value in captured.items()
        if field not in _PHASE15_IGNORED_IDENTITY_FIELDS
    }


def create_app(
    project_root: Path,
    api_key: str | None = None,
    run_mode: str | None = None,
    principal_bindings: dict | None = None,
    registration_engine_resolver=resolve_registration_engine,
) -> FastAPI:
    """创建最小 API 应用。"""

    resolved_run_mode = (
        os.environ.get("PC_SYSTEM_RUN_MODE", "development")
        if run_mode is None
        else run_mode
    )
    if type(resolved_run_mode) is not str or resolved_run_mode not in {
        "development",
        "production",
    }:
        raise ValueError("run_mode must be development or production.")
    resolved_api_key = api_key if api_key is not None else os.environ.get("PC_SYSTEM_API_KEY")
    if resolved_run_mode == "production" and not resolved_api_key:
        raise ValueError("PC_SYSTEM_API_KEY is required in production mode.")
    resolved_principal_bindings = _freeze_principal_bindings(principal_bindings)
    cors_origins = [] if resolved_run_mode == "production" else ["*"]
    app = FastAPI(title="Point Cloud Platform API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


    def require_write_key(x_api_key: str | None) -> None:
        """保护写入接口；未配置 API Key 时保持开发模式兼容。"""

        if resolved_api_key and x_api_key != resolved_api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    def correction_action(function, /, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CorrectionError as exc:
            raise _correction_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_correction_input",
                    "message": str(exc),
                },
            ) from exc

    def phase15_action(function, /, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ModelMatchingError as exc:
            raise _phase15_http_error(exc) from exc
        except ValueError as exc:
            error = _invalid_request("Request contains an invalid identifier.")
            raise _phase15_http_error(error) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "internal_error",
                    "message": "Phase 15 operation failed unexpectedly.",
                },
            ) from exc

    def require_phase15_principal(
        request: Request, *, route: str, allowed_roles: set[str]
    ) -> Principal:
        token = request.headers.get("x-api-key")
        authorization = request.headers.get("authorization")
        bearer_token: str | None = None
        if authorization is not None:
            parts = authorization.split(" ")
            if len(parts) == 2 and parts[0] == "Bearer" and parts[1]:
                bearer_token = parts[1]
            else:
                bearer_token = ""
        if (
            token is not None
            and bearer_token is not None
            and token != bearer_token
        ):
            token = ""
        elif bearer_token is not None:
            token = bearer_token
        actor_header = request.headers.get("x-actor-id")
        roles_header = request.headers.get("x-actor-roles")
        try:
            principal = resolve_principal(
                run_mode=resolved_run_mode,
                token=token,
                actor_header=(
                    actor_header
                    if resolved_run_mode == "development"
                    else None
                ),
                roles_header=(
                    roles_header
                    if resolved_run_mode == "development"
                    else None
                ),
                bindings=resolved_principal_bindings,
            )
            require_any_role(principal, allowed_roles)
            return principal
        except (ModelMatchingError, ValueError) as exc:
            denied = ModelMatchingError(
                "permission_denied",
                "A trusted principal with a required role is required.",
            )
            try:
                record_denied_operation(
                    project_root,
                    request_id=f"request-denied-{uuid.uuid4().hex}",
                    route=route,
                    token=token,
                    reason="permission_denied",
                )
            except ModelMatchingError as audit_exc:
                if audit_exc.code in {
                    "audit_integrity_error",
                    "audit_persistence_error",
                }:
                    durable_error = audit_exc
                else:
                    durable_error = ModelMatchingError(
                        "audit_persistence_error",
                        "Denied request audit could not be made durable.",
                    )
                raise _phase15_http_error(durable_error) from audit_exc
            except Exception as audit_exc:
                persistence_error = ModelMatchingError(
                    "audit_persistence_error",
                    "Denied request audit could not be made durable.",
                )
                raise _phase15_http_error(persistence_error) from audit_exc
            raise _phase15_http_error(denied) from exc

    @app.get("/health")
    def health() -> dict:
        """健康检查，返回当前绑定的项目目录。"""

        return {
            "status": "ok",
            "project_root": str(project_root),
            "run_mode": resolved_run_mode,
            "write_protection": "api_key" if resolved_api_key else "none",
            "cors_origins": cors_origins,
        }

    @app.get("/assets")
    def list_assets() -> dict:
        """返回项目资产索引。"""

        return _load_registry(project_root)

    @app.get("/model-library")
    def get_model_library() -> dict:
        """Model metadata is intentionally readable without authentication."""

        models = phase15_action(list_model_assets, project_root)
        return {"model_count": len(models), "models": models}

    @app.post("/model-library/models", status_code=status.HTTP_201_CREATED)
    async def post_model_library_model(request: Request) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-library/models",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "model_id",
            "display_name",
            "category_id",
            "manufacturer",
            "model_number",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        list_fields = {"keywords", "tags"}
        captured = _capture_payload(payload, text_fields | list_fields)
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            list_fields=list_fields,
        )
        return phase15_action(
            create_model_asset,
            project_root,
            model_id=values["model_id"],
            display_name=values["display_name"],
            category_id=values["category_id"],
            manufacturer=values["manufacturer"],
            model_number=values["model_number"],
            keywords=values["keywords"],
            tags=values["tags"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.get("/model-library/models/{model_id}")
    def get_model_library_model(model_id: str) -> dict:
        """Published model metadata is intentionally public."""

        model = phase15_action(load_model_asset, project_root, model_id)
        versions = phase15_action(
            list_model_versions, project_root, model_id
        )
        current_release = phase15_action(
            load_current_model_release, project_root, model_id
        )
        release_history = phase15_action(
            list_model_releases, project_root, model_id
        )
        return {
            "model": model,
            "version_count": len(versions),
            "versions": versions,
            "current_release": current_release,
            "release_history": release_history,
        }

    @app.post(
        "/model-library/models/{model_id}/releases",
        status_code=status.HTTP_201_CREATED,
    )
    async def post_model_library_release(
        model_id: str, request: Request
    ) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-library/models/{model_id}/releases",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "version_id",
            "release_id",
            "action",
            "reason",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        optional_text_fields = {
            "expected_current_release_id",
            "rollback_of_release_id",
        }
        captured = _capture_payload(
            payload, text_fields | optional_text_fields
        )
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            optional_text_fields=optional_text_fields,
        )
        return phase15_action(
            release_model_version,
            project_root,
            model_id=model_id,
            version_id=values["version_id"],
            release_id=values["release_id"],
            action=values["action"],
            expected_current_release_id=values[
                "expected_current_release_id"
            ],
            rollback_of_release_id=values["rollback_of_release_id"],
            reason=values["reason"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.post(
        "/model-library/models/{model_id}/versions",
        status_code=status.HTTP_201_CREATED,
    )
    async def post_model_library_version(
        model_id: str, request: Request
    ) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-library/models/{model_id}/versions",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "version_id",
            "staged_source",
            "declared_unit",
            "license",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        object_fields = {"provenance"}
        optional_text_fields = {"supersedes_version_id"}
        captured = _capture_payload(
            payload,
            text_fields | object_fields | optional_text_fields,
        )
        source_path = phase15_action(
            _staged_model_source,
            project_root,
            captured.get("staged_source"),
        )
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            object_fields=object_fields,
            optional_text_fields=optional_text_fields,
        )
        return phase15_action(
            import_model_version,
            project_root,
            model_id=model_id,
            version_id=values["version_id"],
            source_path=source_path,
            declared_unit=values["declared_unit"],
            license_name=values["license"],
            provenance=values["provenance"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
            supersedes_version_id=values["supersedes_version_id"],
        )

    @app.post(
        "/model-matching/retrieval-configs",
        status_code=status.HTTP_201_CREATED,
    )
    async def post_model_retrieval_config(request: Request) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-matching/retrieval-configs",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "config_id",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        object_fields = {"feature", "scoring", "category_mapping"}
        captured = _capture_phase15b2_payload(
            payload, text_fields | object_fields
        )
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            object_fields=object_fields,
        )
        return phase15_action(
            publish_retrieval_config,
            project_root,
            config_id=values["config_id"],
            feature=values["feature"],
            scoring=values["scoring"],
            category_mapping=values["category_mapping"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.get("/model-matching/retrieval-configs")
    def get_model_retrieval_configs(request: Request) -> dict:
        require_phase15_principal(
            request,
            route="GET /model-matching/retrieval-configs",
            allowed_roles={"expert", "auditor"},
        )
        configs = phase15_action(list_retrieval_configs, project_root)
        return {"config_count": len(configs), "configs": configs}

    @app.post(
        "/model-matching/feature-indexes",
        status_code=status.HTTP_201_CREATED,
    )
    async def post_model_feature_index(request: Request) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-matching/feature-indexes",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "index_id",
            "index_mode",
            "config_id",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        optional_history = {"historical_releases"}
        captured = _capture_phase15b2_payload(
            payload, text_fields | optional_history
        )
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            nullable_object_list_fields=optional_history,
        )
        return phase15_action(
            build_model_feature_index,
            project_root,
            index_id=values["index_id"],
            index_mode=values["index_mode"],
            config_id=values["config_id"],
            historical_releases=values["historical_releases"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.get("/model-matching/feature-indexes")
    def get_model_feature_indexes(request: Request) -> dict:
        require_phase15_principal(
            request,
            route="GET /model-matching/feature-indexes",
            allowed_roles={"expert", "auditor"},
        )
        indexes = phase15_action(list_model_feature_indexes, project_root)
        return {"index_count": len(indexes), "indexes": indexes}

    @app.post(
        "/model-matching/feature-index-releases",
        status_code=status.HTTP_201_CREATED,
    )
    async def post_model_feature_index_release(request: Request) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-matching/feature-index-releases",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "index_id",
            "release_id",
            "action",
            "reason",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        optional_text_fields = {
            "expected_current_release_id",
            "rollback_of_release_id",
        }
        captured = _capture_phase15b2_payload(
            payload, text_fields | optional_text_fields
        )
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            optional_text_fields=optional_text_fields,
        )
        return phase15_action(
            release_model_feature_index,
            project_root,
            index_id=values["index_id"],
            release_id=values["release_id"],
            action=values["action"],
            expected_current_release_id=values[
                "expected_current_release_id"
            ],
            rollback_of_release_id=values["rollback_of_release_id"],
            reason=values["reason"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.get("/model-matching/feature-index-releases")
    def get_model_feature_index_releases(request: Request) -> dict:
        require_phase15_principal(
            request,
            route="GET /model-matching/feature-index-releases",
            allowed_roles={"expert", "auditor"},
        )
        releases = phase15_action(
            list_model_feature_index_releases, project_root
        )
        return {"release_count": len(releases), "releases": releases}

    @app.post(
        "/model-matching/retrievals",
        status_code=status.HTTP_201_CREATED,
    )
    async def post_model_retrieval(request: Request) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-matching/retrievals",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "retrieval_run_id",
            "source_kind",
            "asset_id",
            "source_id",
            "instance_id",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        optional_text_fields = {
            "index_release_id",
            "index_id",
            "manufacturer",
            "model_number",
            "hint_source",
        }
        list_fields = {"keywords", "tags"}
        integer_fields = {"top_k"}
        captured = _capture_phase15b2_payload(
            payload,
            text_fields | optional_text_fields | list_fields | integer_fields,
        )
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            optional_text_fields=optional_text_fields,
            list_fields=list_fields,
            integer_fields=integer_fields,
        )
        return phase15_action(
            retrieve_model_candidates,
            project_root,
            retrieval_run_id=values["retrieval_run_id"],
            source_kind=values["source_kind"],
            asset_id=values["asset_id"],
            source_id=values["source_id"],
            instance_id=values["instance_id"],
            index_release_id=values["index_release_id"],
            index_id=values["index_id"],
            top_k=values["top_k"],
            keywords=values["keywords"],
            tags=values["tags"],
            manufacturer=values["manufacturer"],
            model_number=values["model_number"],
            hint_source=values["hint_source"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.get(
        "/model-matching/retrievals/{asset_id}/{source_id}/{instance_id}/{retrieval_run_id}"
    )
    def get_model_retrieval(
        asset_id: str,
        source_id: str,
        instance_id: str,
        retrieval_run_id: str,
        request: Request,
    ) -> dict:
        require_phase15_principal(
            request,
            route=(
                "GET /model-matching/retrievals/"
                "{asset_id}/{source_id}/{instance_id}/{retrieval_run_id}"
            ),
            allowed_roles={"expert", "auditor"},
        )
        return phase15_action(
            load_model_retrieval,
            project_root,
            asset_id=asset_id,
            source_id=source_id,
            instance_id=instance_id,
            retrieval_run_id=retrieval_run_id,
        )

    @app.post(
        "/model-matching/registration-configs",
        status_code=status.HTTP_201_CREATED,
    )
    async def post_model_registration_config(request: Request) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-matching/registration-configs",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "config_id",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        captured = _capture_phase15b2_payload(payload, text_fields | {"config"})
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            object_fields={"config"},
        )
        return phase15_action(
            publish_registration_config,
            project_root,
            config_id=values["config_id"],
            config=values["config"],
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.get("/model-matching/registration-configs")
    def get_model_registration_configs(request: Request) -> dict:
        require_phase15_principal(
            request,
            route="GET /model-matching/registration-configs",
            allowed_roles={"expert", "auditor"},
        )
        configs = phase15_action(list_registration_configs, project_root)
        return {"config_count": len(configs), "configs": configs}

    @app.get("/model-matching/decision-items")
    def get_model_decision_items(request: Request, status: str = "all", asset_id: str | None = None,
                                 class_id: str | None = None, gate_status: str | None = None,
                                 decided_by: str | None = None, started_at: str | None = None,
                                 ended_at: str | None = None, limit: int = 50, cursor: str | None = None) -> dict:
        principal = require_phase15_principal(request, route="GET /model-matching/decision-items",
                                              allowed_roles={"operator", "expert", "auditor"})
        return phase15_action(list_model_decision_items, project_root, principal=principal, status=status,
            asset_id=asset_id, class_id=class_id, gate_status=gate_status, decided_by=decided_by,
            started_at=started_at, ended_at=ended_at, limit=limit, cursor=cursor)

    @app.get("/model-matching/decision-items/{case_id}")
    def get_model_decision_item(case_id: str, request: Request) -> dict:
        principal = require_phase15_principal(request, route="GET /model-matching/decision-items/{case_id}",
                                              allowed_roles={"operator", "expert", "auditor"})
        return phase15_action(load_model_decision_item, project_root, case_id=case_id, principal=principal)

    def read_model_bindings(request, asset_id, source_id, instance_id, include_history):
        principal = require_phase15_principal(request, route="GET /model-matching/bindings",
                                              allowed_roles={"operator", "expert", "auditor"})
        return phase15_action(load_model_bindings, project_root, asset_id=asset_id, source_id=source_id,
                             instance_id=instance_id, principal=principal, include_history=include_history)

    @app.get("/model-matching/bindings/{asset_id}/{source_id}/{instance_id}")
    def get_model_binding(asset_id: str, source_id: str, instance_id: str, request: Request) -> dict:
        return read_model_bindings(request, asset_id, source_id, instance_id, False)

    @app.get("/model-matching/bindings/{asset_id}/{source_id}/{instance_id}/history")
    def get_model_binding_history(asset_id: str, source_id: str, instance_id: str, request: Request) -> dict:
        return read_model_bindings(request, asset_id, source_id, instance_id, True)

    async def phase15d_write(request, action, binding_id=None):
        principal = require_phase15_principal(request, route=f"POST /model-matching/{action}",
            allowed_roles={"operator", "expert"} if action == "decisions" else {"expert"})
        payload = await _phase15_json_object(request, reject_duplicate_fields=True)
        common = {"decision_id", "decision_reason", "verification_scope", "expected_case_revision",
                  "operation_id", "request_id", "idempotency_key"}
        nullable = set()
        if action == "decisions":
            text_fields = common | {"case_id", "decision"}
            nullable = {"registration_id", "binding_id", "candidate_rank"}
            fields = text_fields | nullable
        else:
            text_fields = common | {"asset_id", "source_id", "instance_id", "retrieval_run_id", "binding_id"}
            fields = text_fields | ({"registration_id", "candidate_rank"} if action == "supersede" else {"restores_binding_id"})
            text_fields |= {"registration_id"} if action == "supersede" else {"restores_binding_id"}
        if set(payload) != fields:
            raise _phase15_http_error(_invalid_request("Request fields must exactly match the decision schema."))
        if any(type(payload[field]) is not str for field in text_fields):
            raise _phase15_http_error(_invalid_request("Decision text fields must be exact strings."))
        for field in nullable - {"candidate_rank"}:
            if payload[field] is not None and type(payload[field]) is not str:
                raise _phase15_http_error(_invalid_request("Nullable identifiers must be strings or null."))
        if "candidate_rank" in payload and not (action == "decisions" and payload["candidate_rank"] is None):
            if type(payload["candidate_rank"]) is not int or payload["candidate_rank"] < 1:
                raise _phase15_http_error(_invalid_request("Candidate rank must be a positive integer."))
        function = {"decisions": decide_model_match, "supersede": supersede_model_binding, "restore": restore_model_binding}[action]
        if binding_id is not None:
            payload["current_binding_id"] = binding_id
        result = phase15_action(function, project_root, principal=principal, **payload)
        return {**result, "binding": crop_model_binding(result["binding"], principal=principal)}

    @app.post("/model-matching/decisions", status_code=201)
    async def post_model_decision(request: Request) -> dict:
        return await phase15d_write(request, "decisions")

    @app.post("/model-matching/bindings/{binding_id}/supersede", status_code=201)
    async def post_model_binding_supersede(binding_id: str, request: Request) -> dict:
        return await phase15d_write(request, "supersede", binding_id)

    @app.post("/model-matching/bindings/{binding_id}/restore", status_code=201)
    async def post_model_binding_restore(binding_id: str, request: Request) -> dict:
        return await phase15d_write(request, "restore", binding_id)

    @app.post("/model-matching/registrations")
    async def post_model_registration(request: Request) -> dict:
        principal = require_phase15_principal(
            request,
            route="POST /model-matching/registrations",
            allowed_roles={"expert"},
        )
        payload = await _phase15_json_object(request)
        text_fields = {
            "registration_id",
            "asset_id",
            "source_id",
            "instance_id",
            "retrieval_run_id",
            "config_id",
            "operation_id",
            "request_id",
            "idempotency_key",
        }
        captured = _capture_phase15b2_payload(
            payload, text_fields | {"candidate_rank"}
        )
        values = _require_payload_shape(
            captured,
            text_fields=text_fields,
            integer_fields={"candidate_rank"},
        )
        return phase15_action(
            register_model_candidate,
            project_root,
            registration_id=values["registration_id"],
            asset_id=values["asset_id"],
            source_id=values["source_id"],
            instance_id=values["instance_id"],
            retrieval_run_id=values["retrieval_run_id"],
            candidate_rank=values["candidate_rank"],
            config_id=values["config_id"],
            engine_resolver=registration_engine_resolver,
            principal=principal,
            operation_id=values["operation_id"],
            request_id=values["request_id"],
            idempotency_key=values["idempotency_key"],
        )

    @app.get(
        "/model-matching/registrations/{asset_id}/{source_id}/{instance_id}/{registration_id}"
    )
    def get_model_registration(
        asset_id: str,
        source_id: str,
        instance_id: str,
        registration_id: str,
        request: Request,
    ) -> dict:
        require_phase15_principal(
            request,
            route=(
                "GET /model-matching/registrations/"
                "{asset_id}/{source_id}/{instance_id}/{registration_id}"
            ),
            allowed_roles={"expert", "auditor"},
        )
        return phase15_action(
            load_model_registration,
            project_root,
            asset_id=asset_id,
            source_id=source_id,
            instance_id=instance_id,
            registration_id=registration_id,
        )

    @app.get("/audit/operations/{operation_id}")
    def get_model_matching_operation(
        operation_id: str, request: Request
    ) -> dict:
        require_phase15_principal(
            request,
            route="GET /audit/operations/{operation_id}",
            allowed_roles={"auditor"},
        )
        try:
            normalized_operation_id = validate_identifier(
                operation_id, "operation_id"
            )
        except ValueError as exc:
            phase15_action(
                read_verified_operation_snapshot,
                project_root,
                operation_id,
            )
            raise AssertionError("Invalid audit request unexpectedly returned.") from exc
        operation_root = (
            Path(project_root)
            / "reports"
            / "model_matching_operations"
            / normalized_operation_id
        )
        try:
            operation_path_chain = (
                Path(project_root) / "reports",
                Path(project_root)
                / "reports"
                / "model_matching_operations",
                operation_root,
            )
            operation_infos = [
                path.lstat() for path in operation_path_chain
            ]
        except FileNotFoundError:
            raise _phase15_http_error(
                ModelMatchingError(
                    "operation_not_found", "Audit operation does not exist."
                )
            )
        except OSError as exc:
            raise _phase15_http_error(
                ModelMatchingError(
                    "audit_persistence_error",
                    "Audit operation path could not be inspected.",
                )
            ) from exc
        if any(
            _path_entry_is_link_or_reparse(info)
            or not stat_module.S_ISDIR(info.st_mode)
            for info in operation_infos
        ):
            raise _phase15_http_error(
                ModelMatchingError(
                    "audit_integrity_error",
                    "Audit operation path is not a plain directory.",
                )
            )
        snapshot = phase15_action(
            read_verified_operation_snapshot,
            project_root,
            normalized_operation_id,
        )
        return {**snapshot, "chain_valid": True}

    @app.get("/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict:
        """返回单个资产索引条目。"""

        return _asset_or_404(project_root, asset_id)

    @app.get("/runs/{asset_id}/plan")
    def get_run_plan(asset_id: str) -> dict:
        """返回生产运行计划。"""

        return _read_json_or_404(_production_dir(project_root, asset_id) / "production_run_plan.json", "Production run plan")

    @app.get("/runs/{asset_id}/report")
    def get_run_report(asset_id: str) -> dict:
        """返回生产运行报告。"""

        return _read_json_or_404(_production_dir(project_root, asset_id) / "production_run_report.json", "Production run report")

    @app.get("/runs/{asset_id}/jobs")
    def list_jobs(asset_id: str) -> dict:
        """返回资产关联的本地 job 状态列表和前端汇总。"""

        jobs_dir = _jobs_dir(project_root, asset_id)
        jobs = []
        if jobs_dir.exists():
            for path in sorted(jobs_dir.glob("*.json")):
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
        return {"asset_id": asset_id, "jobs": jobs, **_summarize_jobs(jobs)}

    @app.post("/runs/{asset_id}/jobs", status_code=status.HTTP_201_CREATED)
    def create_job(asset_id: str, payload: dict | None = None, x_api_key: str | None = Header(default=None)) -> dict:
        """从生产运行计划创建 job，供前端或自动化流程受控触发。"""

        require_write_key(x_api_key)
        ProjectConfig(project_root=project_root).ensure_directories()
        plan_path = _production_dir(project_root, asset_id) / "production_run_plan.json"
        if not plan_path.exists():
            raise HTTPException(status_code=404, detail=f"Production run plan not found: {plan_path}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        job_id = (payload or {}).get("job_id")
        if job_id is not None:
            _validate_api_identifier(job_id, "job_id")
        job = create_job_from_plan(plan, job_id=job_id)
        jobs_dir = _jobs_dir(project_root, asset_id)
        write_job(job, jobs_dir)
        write_job_event(jobs_dir, job["job_id"], action="job_created", new_status=job["status"], actor="api")
        return job

    @app.patch("/runs/{asset_id}/jobs/{job_id}/steps/{step_id}")
    def update_job_step(asset_id: str, job_id: str, step_id: str, payload: dict, x_api_key: str | None = Header(default=None)) -> dict:
        """更新单个 job step 状态，写回本地 job JSON。"""

        require_write_key(x_api_key)
        status_value = payload.get("status")
        message = payload.get("message", "")
        if status_value not in JOB_STATUSES:
            raise HTTPException(status_code=400, detail=f"Unsupported job step status: {status_value}")
        path = _job_path(project_root, asset_id, job_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Job not found: {path}")
        job = load_job(path)
        old_status = ""
        for step in job.get("steps", []):
            if step.get("step_id") == step_id:
                old_status = step.get("status", "")
                break
        try:
            updated = mark_step_status(job, step_id, status_value, message=message)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        write_job(updated, path.parent)
        write_job_event(
            path.parent,
            job_id,
            action="step_status_updated",
            step_id=step_id,
            old_status=old_status,
            new_status=status_value,
            message=message,
            actor="api",
        )
        return updated


    @app.get("/runs/{asset_id}/jobs/{job_id}")
    def get_job_detail(asset_id: str, job_id: str) -> dict:
        """返回单个 job 详情和审计事件。"""

        path = _job_path(project_root, asset_id, job_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Job not found: {path}")
        return {"asset_id": asset_id, "job": load_job(path), "events": read_job_events(path.parent, job_id)}
    @app.get("/reports/center")
    def get_report_center() -> dict:
        """返回 Phase 11 报告中心索引。"""

        return build_report_center(project_root)

    @app.get("/reports/{asset_id}")
    def list_reports(asset_id: str) -> dict:
        """返回常用报告路径，前端可直接生成链接。"""

        return {
            "asset_id": asset_id,
            "quality_report": f"reports/{asset_id}/quality_report.html",
            "production_plan": f"reports/production_runs/{asset_id}/production_run_plan.json",
            "production_report": f"reports/production_runs/{asset_id}/production_run_report.json",
            "deployment_checklist": f"reports/deployment/{asset_id}/deployment_checklist.json",
        }

    @app.get("/delivery/{asset_id}/status")
    def get_delivery_status(asset_id: str) -> dict:
        """返回资产交付物的真实文件存在性和类型。"""

        asset = _asset_or_404(project_root, asset_id)
        viewer_paths = asset.get("viewer_paths", {})
        report_paths = asset.get("report_paths", {})
        outputs = {
            "viewer_url": _delivery_output(project_root, viewer_paths.get("viewer_url") or viewer_paths.get("viewer_html_path")),
            "viewer_html_path": _delivery_output(project_root, viewer_paths.get("viewer_html_path") or viewer_paths.get("viewer_url")),
            "manifest_path": _delivery_output(project_root, viewer_paths.get("manifest_path")),
            "potree_manifest_path": _delivery_output(project_root, viewer_paths.get("potree_manifest_path")),
            "report_path": _delivery_output(project_root, viewer_paths.get("report_path") or report_paths.get("production_report")),
            "quality_report": _delivery_output(project_root, report_paths.get("quality_report")),
        }
        return {"asset_id": asset_id, "outputs": outputs}


    @app.get("/analysis")
    def list_point_cloud_analysis() -> dict:
        """返回所有 Phase 7 点云分析报告的轻量汇总。"""

        analysis_root = project_root / "reports" / "analysis"
        rows = []
        if analysis_root.exists():
            for report_path in sorted(analysis_root.glob("*/point_cloud_analysis.json"), key=lambda path: path.parent.name):
                report = json.loads(report_path.read_text(encoding="utf-8"))
                rows.append({
                    "asset_id": report.get("asset_id", report_path.parent.name),
                    "point_count": report.get("point_count", 0),
                    "rgb_coverage": report.get("rgb_coverage", 0.0),
                    "finding_count": len(report.get("findings", [])),
                })
        return {"asset_count": len(rows), "analyses": rows}

    @app.get("/analysis/{asset_id}")
    def get_point_cloud_analysis(asset_id: str) -> dict:
        """返回 Phase 6 点云分析报告。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        path = project_root / "reports" / "analysis" / asset_id / "point_cloud_analysis.json"
        return _read_json_or_404(path, "Point cloud analysis")

    @app.get("/segments/{asset_id}/objects")
    def get_object_segments(asset_id: str) -> dict:
        """返回 Phase 10 物体候选分割报告。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        path = project_root / "reports" / "object_segments" / asset_id / "object_segments.json"
        return _read_json_or_404(path, "Object segmentation")

    @app.get("/segmentation-runs/{asset_id}")
    def list_segmentation_runs(asset_id: str) -> dict:
        """返回资产的历史分割运行。"""

        runs_dir = _segmentation_runs_dir(project_root, asset_id)
        runs = []
        if runs_dir.exists():
            for path in sorted(runs_dir.glob("*/segmentation_run.json"), key=lambda item: item.parent.name):
                runs.append(json.loads(path.read_text(encoding="utf-8")))
        return {"asset_id": asset_id, "run_count": len(runs), "runs": runs}

    @app.get("/segmentation-runs/{asset_id}/{run_id}")
    def get_segmentation_run(asset_id: str, run_id: str) -> dict:
        """返回单次版本化分割运行。"""

        run_id = _validate_api_identifier(run_id, "run_id")
        path = _segmentation_runs_dir(project_root, asset_id) / run_id / "segmentation_run.json"
        return _read_json_or_404(path, "Segmentation run")

    @app.get("/segmentation-runs/{asset_id}/{run_id}/quality")
    def get_segmentation_run_quality(asset_id: str, run_id: str) -> dict:
        """返回单次分割运行的无标注质量代理报告。"""

        run_id = _validate_api_identifier(run_id, "run_id")
        path = _segmentation_runs_dir(project_root, asset_id) / run_id / "segmentation_quality.json"
        return _read_json_or_404(path, "Segmentation operational quality")

    @app.get("/segmentation-benchmarks")
    def list_segmentation_benchmarks() -> dict:
        """返回已导入的黄金 benchmark 清单。"""

        root = project_root / "benchmarks"
        benchmarks = []
        if root.exists():
            for path in sorted(root.glob("*/benchmark.json"), key=lambda item: item.parent.name):
                benchmarks.append(json.loads(path.read_text(encoding="utf-8")))
        return {"benchmark_count": len(benchmarks), "benchmarks": benchmarks}

    @app.get("/segmentation-benchmarks/{benchmark_id}")
    def get_segmentation_benchmark(benchmark_id: str) -> dict:
        """返回单个黄金 benchmark。"""

        benchmark_id = _validate_api_identifier(benchmark_id, "benchmark_id")
        path = project_root / "benchmarks" / benchmark_id / "benchmark.json"
        return _read_json_or_404(path, "Segmentation benchmark")

    @app.get("/segmentation-evaluations/{asset_id}")
    def list_segmentation_evaluations(asset_id: str) -> dict:
        """返回资产的黄金评估运行。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        root = project_root / "reports" / "segmentation_evaluations" / asset_id
        evaluations = []
        if root.exists():
            for path in sorted(root.glob("*/evaluation_run.json"), key=lambda item: item.parent.name):
                evaluations.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "asset_id": asset_id,
            "evaluation_count": len(evaluations),
            "evaluations": evaluations,
        }

    @app.get("/segmentation-evaluations/{asset_id}/{evaluation_id}")
    def get_segmentation_evaluation(asset_id: str, evaluation_id: str) -> dict:
        """返回单次黄金评估。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        evaluation_id = _validate_api_identifier(evaluation_id, "evaluation_id")
        path = (
            project_root
            / "reports"
            / "segmentation_evaluations"
            / asset_id
            / evaluation_id
            / "evaluation_run.json"
        )
        return _read_json_or_404(path, "Segmentation evaluation")

    @app.get("/segmentation-comparisons/{asset_id}/{comparison_id}")
    def get_segmentation_comparison(asset_id: str, comparison_id: str) -> dict:
        """返回候选/基线比较和回归门禁。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        comparison_id = _validate_api_identifier(comparison_id, "comparison_id")
        root = (
            project_root
            / "reports"
            / "segmentation_comparisons"
            / asset_id
            / comparison_id
        )
        return {
            "comparison": _read_json_or_404(
                root / "comparison.json", "Segmentation comparison"
            ),
            "gate": _read_json_or_404(
                root / "regression_gate.json", "Segmentation regression gate"
            ),
        }

    @app.get("/segmentation-searches/{asset_id}")
    def list_segmentation_searches(asset_id: str) -> dict:
        """返回资产的参数搜索运行。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        root = project_root / "reports" / "segmentation_searches" / asset_id
        searches = []
        if root.exists():
            for path in sorted(root.glob("*/search_run.json"), key=lambda item: item.parent.name):
                searches.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "asset_id": asset_id,
            "search_count": len(searches),
            "searches": searches,
        }

    @app.get("/segmentation-searches/{asset_id}/{search_id}")
    def get_segmentation_search(asset_id: str, search_id: str) -> dict:
        """返回一次参数搜索运行。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        search_id = _validate_api_identifier(search_id, "search_id")
        path = (
            project_root
            / "reports"
            / "segmentation_searches"
            / asset_id
            / search_id
            / "search_run.json"
        )
        return _read_json_or_404(path, "Segmentation search")

    @app.get("/segmentation-searches/{asset_id}/{search_id}/recommendation")
    def get_segmentation_search_recommendation(
        asset_id: str, search_id: str
    ) -> dict:
        """返回一次参数搜索的建议配置。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        search_id = _validate_api_identifier(search_id, "search_id")
        path = (
            project_root
            / "reports"
            / "segmentation_searches"
            / asset_id
            / search_id
            / "recommendation.json"
        )
        return _read_json_or_404(path, "Segmentation search recommendation")

    @app.get("/segmentation-searches/{asset_id}/{search_id}/trials")
    def list_segmentation_search_trials(
        asset_id: str, search_id: str
    ) -> dict:
        """返回一次参数搜索的全部试验记录。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        search_id = _validate_api_identifier(search_id, "search_id")
        root = (
            project_root
            / "reports"
            / "segmentation_searches"
            / asset_id
            / search_id
        )
        search = _read_json_or_404(
            root / "search_run.json", "Segmentation search"
        )
        trials = []
        trials_root = root / "trials"
        if trials_root.exists():
            for path in sorted(
                trials_root.glob("*.json"), key=lambda item: item.name
            ):
                trials.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "asset_id": asset_id,
            "search_id": search_id,
            "search_status": search.get("status"),
            "trial_count": len(trials),
            "trials": trials,
        }

    @app.get("/segmentation-corrections/{asset_id}")
    def get_correction_sessions(asset_id: str) -> dict:
        asset_id = _validate_api_identifier(asset_id, "asset_id")
        sessions = correction_action(
            list_correction_sessions, project_root, asset_id
        )
        return {
            "asset_id": asset_id,
            "session_count": len(sessions),
            "sessions": sessions,
        }

    @app.post(
        "/segmentation-corrections/{asset_id}",
        status_code=status.HTTP_201_CREATED,
    )
    def post_correction_session(
        asset_id: str,
        payload: dict,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        require_write_key(x_api_key)
        return correction_action(
            create_correction_session,
            project_root,
            asset_id=asset_id,
            run_id=payload.get("run_id"),
            session_id=payload.get("session_id"),
            sample_id=payload.get("sample_id"),
            actor=payload.get("actor"),
            benchmark_id=payload.get("benchmark_id"),
            baseline_release_id=payload.get("baseline_release_id"),
            lock_ttl_seconds=payload.get("lock_ttl_seconds", 900),
        )

    @app.get("/segmentation-corrections/{asset_id}/{session_id}")
    def get_correction_session(asset_id: str, session_id: str) -> dict:
        return correction_action(
            load_correction_session,
            project_root,
            asset_id,
            session_id,
        )

    @app.get("/segmentation-corrections/{asset_id}/{session_id}/points")
    def get_correction_points(
        asset_id: str,
        session_id: str,
        offset: int = 0,
        limit: int = 10_000,
    ) -> dict:
        return correction_action(
            load_correction_points,
            project_root,
            asset_id,
            session_id,
            offset=offset,
            limit=limit,
        )

    @app.get("/segmentation-corrections/{asset_id}/{session_id}/objects")
    def get_correction_objects(asset_id: str, session_id: str) -> dict:
        return correction_action(
            load_correction_objects,
            project_root,
            asset_id,
            session_id,
        )

    @app.get("/segmentation-corrections/{asset_id}/{session_id}/queue")
    def get_correction_queue(asset_id: str, session_id: str) -> dict:
        root = correction_action(
            _session_dir, project_root, asset_id, session_id
        )
        correction_action(
            load_correction_session, project_root, asset_id, session_id
        )
        return _read_json_or_404(root / "review_queue.json", "Correction queue")

    @app.get("/segmentation-corrections/{asset_id}/{session_id}/events")
    def get_correction_events(asset_id: str, session_id: str) -> dict:
        events = correction_action(
            read_correction_events,
            project_root,
            asset_id,
            session_id,
        )
        public_events = [
            {key: value for key, value in event.items() if key != "accepted_response"}
            for event in events
        ]
        return {
            "asset_id": asset_id,
            "session_id": session_id,
            "event_count": len(public_events),
            "events": public_events,
        }

    @app.post("/segmentation-corrections/{asset_id}/{session_id}/events")
    def post_correction_event(
        asset_id: str,
        session_id: str,
        payload: dict,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        require_write_key(x_api_key)
        return correction_action(
            apply_correction_event,
            project_root,
            asset_id=asset_id,
            session_id=session_id,
            actor=payload.get("actor"),
            expected_revision=payload.get("expected_revision"),
            client_request_id=payload.get("client_request_id"),
            operation=payload.get("operation"),
        )

    @app.post("/segmentation-corrections/{asset_id}/{session_id}/submit")
    def submit_correction_session(
        asset_id: str,
        session_id: str,
        payload: dict,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        require_write_key(x_api_key)
        return correction_action(
            transition_correction_session,
            project_root,
            asset_id=asset_id,
            session_id=session_id,
            action="submit",
            actor=payload.get("actor"),
            expected_revision=payload.get("expected_revision"),
        )

    @app.post("/segmentation-corrections/{asset_id}/{session_id}/return")
    def return_correction_session(
        asset_id: str,
        session_id: str,
        payload: dict,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        require_write_key(x_api_key)
        return correction_action(
            transition_correction_session,
            project_root,
            asset_id=asset_id,
            session_id=session_id,
            action="return",
            actor=payload.get("actor"),
            expected_revision=payload.get("expected_revision"),
        )

    @app.post(
        "/segmentation-corrections/{asset_id}/{session_id}/publish",
        status_code=status.HTTP_201_CREATED,
    )
    def publish_correction_session(
        asset_id: str,
        session_id: str,
        payload: dict,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        require_write_key(x_api_key)
        return correction_action(
            publish_correction_release,
            project_root,
            asset_id=asset_id,
            session_id=session_id,
            release_id=payload.get("release_id"),
            reviewer=payload.get("reviewer"),
            expected_revision=payload.get("expected_revision"),
            benchmark_split=payload.get("benchmark_split"),
            license_name=payload.get("license"),
            evaluation_config=payload.get("evaluation_config"),
            baseline_evaluation_id=payload.get("baseline_evaluation_id"),
            regression_thresholds=payload.get("regression_thresholds"),
            search_config=payload.get("search_config"),
        )

    @app.get("/segmentation-correction-releases/{asset_id}")
    def get_correction_releases(asset_id: str) -> dict:
        asset_id = _validate_api_identifier(asset_id, "asset_id")
        releases = correction_action(
            list_correction_releases, project_root, asset_id
        )
        return {
            "asset_id": asset_id,
            "release_count": len(releases),
            "releases": releases,
        }

    @app.get("/segmentation-correction-releases/{asset_id}/{release_id}")
    def get_correction_release(asset_id: str, release_id: str) -> dict:
        return correction_action(
            load_correction_release,
            project_root,
            asset_id,
            release_id,
        )

    @app.post(
        "/segmentation-correction-releases/{asset_id}/{release_id}/retry"
    )
    def retry_correction_publication(
        asset_id: str,
        release_id: str,
        payload: dict,
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        require_write_key(x_api_key)
        return correction_action(
            retry_publication_tasks,
            project_root,
            asset_id=asset_id,
            release_id=release_id,
            actor=payload.get("actor"),
        )

    @app.get("/project-gate")
    def get_project_gate() -> dict:
        """返回 Phase 11 项目级门禁报告。"""

        path = project_root / "reports" / "project_gate" / "project_gate.json"
        return _read_json_or_404(path, "Project gate")

    @app.get("/quality-gates/{asset_id}")
    def get_quality_gate(asset_id: str) -> dict:
        """返回 Phase 8 质量门禁报告。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        path = project_root / "reports" / "quality_gates" / asset_id / "quality_gate.json"
        return _read_json_or_404(path, "Quality gate")
    @app.get("/deployment/{asset_id}")
    def get_deployment(asset_id: str) -> dict:
        """返回部署交付检查清单。"""

        asset_id = _validate_api_identifier(asset_id, "asset_id")
        path = project_root / "reports" / "deployment" / asset_id / "deployment_checklist.json"
        return _read_json_or_404(path, "Deployment checklist")

    return app


app = create_app(Path(os.environ.get("PC_SYSTEM_PROJECT_ROOT", "workspace")))



