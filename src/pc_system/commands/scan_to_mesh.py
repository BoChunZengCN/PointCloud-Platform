"""点云转网格 CLI 的薄适配层。"""

import json
import math
import os
import stat
from pathlib import Path

from pc_system.mesh_reconstruction import create_mesh_run
from pc_system.mesh_reconstruction_config import (
    publish_mesh_config,
    publish_mesh_quality_policy,
)
from pc_system.mesh_release import decide_mesh_run, release_mesh_run
from pc_system.model_matching_identity import Principal
from pc_system.scan_to_mesh_catalog import (
    list_mesh_release_catalog,
    list_mesh_run_catalog,
    load_mesh_run_catalog,
)
from pc_system.scan_to_mesh_errors import ScanToMeshError


_MAXIMUM_JSON_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400


def _principal(actor: str, role: str = "expert") -> Principal:
    return Principal(actor, frozenset({role}), "cli")


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _load_json(path: Path, *, code: str, label: str) -> dict:
    descriptor = None
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or bool(getattr(before, "st_file_attributes", 0) & _REPARSE_POINT)
            or before.st_size > _MAXIMUM_JSON_BYTES
        ):
            raise ValueError("unsafe JSON input")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > _MAXIMUM_JSON_BYTES
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError("JSON input changed")
        chunks = []
        remaining = _MAXIMUM_JSON_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAXIMUM_JSON_BYTES:
            raise ValueError("JSON input too large")
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            parse_float=_finite_float,
            object_pairs_hook=_json_object,
        )
        if type(value) is not dict:
            raise ValueError("JSON input is not an object")
        return value
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ScanToMeshError(code, f"{label}必须是安全且字段唯一的 JSON 对象。") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _print(value: dict) -> int:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


def run_publish_mesh_config(
    project_root: Path,
    *,
    config_id: str,
    config_path: Path,
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> int:
    result = publish_mesh_config(
        project_root,
        config_id=config_id,
        config=_load_json(config_path, code="mesh_config_invalid", label="网格配置"),
        principal=_principal(actor),
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    return _print(result)


def run_publish_mesh_policy(
    project_root: Path,
    *,
    policy_id: str,
    policy_path: Path,
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> int:
    result = publish_mesh_quality_policy(
        project_root,
        policy_id=policy_id,
        policy=_load_json(
            policy_path, code="mesh_policy_invalid", label="网格质量策略"
        ),
        principal=_principal(actor),
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    return _print(result)


def _source_ref(
    *,
    source_kind: str,
    asset_id: str | None,
    source_release_id: str | None,
    instance_id: str | None,
    model_id: str | None,
    version_id: str | None,
) -> dict:
    if source_kind == "phase14_correction_release" and all(
        type(value) is str and value
        for value in (asset_id, source_release_id, instance_id)
    ) and model_id is None and version_id is None:
        return {
            "asset_id": asset_id,
            "release_id": source_release_id,
            "instance_id": instance_id,
        }
    if source_kind == "phase15e_scanned_reference" and all(
        type(value) is str and value for value in (model_id, version_id)
    ) and asset_id is None and source_release_id is None and instance_id is None:
        return {"model_id": model_id, "version_id": version_id}
    raise ScanToMeshError("mesh_run_invalid", "点云来源身份与来源类型不匹配。")


def run_create_mesh_run(
    project_root: Path,
    *,
    run_id: str,
    source_kind: str,
    asset_id: str | None,
    source_release_id: str | None,
    instance_id: str | None,
    model_id: str | None,
    version_id: str | None,
    config_id: str,
    policy_id: str,
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
    worker,
    cursor_secret: bytes | None,
) -> int:
    if worker is None:
        raise ScanToMeshError("mesh_worker_unavailable", "未配置网格重建工作器。")
    principal = _principal(actor)
    create_mesh_run(
        project_root,
        run_id=run_id,
        source_kind=source_kind,
        source_ref=_source_ref(
            source_kind=source_kind,
            asset_id=asset_id,
            source_release_id=source_release_id,
            instance_id=instance_id,
            model_id=model_id,
            version_id=version_id,
        ),
        config_id=config_id,
        policy_id=policy_id,
        worker=worker,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    return _print(load_mesh_run_catalog(project_root, run_id, principal=principal))


def run_list_mesh_runs(
    project_root: Path,
    *,
    actor: str,
    role: str,
    status: str | None,
    cursor: str | None,
    limit: int,
    cursor_secret: bytes | None,
) -> int:
    return _print(
        list_mesh_run_catalog(
            project_root,
            principal=_principal(actor, role),
            status=status,
            cursor=cursor,
            limit=limit,
            cursor_secret=cursor_secret,
        )
    )


def run_show_mesh_run(
    project_root: Path, *, run_id: str, actor: str, role: str
) -> int:
    return _print(
        load_mesh_run_catalog(
            project_root, run_id, principal=_principal(actor, role)
        )
    )


def run_decide_mesh_run(
    project_root: Path,
    *,
    run_id: str,
    decision: str,
    reason: str,
    acknowledgements: list[str],
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> int:
    principal = _principal(actor)
    decide_mesh_run(
        project_root,
        run_id=run_id,
        decision=decision,
        reason=reason,
        acknowledgements=acknowledgements,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    return _print(load_mesh_run_catalog(project_root, run_id, principal=principal))


def run_release_mesh(
    project_root: Path,
    *,
    object_key: str,
    run_id: str,
    release_id: str,
    action: str,
    reason: str,
    expected_current_release_id: str | None,
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> int:
    principal = _principal(actor)
    release_mesh_run(
        project_root,
        object_key=object_key,
        run_id=run_id,
        release_id=release_id,
        action=action,
        reason=reason,
        expected_current_release_id=expected_current_release_id,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    catalog = list_mesh_release_catalog(
        project_root, object_key, principal=principal
    )
    return _print(next(item for item in catalog["items"] if item["release_id"] == release_id))
