"""点云转网格的角色裁剪目录、稳定分页与安全投影。"""

import base64
import hashlib
import hmac
import json
import stat
from pathlib import Path

from pc_system.mesh_reconstruction import _quality_report, _read_canonical, load_mesh_run
from pc_system.mesh_reconstruction_config import (
    list_mesh_configs,
    list_mesh_quality_policies,
)
from pc_system.mesh_release import (
    list_mesh_releases,
    load_current_mesh_release,
    load_mesh_decision,
    mesh_object_key,
)
from pc_system.model_matching_audit import load_operation
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.mesh_worker_contract import load_bounded_mesh_preview
from pc_system.scan_to_mesh_errors import ScanToMeshError


_DEFAULT_CURSOR_SECRET = b"pc-system-scan-to-mesh-cursor-v1"
_STATUSES = frozenset(
    {
        "all",
        "pending",
        "processed",
        "passed",
        "review_required",
        "rejected",
        "published",
    }
)
_REPARSE_POINT = 0x400


def _invalid(message: str) -> ScanToMeshError:
    return ScanToMeshError("scan_to_mesh_catalog_invalid", message)


def _viewer_role(principal: Principal) -> str:
    require_any_role(principal, {"operator", "expert", "auditor"})
    if "expert" in principal.roles:
        return "expert"
    if "auditor" in principal.roles:
        return "auditor"
    return "operator"


def mesh_session(*, principal: Principal) -> dict:
    return {"viewer_role": _viewer_role(principal)}


def _audit_projection(root: Path, operation_id: str) -> dict:
    operation = load_operation(root, operation_id)
    return {
        "operation_id": operation["operation_id"],
        "operation_type": operation["operation_type"],
        "status": operation["status"],
        "actor_id": operation["actor_id"],
        "principal_source": operation["principal_source"],
        "request_id": operation["request_id"],
        "started_at": operation["started_at"],
        "completed_at": operation.get("completed_at"),
        "error": operation.get("error"),
    }


def _catalog_document(root: Path, value: dict, *, role: str, kind: str) -> dict:
    id_field = "config_id" if kind == "config" else "policy_id"
    fingerprint_field = (
        "config_fingerprint" if kind == "config" else "policy_fingerprint"
    )
    summary = {
        id_field: value[id_field],
        "status": value["status"],
        "created_at": value["created_at"],
        fingerprint_field: value[fingerprint_field],
    }
    if kind == "config":
        summary["algorithm"] = value["algorithm"]
        summary["worker_version"] = value["worker_version"]
    else:
        summary["policy_kind"] = value["policy_kind"]
    if role in {"expert", "auditor"}:
        summary.update(value)
    if role == "auditor":
        summary["audit"] = _audit_projection(root, value["operation_id"])
    return summary


def list_mesh_config_catalog(root: Path, *, principal: Principal) -> dict:
    role = _viewer_role(principal)
    project_root = Path(root)
    return {
        "viewer_role": role,
        "items": [
            _catalog_document(project_root, item, role=role, kind="config")
            for item in list_mesh_configs(project_root)
        ],
    }


def list_mesh_policy_catalog(root: Path, *, principal: Principal) -> dict:
    role = _viewer_role(principal)
    project_root = Path(root)
    return {
        "viewer_role": role,
        "items": [
            _catalog_document(project_root, item, role=role, kind="policy")
            for item in list_mesh_quality_policies(project_root)
        ],
    }


def _safe_artifacts(artifacts: dict) -> dict:
    result = {}
    for kind, descriptor in sorted(artifacts.items()):
        item = {
            field: descriptor[field]
            for field in ("bytes", "sha256", "vertex_count", "triangle_count")
            if field in descriptor
        }
        result[kind] = item
    return result


def _quality_evidence(root: Path, run: dict) -> dict:
    directory = root / "scan_to_cad" / "mesh_runs" / run["run_id"]
    metrics = _read_canonical(directory / "mesh_metrics.json")
    policy = _read_canonical(directory / "policy_snapshot.json")
    quality = _read_canonical(directory / "quality_report.json")
    rebuilt = _quality_report(metrics, policy, quality["metrics_fingerprint"])
    if (
        rebuilt != quality
        or quality["quality_report_fingerprint"]
        != run["quality_report_fingerprint"]
    ):
        raise ScanToMeshError(
            "mesh_run_integrity_error", "网格质量证据与运行清单不一致。"
        )
    metric_fields = (
        "raw_vertex_count",
        "raw_triangle_count",
        "repaired_vertex_count",
        "repaired_triangle_count",
        "component_count",
        "largest_component_fraction",
        "mesh_to_point_coverage",
        "point_to_mesh_p95_m",
        "repair_max_displacement_m",
    )
    return {
        "status": quality["status"],
        "summary_zh": quality["summary_zh"],
        "reasons": quality["reasons"],
        "derived": quality["derived"],
        "metrics": {field: metrics[field] for field in metric_fields},
        "quality_report_fingerprint": quality["quality_report_fingerprint"],
    }


def _decision(root: Path, run_id: str) -> dict | None:
    try:
        return load_mesh_decision(root, run_id)
    except ScanToMeshError as exc:
        if exc.code == "mesh_decision_not_found":
            return None
        raise


def _release_context(root: Path, run: dict) -> tuple[str, list[dict], dict | None]:
    object_key = mesh_object_key(run)
    history = list_mesh_releases(root, object_key)
    current = load_current_mesh_release(root, object_key)
    return object_key, history, current


def _release_summary(release: dict) -> dict:
    return {
        field: release[field]
        for field in (
            "object_key",
            "release_id",
            "run_id",
            "action",
            "previous_release_id",
            "rollback_of_release_id",
            "reason",
            "created_by",
            "created_at",
            "status",
            "release_fingerprint",
        )
    }


def _run_context(root: Path, run: dict) -> dict:
    operation = load_operation(root, run["operation_id"])
    decision = _decision(root, run["run_id"])
    object_key, releases, current = _release_context(root, run)
    publication_status = "unpublished"
    if current is not None and current["run_id"] == run["run_id"]:
        publication_status = "current"
    elif any(item["run_id"] == run["run_id"] for item in releases):
        publication_status = "historical"
    return {
        "run": run,
        "created_at": operation["started_at"],
        "operation": operation,
        "decision": decision,
        "object_key": object_key,
        "releases": releases,
        "current": current,
        "publication_status": publication_status,
    }


def _business_run(context: dict) -> dict:
    run = context["run"]
    current = context["current"]
    return {
        "run_id": run["run_id"],
        "object_key": context["object_key"],
        "source_kind": run["source_kind"],
        "source_identity": run["source_identity"],
        "quality_status": run["status"],
        "publication_status": "current",
        "current_release_id": current["release_id"],
        "created_at": context["created_at"],
    }


def _professional_run(root: Path, context: dict, *, role: str) -> dict:
    run = context["run"]
    current = context["current"]
    result = {
        "viewer_role": role,
        "run_id": run["run_id"],
        "object_key": context["object_key"],
        "created_at": context["created_at"],
        "status": run["status"],
        "state_history": run["state_history"],
        "source_kind": run["source_kind"],
        "source_identity": run["source_identity"],
        "source_snapshot_fingerprint": run["source_snapshot_fingerprint"],
        "config_id": run["config_id"],
        "config_fingerprint": run["config_fingerprint"],
        "policy_id": run["policy_id"],
        "policy_fingerprint": run["policy_fingerprint"],
        "manifest_fingerprint": run["manifest_fingerprint"],
        "quality": _quality_evidence(root, run),
        "artifacts": _safe_artifacts(run["artifacts"]),
        "decision": context["decision"],
        "publication_status": context["publication_status"],
        "current_release_id": None if current is None else current["release_id"],
        "release_history": [
            _release_summary(release) for release in context["releases"]
        ],
    }
    if role == "auditor":
        result["audit"] = _audit_projection(root, run["operation_id"])
    return result


def _matches(context: dict, status: str) -> bool:
    if status == "all":
        return True
    if status == "pending":
        return context["decision"] is None
    if status == "processed":
        return context["decision"] is not None
    if status == "published":
        return context["publication_status"] in {"current", "historical"}
    return context["run"]["status"] == status


def _is_plain_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and not bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)
    )


def _committed_run_ids(root: Path) -> list[str]:
    parent = root / "scan_to_cad" / "mesh_runs"
    try:
        entries = tuple(parent.iterdir())
    except FileNotFoundError:
        return []
    return sorted(
        path.name
        for path in entries
        if _is_plain_directory(path) and (path / "commit.json").is_file()
    )


def _secret(value: bytes | None) -> bytes:
    if value is None:
        return _DEFAULT_CURSOR_SECRET
    if type(value) is not bytes or not value:
        raise _invalid("分页签名密钥无效。")
    return value


def _cursor_payload(*, role: str, status: str, after: tuple[str, str]) -> bytes:
    return json.dumps(
        {"version": "1.0", "role": role, "status": status, "after": list(after)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode_cursor(*, role: str, status: str, after: tuple[str, str], secret: bytes) -> str:
    payload = _cursor_payload(role=role, status=status, after=after)
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode("ascii")


def _decode_cursor(
    cursor: str | None, *, role: str, status: str, secret: bytes
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        if type(cursor) is not str or not cursor or len(cursor) > 4096:
            raise ValueError("cursor length")
        raw = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
        if len(raw) <= hashlib.sha256().digest_size:
            raise ValueError("cursor size")
        payload, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(hmac.new(secret, payload, hashlib.sha256).digest(), signature):
            raise ValueError("cursor signature")
        value = json.loads(payload)
        if (
            type(value) is not dict
            or set(value) != {"version", "role", "status", "after"}
            or value["version"] != "1.0"
            or value["role"] != role
            or value["status"] != status
            or type(value["after"]) is not list
            or len(value["after"]) != 2
            or any(type(item) is not str for item in value["after"])
        ):
            raise ValueError("cursor binding")
        return value["after"][0], value["after"][1]
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise _invalid("分页游标无效，或不属于当前角色与筛选条件。") from exc


def list_mesh_run_catalog(
    root: Path,
    *,
    principal: Principal,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    cursor_secret: bytes | None = None,
) -> dict:
    role = _viewer_role(principal)
    normalized_status = "all" if status is None else status
    if type(normalized_status) is not str or normalized_status not in _STATUSES:
        raise _invalid("网格运行状态筛选无效。")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _invalid("分页大小必须是 1 到 100 的整数。")
    key = _secret(cursor_secret)
    after = _decode_cursor(
        cursor, role=role, status=normalized_status, secret=key
    )
    project_root = Path(root)
    contexts = [
        _run_context(project_root, load_mesh_run(project_root, run_id))
        for run_id in _committed_run_ids(project_root)
    ]
    contexts = [item for item in contexts if _matches(item, normalized_status)]
    if role == "operator":
        contexts = [item for item in contexts if item["publication_status"] == "current"]
    contexts.sort(key=lambda item: (item["created_at"], item["run"]["run_id"]))
    if after is not None:
        contexts = [
            item
            for item in contexts
            if (item["created_at"], item["run"]["run_id"]) > after
        ]
    page = contexts[:limit]
    items = [
        _business_run(item)
        if role == "operator"
        else _professional_run(project_root, item, role=role)
        for item in page
    ]
    next_cursor = None
    if len(contexts) > limit:
        last = page[-1]
        next_cursor = _encode_cursor(
            role=role,
            status=normalized_status,
            after=(last["created_at"], last["run"]["run_id"]),
            secret=key,
        )
    return {"viewer_role": role, "items": items, "next_cursor": next_cursor}


def load_mesh_run_catalog(
    root: Path, run_id: str, *, principal: Principal
) -> dict:
    role = _viewer_role(principal)
    project_root = Path(root)
    context = _run_context(project_root, load_mesh_run(project_root, run_id))
    if role == "operator":
        if context["publication_status"] != "current":
            raise ScanToMeshError("mesh_run_not_found", "当前已发布网格运行不存在。")
        return {"viewer_role": role, **_business_run(context)}
    return _professional_run(project_root, context, role=role)


def load_mesh_run_preview(
    root: Path, run_id: str, *, principal: Principal
) -> dict:
    role = _viewer_role(principal)
    if role == "operator":
        raise ScanToMeshError(
            "permission_denied", "业务角色不能读取专业网格几何预览。"
        )
    project_root = Path(root)
    run = load_mesh_run(project_root, run_id)
    preview = load_bounded_mesh_preview(
        project_root / "scan_to_cad" / "mesh_runs" / run_id
    )
    if preview["source_snapshot_fingerprint"] != run["source_snapshot_fingerprint"]:
        raise ScanToMeshError(
            "mesh_run_integrity_error", "网格预览与来源快照不一致。"
        )
    for key in ("raw_mesh", "repaired_mesh"):
        expected = run["artifacts"][key]
        actual = preview[key]
        if (
            actual["vertex_count"] != expected["vertex_count"]
            or actual["triangle_count"] != expected["triangle_count"]
        ):
            raise ScanToMeshError(
                "mesh_run_integrity_error", "网格预览与运行清单不一致。"
            )
    preview.pop("source_snapshot_fingerprint")
    return {"viewer_role": role, "run_id": run_id, **preview}


def list_mesh_release_catalog(
    root: Path, object_key: str, *, principal: Principal
) -> dict:
    role = _viewer_role(principal)
    project_root = Path(root)
    releases = list_mesh_releases(project_root, object_key)
    current = load_current_mesh_release(project_root, object_key)
    if role == "operator":
        items = [] if current is None else [_release_summary(current)]
    else:
        items = [_release_summary(item) for item in releases]
        if role == "auditor":
            for item, release in zip(items, releases, strict=True):
                item["audit"] = _audit_projection(project_root, release["operation_id"])
    return {
        "viewer_role": role,
        "object_key": object_key,
        "current_release_id": None if current is None else current["release_id"],
        "items": items,
    }
