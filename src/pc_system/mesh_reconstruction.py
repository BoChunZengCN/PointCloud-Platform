"""可恢复的点云到网格运行编排与不可变运行包。"""

import hashlib
import json
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.mesh_quality import evaluate_mesh_quality
from pc_system.mesh_reconstruction_config import (
    build_mesh_config,
    build_mesh_quality_policy,
    load_mesh_config,
    load_mesh_quality_policy,
)
from pc_system.mesh_worker_contract import (
    MeshWorker,
    _read_plain_bytes,
    load_mesh_worker_request,
    prepare_mesh_worker_request,
    validate_mesh_worker_result,
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
from pc_system.model_release import _require_plain
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_sampling import _canonical_json_bytes, _publish_exact_json
from pc_system.scan_to_mesh_errors import ScanToMeshError
from pc_system.scan_to_mesh_source import freeze_mesh_source


_MAXIMUM_SNAPSHOT_BYTES = 600 * 1024 * 1024
_OUTPUT_NAMES = {
    "worker_result.json",
    "raw_mesh.ply",
    "repaired_mesh.ply",
    "preview_mesh.glb",
    "mesh_metrics.json",
}
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
_POLICY_FIELDS = {
    "schema_version",
    "policy_kind",
    "production_context",
    "requires_watertight",
    "hard_limits",
    "review_limits",
    "decisions",
}
_OWNER_FIELDS = {
    "schema_version",
    "run_id",
    "operation_id",
    "actor_id",
    "roles",
    "principal_source",
    "request_id",
    "request_fingerprint",
    "idempotency_key_hash",
    "source_kind",
    "source_ref",
    "config_id",
    "policy_id",
}


def _error(code: str, message: str) -> ScanToMeshError:
    return ScanToMeshError(code, message)


def _as_scan_error(exc: ModelMatchingError) -> ScanToMeshError:
    if isinstance(exc, ScanToMeshError):
        return exc
    return _error(exc.code, str(exc))


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_canonical(path: Path, *, maximum_bytes: int = _MAXIMUM_SNAPSHOT_BYTES) -> dict:
    try:
        payload = _read_plain_bytes(path, maximum_bytes=maximum_bytes)
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
            object_pairs_hook=_strict_object,
        )
        if type(value) is not dict or payload != _canonical_json_bytes(value):
            raise ValueError("noncanonical JSON")
        return value
    except ScanToMeshError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise FileNotFoundError(path) from exc
        raise _error("mesh_run_integrity_error", "网格运行工件无效。") from exc
    except (OSError, UnicodeError, ValueError, ModelMatchingError) as exc:
        raise _error("mesh_run_integrity_error", "网格运行工件无效。") from exc


def _publish(path: Path, value: dict) -> None:
    try:
        _publish_exact_json(
            path,
            value,
            conflict_code="mesh_run_integrity_error",
            conflict_message="网格运行工件与已有内容冲突。",
        )
    except OSError as exc:
        raise _error(
            "publication_recovery_required",
            "网格运行工件发布中断，必须由同一操作恢复。",
        ) from exc
    except ModelMatchingError as exc:
        raise _as_scan_error(exc) from exc


def _run_directory(root: Path, run_id: str) -> Path:
    return Path(root) / "scan_to_cad" / "mesh_runs" / run_id


def _prepare_directory(root: Path, run_id: str) -> Path:
    try:
        _require_plain(root, directory=True)
        base = root / "scan_to_cad"
        base.mkdir(exist_ok=True)
        _require_plain(base, directory=True)
        runs = base / "mesh_runs"
        runs.mkdir(exist_ok=True)
        _require_plain(runs, directory=True)
        directory = runs / run_id
        directory.mkdir(exist_ok=True)
        _require_plain(directory, directory=True)
        return directory
    except (OSError, ModelMatchingError) as exc:
        raise _error("mesh_run_integrity_error", "网格运行目录不安全。") from exc


def _expected_owner(
    operation: dict,
    *,
    run_id: str,
    source_kind: str,
    source_ref: dict,
    config_id: str,
    policy_id: str,
) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "operation_id": operation["operation_id"],
        "actor_id": operation["actor_id"],
        "roles": operation["roles"],
        "principal_source": operation["principal_source"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
        "idempotency_key_hash": operation["idempotency_key_hash"],
        "source_kind": source_kind,
        "source_ref": source_ref,
        "config_id": config_id,
        "policy_id": policy_id,
    }


def _claim_owner(directory: Path, expected: dict) -> dict:
    owner_path = directory / "owner.json"
    try:
        owner = _read_canonical(owner_path)
    except FileNotFoundError:
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            raise _error("mesh_run_integrity_error", "无法检查网格运行目录。") from exc
        if entries:
            raise _error(
                "publication_recovery_required",
                "网格运行目录非空但缺少可证明的所有者。",
            )
        _publish(owner_path, expected)
        owner = _read_canonical(owner_path)
    if set(owner) != _OWNER_FIELDS:
        raise _error("mesh_run_integrity_error", "网格运行所有者字段无效。")
    if owner != expected:
        raise _error("operation_busy", "网格运行已由其他操作持有。")
    return owner


def _validate_source(value: dict, source_kind: str, source_ref: dict) -> dict:
    try:
        payload = dict(value)
        fingerprint = payload.pop("fingerprint")
        if (
            type(fingerprint) is not str
            or len(fingerprint) != 64
            or _digest(payload) != fingerprint
            or value.get("schema_version") != "1.0"
            or value.get("source_kind") != source_kind
            or value.get("coordinate_unit") != "m"
            or type(value.get("points")) is not list
            or value.get("point_count") != len(value["points"])
            or type(value.get("source_identity")) is not dict
            or any(
                value["source_identity"].get(key) != expected
                for key, expected in source_ref.items()
            )
        ):
            raise ValueError("source snapshot differs")
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("mesh_run_integrity_error", "冻结来源快照无效。") from exc
    return value


def _validate_config(value: dict, config_id: str) -> dict:
    try:
        rebuilt = build_mesh_config(
            config_id, {key: value[key] for key in _CONFIG_FIELDS}
        )
        if (
            value.get("config_id") != config_id
            or value.get("config_fingerprint") != rebuilt["config_fingerprint"]
        ):
            raise ValueError("config fingerprint differs")
    except (KeyError, ValueError, ModelMatchingError) as exc:
        raise _error("mesh_run_integrity_error", "冻结网格配置无效。") from exc
    return value


def _validate_policy(value: dict, policy_id: str) -> dict:
    try:
        rebuilt = build_mesh_quality_policy(
            policy_id, {key: value[key] for key in _POLICY_FIELDS}
        )
        if (
            value.get("policy_id") != policy_id
            or value.get("policy_fingerprint") != rebuilt["policy_fingerprint"]
        ):
            raise ValueError("policy fingerprint differs")
    except (KeyError, ValueError, ModelMatchingError) as exc:
        raise _error("mesh_run_integrity_error", "冻结质量策略无效。") from exc
    return value


def _load_or_freeze(
    path: Path,
    producer,
    validator,
) -> dict:
    try:
        value = _read_canonical(path)
    except FileNotFoundError:
        value = producer()
        validator(value)
        _publish(path, value)
        value = _read_canonical(path)
    return validator(value)


def _read_metrics(directory: Path, result: dict, maximum_bytes: int) -> dict:
    descriptor = result["artifacts"]["mesh_metrics"]
    try:
        payload = _read_plain_bytes(
            directory / "mesh_metrics.json", maximum_bytes=maximum_bytes
        )
        if (
            len(payload) != descriptor["bytes"]
            or hashlib.sha256(payload).hexdigest() != descriptor["sha256"]
        ):
            raise ValueError("metrics descriptor differs")
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
            object_pairs_hook=_strict_object,
        )
        if type(value) is not dict or payload != _canonical_json_bytes(value):
            raise ValueError("metrics JSON differs")
        return value
    except (KeyError, TypeError, UnicodeError, ValueError, ModelMatchingError) as exc:
        raise _error("mesh_run_integrity_error", "网格指标工件无效。") from exc


def _quality_report(metrics: dict, policy: dict, expected_metrics_fingerprint: str) -> dict:
    report = evaluate_mesh_quality(metrics, policy)
    if report["metrics_fingerprint"] != expected_metrics_fingerprint:
        raise _error("mesh_run_integrity_error", "质量报告与工作器指标不一致。")
    return {**report, "quality_report_fingerprint": _digest(report)}


def _manifest(
    *,
    run_id: str,
    owner: dict,
    source: dict,
    config: dict,
    policy: dict,
    request: dict,
    result: dict,
    quality: dict,
) -> dict:
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": quality["status"],
        "state_history": [
            "requested",
            "running",
            "quality_checked",
            quality["status"],
        ],
        "operation_id": owner["operation_id"],
        "source_kind": source["source_kind"],
        "source_identity": source["source_identity"],
        "source_snapshot_fingerprint": source["fingerprint"],
        "config_id": config["config_id"],
        "config_fingerprint": config["config_fingerprint"],
        "policy_id": policy["policy_id"],
        "policy_fingerprint": policy["policy_fingerprint"],
        "worker_request_fingerprint": request["request_fingerprint"],
        "worker_result_fingerprint": _digest(result),
        "quality_report_fingerprint": quality["quality_report_fingerprint"],
        "artifacts": result["artifacts"],
    }
    return {**payload, "manifest_fingerprint": _digest(payload)}


def _commit(manifest: dict) -> dict:
    payload = {
        "schema_version": "1.0",
        "run_id": manifest["run_id"],
        "operation_id": manifest["operation_id"],
        "status": manifest["status"],
        "manifest_fingerprint": manifest["manifest_fingerprint"],
    }
    return {**payload, "commit_fingerprint": _digest(payload)}


def _result(manifest: dict, commit: dict) -> dict:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "commit_fingerprint": commit["commit_fingerprint"],
    }


def _load_bundle(directory: Path, *, validate_audit: bool, root: Path) -> dict:
    try:
        stored_commit = _read_canonical(directory / "commit.json")
    except FileNotFoundError as exc:
        raise _error("mesh_run_not_found", "已提交网格运行不存在。") from exc
    except ScanToMeshError as exc:
        raise _error("mesh_run_integrity_error", "网格运行提交标记无效。") from exc
    try:
        owner = _read_canonical(directory / "owner.json")
        if set(owner) != _OWNER_FIELDS or owner.get("run_id") != directory.name:
            raise ValueError("owner fields differ")
        source = _validate_source(
            _read_canonical(directory / "source_snapshot.json"),
            owner["source_kind"],
            owner["source_ref"],
        )
        config = _validate_config(
            _read_canonical(directory / "config_snapshot.json"), owner["config_id"]
        )
        policy = _validate_policy(
            _read_canonical(directory / "policy_snapshot.json"), owner["policy_id"]
        )
        request = load_mesh_worker_request(directory)
        result = validate_mesh_worker_result(directory, request)
        metrics = _read_metrics(
            directory, result, request["limits"]["maximum_artifact_bytes"]
        )
        quality = _quality_report(metrics, policy, result["metrics_fingerprint"])
        if _read_canonical(directory / "quality_report.json") != quality:
            raise ValueError("quality report differs")
        manifest = _manifest(
            run_id=owner["run_id"],
            owner=owner,
            source=source,
            config=config,
            policy=policy,
            request=request,
            result=result,
            quality=quality,
        )
        if _read_canonical(directory / "run_manifest.json") != manifest:
            raise ValueError("manifest differs")
        commit = _commit(manifest)
        if stored_commit != commit:
            raise ValueError("commit differs")
        if validate_audit:
            operation = load_operation(root, owner["operation_id"])
            if operation.get("status") != "completed" or operation.get("result") != _result(manifest, commit):
                raise ValueError("completed audit differs")
        return manifest
    except (FileNotFoundError, KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        raise _error("mesh_run_integrity_error", "网格运行包完整性校验失败。") from exc


def _fail_running(root: Path, operation_id: str, error: ScanToMeshError) -> None:
    try:
        if load_operation(root, operation_id)["status"] == "running":
            fail_operation(root, operation_id, error.code, str(error))
    except ModelMatchingError:
        pass


def _execute_worker(
    directory: Path, request: dict, worker: MeshWorker, operation_id: str, root: Path
) -> dict:
    result_path = directory / "worker_result.json"
    if result_path.exists():
        try:
            return validate_mesh_worker_result(directory, request)
        except ScanToMeshError as exc:
            raise _error(
                "publication_recovery_required",
                "已有工作器结果无法证明完整，禁止自动覆盖或重跑。",
            ) from exc
    if any((directory / name).exists() for name in _OUTPUT_NAMES - {"worker_result.json"}):
        raise _error(
            "publication_recovery_required",
            "工作器留下未确定工件，禁止自动覆盖或重跑。",
        )
    try:
        worker(request, directory)
        return validate_mesh_worker_result(directory, request)
    except ModelMatchingError as exc:
        error = _as_scan_error(exc)
    except Exception as exc:
        error = _error("mesh_worker_failed", "网格工作器执行失败。")
        error.__cause__ = exc
    _fail_running(root, operation_id, error)
    raise error


def _finalize_audit(
    root: Path,
    operation_id: str,
    manifest: dict,
    commit: dict,
) -> None:
    result = _result(manifest, commit)
    try:
        ensure_operation_event(
            root,
            operation_id,
            "mesh.worker_completed",
            {
                "run_id": manifest["run_id"],
                "worker_result_fingerprint": manifest["worker_result_fingerprint"],
            },
        )
        ensure_operation_event(
            root,
            operation_id,
            "mesh.quality_checked",
            {
                "run_id": manifest["run_id"],
                "status": manifest["status"],
                "quality_report_fingerprint": manifest[
                    "quality_report_fingerprint"
                ],
            },
        )
        ensure_operation_event(
            root,
            operation_id,
            "mesh.run_completed",
            result,
        )
        complete_operation(root, operation_id, result)
    except ModelMatchingError as exc:
        raise _error(
            "publication_recovery_required",
            "网格运行已提交，但审计完成仍需同一操作恢复。",
        ) from exc


def create_mesh_run(
    project_root: Path,
    *,
    run_id: str,
    source_kind: str,
    source_ref: dict,
    config_id: str,
    policy_id: str,
    worker: MeshWorker,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    """创建或以相同所有者确定性恢复一个网格运行。"""

    try:
        run_id = validate_identifier(run_id, "mesh_run_id")
        config_id = validate_identifier(config_id, "mesh_config_id")
        policy_id = validate_identifier(policy_id, "mesh_policy_id")
        if type(source_kind) is not str or type(source_ref) is not dict:
            raise ValueError("source identity")
        source_ref = json.loads(
            json.dumps(source_ref, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise _error("mesh_run_invalid", "网格运行请求无效。") from exc
    root = Path(project_root)
    request_payload = {
        "run_id": run_id,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "config_id": config_id,
        "policy_id": policy_id,
    }
    try:
        operation, replayed = start_operation(
            root,
            operation_id=operation_id,
            operation_type="mesh.run.create",
            principal=principal,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    except ModelMatchingError as exc:
        raise _as_scan_error(exc) from exc
    if replayed and operation["status"] == "completed":
        return load_mesh_run(root, run_id)
    if replayed and operation["status"] == "failed":
        failure = operation.get("error") or {}
        raise _error(
            failure.get("code", "mesh_run_failed"),
            failure.get("message", "网格运行失败。"),
        )
    try:
        require_any_role(principal, {"expert"})
    except ModelMatchingError as exc:
        error = _error("permission_denied", "只有专家可以创建网格运行。")
        _fail_running(root, operation_id, error)
        raise error from exc
    expected_owner = _expected_owner(
        operation,
        run_id=run_id,
        source_kind=source_kind,
        source_ref=source_ref,
        config_id=config_id,
        policy_id=policy_id,
    )
    try:
        with model_resource_lock(root, "mesh-run", run_id):
            directory = _prepare_directory(root, run_id)
            owner = _claim_owner(directory, expected_owner)
            source = _load_or_freeze(
                directory / "source_snapshot.json",
                lambda: freeze_mesh_source(
                    root, source_kind=source_kind, source_ref=source_ref
                ),
                lambda value: _validate_source(value, source_kind, source_ref),
            )
            config = _load_or_freeze(
                directory / "config_snapshot.json",
                lambda: load_mesh_config(root, config_id),
                lambda value: _validate_config(value, config_id),
            )
            policy = _load_or_freeze(
                directory / "policy_snapshot.json",
                lambda: load_mesh_quality_policy(root, policy_id),
                lambda value: _validate_policy(value, policy_id),
            )
            request = prepare_mesh_worker_request(
                directory, source_snapshot=source, config=config
            )
            try:
                ensure_operation_event(
                    root,
                    operation_id,
                    "mesh.run_started",
                    {
                        "run_id": run_id,
                        "source_snapshot_fingerprint": source["fingerprint"],
                        "config_fingerprint": config["config_fingerprint"],
                        "policy_fingerprint": policy["policy_fingerprint"],
                        "worker_request_fingerprint": request[
                            "request_fingerprint"
                        ],
                    },
                )
            except ModelMatchingError as exc:
                raise _error(
                    "publication_recovery_required",
                    "运行已冻结，但开始审计仍需同一操作恢复。",
                ) from exc
            result = _execute_worker(
                directory, request, worker, operation_id, root
            )
            metrics = _read_metrics(
                directory,
                result,
                request["limits"]["maximum_artifact_bytes"],
            )
            quality = _quality_report(
                metrics, policy, result["metrics_fingerprint"]
            )
            _publish(directory / "quality_report.json", quality)
            manifest = _manifest(
                run_id=run_id,
                owner=owner,
                source=source,
                config=config,
                policy=policy,
                request=request,
                result=result,
                quality=quality,
            )
            commit = _commit(manifest)
            _publish(directory / "run_manifest.json", manifest)
            _publish(directory / "commit.json", commit)
            _finalize_audit(root, operation_id, manifest, commit)
            return _load_bundle(directory, validate_audit=True, root=root)
    except ScanToMeshError as error:
        if error.code not in {
            "operation_busy",
            "publication_recovery_required",
        }:
            _fail_running(root, operation_id, error)
        raise
    except ModelMatchingError as exc:
        error = _as_scan_error(exc)
        if error.code not in {
            "operation_busy",
            "publication_recovery_required",
        }:
            _fail_running(root, operation_id, error)
        raise error from exc


def load_mesh_run(project_root: Path, run_id: str) -> dict:
    try:
        run_id = validate_identifier(run_id, "mesh_run_id")
    except (TypeError, ValueError) as exc:
        raise _error("mesh_run_invalid", "网格运行 ID 无效。") from exc
    root = Path(project_root)
    try:
        with model_resource_lock(root, "mesh-run", run_id):
            directory = _run_directory(root, run_id)
            _require_plain(directory, directory=True)
            return _load_bundle(directory, validate_audit=True, root=root)
    except FileNotFoundError as exc:
        raise _error("mesh_run_not_found", "已提交网格运行不存在。") from exc
    except ScanToMeshError:
        raise
    except ModelMatchingError as exc:
        raise _as_scan_error(exc) from exc
