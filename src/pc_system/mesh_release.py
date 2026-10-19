"""网格候选的一次性决定、不可变发布、CAS 当前头与回滚。"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.mesh_reconstruction import (
    _digest,
    _publish,
    _read_canonical,
    _require_plain,
    load_mesh_run,
)
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    fail_operation,
    load_operation,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_sampling import _canonical_json_bytes
from pc_system.scan_to_mesh_errors import ScanToMeshError


_DECISION_OWNER_FIELDS = {
    "schema_version",
    "run_id",
    "operation_id",
    "actor_id",
    "principal_source",
    "request_id",
    "request_fingerprint",
    "idempotency_key_hash",
}
_RELEASE_OWNER_FIELDS = {
    "schema_version",
    "object_key",
    "release_id",
    "operation_id",
    "actor_id",
    "request_id",
    "request_fingerprint",
    "idempotency_key_hash",
}


def _error(code: str, message: str) -> ScanToMeshError:
    return ScanToMeshError(code, message)


def _scan_error(exc: ModelMatchingError) -> ScanToMeshError:
    if isinstance(exc, ScanToMeshError):
        return exc
    return _error(exc.code, str(exc))


def _identifier(value: object, label: str) -> str:
    try:
        return validate_identifier(value, label)
    except (TypeError, ValueError) as exc:
        raise _error("mesh_release_invalid", f"{label} 无效。") from exc


def _reason(value: object) -> str:
    if type(value) is not str:
        raise _error("mesh_release_invalid", "原因必须是文本。")
    result = value.strip()
    if not result or len(result) > 2000 or "\x00" in result:
        raise _error("mesh_release_invalid", "原因必须为 1 至 2000 个有效字符。")
    return result


def _prepare_plain_directory(root: Path, *parts: str) -> Path:
    current = Path(root)
    try:
        _require_plain(current, directory=True)
        for part in parts:
            current = current / part
            current.mkdir(exist_ok=True)
            _require_plain(current, directory=True)
        return current
    except (OSError, ModelMatchingError) as exc:
        raise _error("mesh_release_integrity_error", "网格发布目录不安全。") from exc


def _decision_directory(root: Path, run_id: str, *, create: bool = False) -> Path:
    if create:
        return _prepare_plain_directory(Path(root), "scan_to_cad", "mesh_reviews", run_id)
    return Path(root) / "scan_to_cad" / "mesh_reviews" / run_id


def _object_directory(root: Path, object_key: str, *, create: bool = False) -> Path:
    if create:
        return _prepare_plain_directory(Path(root), "scan_to_cad", "mesh_objects", object_key)
    return Path(root) / "scan_to_cad" / "mesh_objects" / object_key


def mesh_object_key(run: dict) -> str:
    try:
        payload = {
            "source_kind": run["source_kind"],
            "source_identity": run["source_identity"],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("mesh_release_integrity_error", "网格运行来源身份无效。") from exc
    return f"mesh-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _decision_result(decision: dict) -> dict:
    return {
        "run_id": decision["run_id"],
        "decision": decision["decision"],
        "decision_fingerprint": decision["decision_fingerprint"],
    }


def _decision_owner(operation: dict, run_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "operation_id": operation["operation_id"],
        "actor_id": operation["actor_id"],
        "principal_source": operation["principal_source"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
        "idempotency_key_hash": operation["idempotency_key_hash"],
    }


def _decision_value(
    *,
    run: dict,
    quality: dict,
    policy: dict,
    decision: str,
    reason: str,
    acknowledgements: list[str],
    principal: Principal,
    operation: dict,
) -> dict:
    risks = quality["reasons"]
    if decision not in {"approve", "reject"}:
        raise _error("mesh_decision_not_allowed", "未知网格决定。")
    if principal.source == "system":
        if (
            decision != "approve"
            or run["status"] != "passed"
            or not policy["decisions"]["auto_publish_passed"]
        ):
            raise _error("mesh_decision_not_allowed", "系统主体不能执行此网格决定。")
        automated = True
    else:
        try:
            require_any_role(principal, {"expert"})
        except ModelMatchingError as exc:
            raise _error("permission_denied", "只有专家可以作出网格决定。") from exc
        automated = False
    if decision == "approve":
        if run["status"] == "rejected":
            raise _error("mesh_decision_not_allowed", "被拒绝网格不能批准。")
        expected = risks if run["status"] == "review_required" else []
        if acknowledgements != expected:
            raise _error(
                "mesh_decision_acknowledgement_required",
                "批准必须精确确认全部质量风险码。",
            )
    elif acknowledgements:
        raise _error("mesh_decision_not_allowed", "驳回决定不能携带风险确认。")
    payload = {
        "schema_version": "1.0",
        "run_id": run["run_id"],
        "decision": decision,
        "reason": reason,
        "acknowledgements": acknowledgements,
        "automated": automated,
        "run_status": run["status"],
        "run_manifest_fingerprint": run["manifest_fingerprint"],
        "quality_report_fingerprint": run["quality_report_fingerprint"],
        "policy_id": run["policy_id"],
        "policy_fingerprint": run["policy_fingerprint"],
        "operation_id": operation["operation_id"],
        "decided_by": operation["actor_id"],
        "decided_at": operation["started_at"],
    }
    return {**payload, "decision_fingerprint": _digest(payload)}


def _load_decision_bundle(root: Path, run_id: str, *, validate_audit: bool) -> dict:
    directory = _decision_directory(root, run_id)
    try:
        commit = _read_canonical(directory / "commit.json")
    except FileNotFoundError as exc:
        raise _error("mesh_decision_not_found", "网格运行尚无最终决定。") from exc
    try:
        owner = _read_canonical(directory / "owner.json")
        decision = _read_canonical(directory / "decision.json")
        if set(owner) != _DECISION_OWNER_FIELDS or owner["run_id"] != run_id:
            raise ValueError("decision owner differs")
        run = load_mesh_run(root, run_id)
        payload = dict(decision)
        fingerprint = payload.pop("decision_fingerprint")
        if (
            _digest(payload) != fingerprint
            or decision["run_manifest_fingerprint"] != run["manifest_fingerprint"]
            or decision["quality_report_fingerprint"]
            != run["quality_report_fingerprint"]
            or decision["operation_id"] != owner["operation_id"]
            or commit
            != {
                "schema_version": "1.0",
                "run_id": run_id,
                "operation_id": owner["operation_id"],
                "decision_fingerprint": fingerprint,
            }
        ):
            raise ValueError("decision evidence differs")
        if validate_audit:
            operation = load_operation(root, owner["operation_id"])
            if operation.get("status") != "completed" or operation.get("result") != _decision_result(decision):
                raise ValueError("decision audit differs")
        return decision
    except (FileNotFoundError, KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        raise _error("mesh_decision_integrity_error", "网格决定完整性校验失败。") from exc


def load_mesh_decision(project_root: Path, run_id: str) -> dict:
    run_id = _identifier(run_id, "run_id")
    root = Path(project_root)
    try:
        with model_resource_lock(root, "mesh-review", run_id):
            return _load_decision_bundle(root, run_id, validate_audit=True)
    except ScanToMeshError:
        raise
    except ModelMatchingError as exc:
        raise _scan_error(exc) from exc


def _fail_running(root: Path, operation_id: str, error: ScanToMeshError) -> None:
    try:
        operation = load_operation(root, operation_id)
        if operation["status"] == "running":
            fail_operation(root, operation_id, error.code, str(error))
    except ModelMatchingError:
        pass


def decide_mesh_run(
    project_root: Path,
    *,
    run_id: str,
    decision: str,
    reason: str,
    acknowledgements: list[str],
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    run_id = _identifier(run_id, "run_id")
    reason = _reason(reason)
    if type(acknowledgements) is not list:
        raise _error("mesh_release_invalid", "风险确认必须是列表。")
    try:
        normalized = sorted(_identifier(value, "risk_code") for value in acknowledgements)
    except ScanToMeshError as exc:
        raise _error("mesh_release_invalid", "风险确认码无效。") from exc
    if len(normalized) != len(set(normalized)):
        raise _error("mesh_release_invalid", "风险确认码不能重复。")
    request = {
        "run_id": run_id,
        "decision": decision,
        "reason": reason,
        "acknowledgements": normalized,
    }
    try:
        operation, replayed = start_operation(
            root,
            operation_id=operation_id,
            operation_type="mesh.review.decide",
            principal=principal,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_payload=request,
        )
    except ModelMatchingError as exc:
        raise _scan_error(exc) from exc
    if replayed and operation["status"] == "failed":
        recorded = operation.get("error") or {}
        raise _error(recorded.get("code", "mesh_decision_not_allowed"), recorded.get("message", "网格决定失败。"))
    if replayed and operation["status"] == "completed":
        return load_mesh_decision(root, run_id)
    try:
        with model_resource_lock(root, "mesh-review", run_id):
            run = load_mesh_run(root, run_id)
            run_directory = root / "scan_to_cad" / "mesh_runs" / run_id
            quality = _read_canonical(run_directory / "quality_report.json")
            policy = _read_canonical(run_directory / "policy_snapshot.json")
            expected = _decision_value(
                run=run,
                quality=quality,
                policy=policy,
                decision=decision,
                reason=reason,
                acknowledgements=normalized,
                principal=principal,
                operation=operation,
            )
            directory = _decision_directory(root, run_id, create=True)
            owner_path = directory / "owner.json"
            owner = _decision_owner(operation, run_id)
            try:
                existing_owner = _read_canonical(owner_path)
            except FileNotFoundError:
                if list(directory.iterdir()):
                    raise _error("publication_recovery_required", "决定目录非空但缺少所有者。")
                _publish(owner_path, owner)
                existing_owner = owner
            if existing_owner != owner:
                if (directory / "commit.json").exists():
                    raise _error("mesh_decision_conflict", "网格运行已有最终决定。")
                raise _error("operation_busy", "网格决定由其他操作持有。")
            _publish(directory / "decision.json", expected)
            _publish(
                directory / "commit.json",
                {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "decision_fingerprint": expected["decision_fingerprint"],
                },
            )
            try:
                ensure_operation_event(root, operation_id, "mesh.review_recorded", _decision_result(expected))
                complete_operation(root, operation_id, _decision_result(expected))
            except ModelMatchingError as exc:
                raise _error("publication_recovery_required", "网格决定已提交但审计仍需恢复。") from exc
            return _load_decision_bundle(root, run_id, validate_audit=True)
    except ScanToMeshError as error:
        if error.code not in {"operation_busy", "publication_recovery_required"}:
            _fail_running(root, operation_id, error)
        raise
    except ModelMatchingError as exc:
        error = _scan_error(exc)
        _fail_running(root, operation_id, error)
        raise error from exc


def _release_result(release: dict) -> dict:
    return {
        "object_key": release["object_key"],
        "release_id": release["release_id"],
        "run_id": release["run_id"],
        "release_fingerprint": release["release_fingerprint"],
    }


def _release_owner(operation: dict, object_key: str, release_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "object_key": object_key,
        "release_id": release_id,
        "operation_id": operation["operation_id"],
        "actor_id": operation["actor_id"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
        "idempotency_key_hash": operation["idempotency_key_hash"],
    }


def _release_directory(root: Path, object_key: str, release_id: str, *, create=False) -> Path:
    object_directory = _object_directory(root, object_key, create=create)
    releases = object_directory / "releases"
    if create:
        releases.mkdir(exist_ok=True)
        _require_plain(releases, directory=True)
        directory = releases / release_id
        directory.mkdir(exist_ok=True)
        _require_plain(directory, directory=True)
        return directory
    return releases / release_id


def _load_release_bundle(root: Path, object_key: str, release_id: str, *, validate_audit=True) -> dict:
    directory = _release_directory(root, object_key, release_id)
    try:
        commit = _read_canonical(directory / "commit.json")
    except FileNotFoundError as exc:
        raise _error("mesh_release_not_found", "网格发布版本不存在。") from exc
    try:
        owner = _read_canonical(directory / "owner.json")
        release = _read_canonical(directory / "release.json")
        if set(owner) != _RELEASE_OWNER_FIELDS or owner["object_key"] != object_key or owner["release_id"] != release_id:
            raise ValueError("release owner differs")
        payload = dict(release)
        fingerprint = payload.pop("release_fingerprint")
        if _digest(payload) != fingerprint or release["operation_id"] != owner["operation_id"]:
            raise ValueError("release fingerprint differs")
        run = load_mesh_run(root, release["run_id"])
        decision = load_mesh_decision(root, release["run_id"])
        if (
            mesh_object_key(run) != object_key
            or decision["decision"] != "approve"
            or release["run_manifest_fingerprint"] != run["manifest_fingerprint"]
            or release["decision_fingerprint"] != decision["decision_fingerprint"]
            or release["artifacts"] != run["artifacts"]
            or commit
            != {
                "schema_version": "1.0",
                "object_key": object_key,
                "release_id": release_id,
                "operation_id": owner["operation_id"],
                "release_fingerprint": fingerprint,
            }
        ):
            raise ValueError("release evidence differs")
        if validate_audit:
            operation = load_operation(root, owner["operation_id"])
            if operation.get("status") != "completed" or operation.get("result") != _release_result(release):
                raise ValueError("release audit differs")
        return release
    except (FileNotFoundError, KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        raise _error("mesh_release_integrity_error", "网格发布版本完整性校验失败。") from exc


def _list_mesh_releases(
    root: Path, object_key: str, *, exclude_release_id: str | None = None
) -> list[dict]:
    parent = _object_directory(root, object_key) / "releases"
    try:
        directories = sorted(parent.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        return []
    releases = [
        _load_release_bundle(root, object_key, directory.name)
        for directory in directories
        if directory.is_dir()
        and directory.name != exclude_release_id
        and (directory / "commit.json").exists()
    ]
    by_id = {item["release_id"]: item for item in releases}
    if len(by_id) != len(releases):
        raise _error("mesh_release_integrity_error", "网格发布历史存在重复 ID。")
    ordered = []
    current = None
    while len(ordered) < len(releases):
        candidates = [item for item in releases if item["previous_release_id"] == current and item not in ordered]
        if len(candidates) != 1:
            raise _error("mesh_release_integrity_error", "网格发布历史不是单一链。")
        ordered.append(candidates[0])
        current = candidates[0]["release_id"]
    return ordered


def list_mesh_releases(project_root: Path, object_key: str) -> list[dict]:
    object_key = _identifier(object_key, "object_key")
    return _list_mesh_releases(Path(project_root), object_key)


def _projection_path(root: Path, object_key: str) -> Path:
    return _object_directory(root, object_key) / "current_release.json"


def _projection(release: dict) -> dict:
    return {
        "schema_version": "1.0",
        "object_key": release["object_key"],
        "current_release_id": release["release_id"],
        "run_id": release["run_id"],
        "release_fingerprint": release["release_fingerprint"],
    }


def _current_unlocked(
    root: Path, object_key: str, *, exclude_release_id: str | None = None
) -> dict | None:
    path = _projection_path(root, object_key)
    try:
        projection = _read_canonical(path)
    except FileNotFoundError:
        releases_root = _object_directory(root, object_key) / "releases"
        try:
            committed = [
                directory.name
                for directory in releases_root.iterdir()
                if directory.is_dir()
                and directory.name != exclude_release_id
                and (directory / "commit.json").exists()
            ]
        except FileNotFoundError:
            committed = []
        if committed:
            raise _error("mesh_release_integrity_error", "发布历史存在但当前头缺失。")
        return None
    release = _load_release_bundle(root, object_key, projection["current_release_id"])
    if projection != _projection(release):
        raise _error("mesh_release_integrity_error", "网格当前头与发布版本不一致。")
    history = _list_mesh_releases(
        root, object_key, exclude_release_id=exclude_release_id
    )
    if not history or history[-1]["release_id"] != release["release_id"]:
        raise _error("mesh_release_integrity_error", "网格当前头不是已完成历史链头。")
    return release


def load_current_mesh_release(project_root: Path, object_key: str) -> dict | None:
    object_key = _identifier(object_key, "object_key")
    root = Path(project_root)
    try:
        with model_resource_lock(root, "mesh-release", object_key):
            return _current_unlocked(root, object_key)
    except ScanToMeshError:
        raise
    except ModelMatchingError as exc:
        raise _scan_error(exc) from exc


def _write_projection(root: Path, release: dict) -> None:
    path = _projection_path(root, release["object_key"])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical_json_bytes(_projection(release)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        if _current_unlocked(root, release["object_key"]) != release:
            raise ValueError("projection differs")
    except (OSError, ValueError, ModelMatchingError) as exc:
        raise _error("publication_recovery_required", "发布版本已提交但当前头仍需恢复。") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def release_mesh_run(
    project_root: Path,
    *,
    object_key: str,
    run_id: str,
    release_id: str,
    action: str,
    reason: str,
    expected_current_release_id: str | None,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    object_key = _identifier(object_key, "object_key")
    run_id = _identifier(run_id, "run_id")
    release_id = _identifier(release_id, "release_id")
    reason = _reason(reason)
    if action not in {"activate", "replace", "rollback"}:
        raise _error("mesh_release_invalid", "未知网格发布动作。")
    if expected_current_release_id is not None:
        expected_current_release_id = _identifier(expected_current_release_id, "expected_current_release_id")
    request = {
        "object_key": object_key,
        "run_id": run_id,
        "release_id": release_id,
        "action": action,
        "reason": reason,
        "expected_current_release_id": expected_current_release_id,
    }
    try:
        operation, replayed = start_operation(
            root,
            operation_id=operation_id,
            operation_type="mesh.release.publish",
            principal=principal,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_payload=request,
        )
    except ModelMatchingError as exc:
        raise _scan_error(exc) from exc
    if replayed and operation["status"] == "failed":
        recorded = operation.get("error") or {}
        raise _error(recorded.get("code", "mesh_release_invalid"), recorded.get("message", "网格发布失败。"))
    try:
        require_any_role(principal, {"expert"})
    except ModelMatchingError as exc:
        error = _error("permission_denied", "只有专家可以发布网格版本。")
        _fail_running(root, operation_id, error)
        raise error from exc
    try:
        with model_resource_lock(root, "mesh-release", object_key):
            _object_directory(root, object_key, create=True)
            try:
                actual_current = _current_unlocked(
                    root, object_key, exclude_release_id=release_id
                )
            except ScanToMeshError as exc:
                if replayed and operation["status"] == "completed" and exc.code == "mesh_release_integrity_error":
                    actual_current = None
                else:
                    raise
            actual_id = None if actual_current is None else actual_current["release_id"]
            if replayed and operation["status"] == "completed":
                release = _load_release_bundle(root, object_key, release_id)
                if actual_id == release_id:
                    return release
                if actual_id != release["previous_release_id"]:
                    raise _error("mesh_release_integrity_error", "已完成发布无法安全恢复当前头。")
                _write_projection(root, release)
                return release
            if actual_id != expected_current_release_id:
                raise _error("mesh_release_conflict", "网格当前发布头已变化。")
            if action == "activate" and actual_id is not None:
                raise _error("mesh_release_conflict", "activate 只允许空当前头。")
            if action in {"replace", "rollback"} and actual_id is None:
                raise _error("mesh_release_conflict", "replace/rollback 要求已有当前头。")
            run = load_mesh_run(root, run_id)
            decision = load_mesh_decision(root, run_id)
            if decision["decision"] != "approve":
                raise _error("mesh_release_not_allowed", "只有已批准网格可以发布。")
            if mesh_object_key(run) != object_key:
                raise _error("mesh_release_conflict", "网格运行不属于指定对象。")
            history = _list_mesh_releases(
                root, object_key, exclude_release_id=release_id
            )
            rollback_of = None
            if action == "rollback":
                targets = [item for item in history if item["run_id"] == run_id and item["action"] != "rollback"]
                if len(targets) != 1:
                    raise _error("mesh_release_not_found", "回滚运行没有唯一历史发布版本。")
                target = targets[0]
                rollback_of = target["release_id"]
                run = load_mesh_run(root, target["run_id"])
                decision = load_mesh_decision(root, target["run_id"])
            elif any(item["run_id"] == run_id and item["action"] != "rollback" for item in history):
                raise _error("mesh_release_conflict", "同一网格运行已经发布。")
            payload = {
                "schema_version": "1.0",
                "object_key": object_key,
                "release_id": release_id,
                "run_id": run["run_id"],
                "action": action,
                "previous_release_id": actual_id,
                "rollback_of_release_id": rollback_of,
                "reason": reason,
                "run_manifest_fingerprint": run["manifest_fingerprint"],
                "decision_fingerprint": decision["decision_fingerprint"],
                "source_kind": run["source_kind"],
                "source_identity": run["source_identity"],
                "artifacts": run["artifacts"],
                "operation_id": operation_id,
                "created_by": operation["actor_id"],
                "created_at": operation["started_at"],
                "status": "published",
            }
            release = {**payload, "release_fingerprint": _digest(payload)}
            directory = _release_directory(root, object_key, release_id, create=True)
            owner = _release_owner(operation, object_key, release_id)
            try:
                existing_owner = _read_canonical(directory / "owner.json")
            except FileNotFoundError:
                if list(directory.iterdir()):
                    raise _error("publication_recovery_required", "发布目录非空但缺少所有者。")
                _publish(directory / "owner.json", owner)
                existing_owner = owner
            if existing_owner != owner:
                raise _error("operation_busy", "发布版本由其他操作持有。")
            _publish(directory / "release.json", release)
            _publish(
                directory / "commit.json",
                {
                    "schema_version": "1.0",
                    "object_key": object_key,
                    "release_id": release_id,
                    "operation_id": operation_id,
                    "release_fingerprint": release["release_fingerprint"],
                },
            )
            try:
                ensure_operation_event(root, operation_id, "mesh.release_created", _release_result(release))
                ensure_operation_event(
                    root,
                    operation_id,
                    "mesh.release_head_changed",
                    {
                        "object_key": object_key,
                        "previous_release_id": actual_id,
                        "current_release_id": release_id,
                        "action": action,
                    },
                )
                complete_operation(root, operation_id, _release_result(release))
            except ModelMatchingError as exc:
                raise _error("publication_recovery_required", "网格发布已提交但审计仍需恢复。") from exc
            _write_projection(root, release)
            return _load_release_bundle(root, object_key, release_id)
    except ScanToMeshError as error:
        if error.code not in {"operation_busy", "publication_recovery_required"}:
            _fail_running(root, operation_id, error)
        raise
    except ModelMatchingError as exc:
        error = _scan_error(exc)
        _fail_running(root, operation_id, error)
        raise error from exc
