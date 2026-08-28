import hashlib
import json
import math
import os
import stat
import uuid
from pathlib import Path

from pc_system.model_import import import_model_version
from pc_system.model_library import create_model_asset, model_asset_path, model_version_dir
from pc_system.model_matching_audit import fail_operation, start_operation
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal
from pc_system.model_mesh import trimesh_mesh_reader
from pc_system.model_release import list_model_releases, release_model_version


_MAX_PROVENANCE_BYTES = 1024 * 1024


def _invalid_provenance() -> ModelMatchingError:
    return ModelMatchingError(
        "invalid_model_provenance", "Model provenance must be a safe JSON object."
    )


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _load_provenance(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        before = path.lstat()
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or bool(getattr(before, "st_file_attributes", 0) & reparse_point)
            or before.st_size > _MAX_PROVENANCE_BYTES
        ):
            raise ValueError("unsafe provenance file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > _MAX_PROVENANCE_BYTES
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise ValueError("unsafe provenance file")
            chunks: list[bytes] = []
            remaining = _MAX_PROVENANCE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > _MAX_PROVENANCE_BYTES:
                raise ValueError("provenance is too large")
        finally:
            os.close(descriptor)
        decoded = payload.decode("utf-8")
        value = json.loads(
            decoded,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
            object_pairs_hook=_json_object,
        )
        if type(value) is not dict:
            raise ValueError("provenance is not an object")
        return value
    except (OSError, RecursionError, UnicodeError, ValueError) as exc:
        raise _invalid_provenance() from exc


def _attempt_value(value: object) -> bytes:
    if type(value) is str:
        return value.encode("utf-8", errors="surrogatepass")
    if value is None:
        return b"<none>"
    if isinstance(value, os.PathLike):
        raw = os.fspath(value)
        if type(raw) is str:
            return raw.encode("utf-8", errors="surrogatepass")
    return b"<invalid>"


def _provenance_attempt_fingerprint(**values: object) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(values.items()):
        digest.update(name.encode("ascii"))
        digest.update(b"\0")
        digest.update(_attempt_value(value))
        digest.update(b"\0")
    return digest.hexdigest()


def _record_provenance_rejection(
    project_root: Path,
    *,
    principal: Principal,
    model_id: str,
    version_id: str,
    source_path: Path,
    declared_unit: str,
    license_name: str,
    provenance_path: Path | None,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> None:
    try:
        nonce = uuid.uuid4().hex
        generated_operation_id = f"p15-provenance-{nonce}"
        generated_request_id = f"p15-provenance-request-{nonce}"
        generated_idempotency_key = f"p15-provenance-idem-{nonce}"
        request_payload = {
            "command": "import-model",
            "route": "cli",
            "rejected_before_domain": True,
            "attempt_fingerprint": _provenance_attempt_fingerprint(
                actor=principal.actor_id,
                declared_unit=declared_unit,
                idempotency_key=idempotency_key,
                license_name=license_name,
                model_id=model_id,
                operation_id=operation_id,
                provenance_path=provenance_path,
                request_id=request_id,
                source_path=source_path,
                version_id=version_id,
            ),
        }
        _operation, replayed = start_operation(
            project_root,
            operation_id=generated_operation_id,
            operation_type="model_version.import",
            principal=principal,
            request_id=generated_request_id,
            idempotency_key=generated_idempotency_key,
            request_payload=request_payload,
        )
        if replayed:
            raise ModelMatchingError(
                "audit_persistence_error",
                "Provenance rejection audit operation unexpectedly replayed.",
            )
        fail_operation(
            project_root,
            generated_operation_id,
            "invalid_model_provenance",
            "Model provenance must be a safe JSON object.",
        )
    except Exception as exc:
        raise ModelMatchingError(
            "audit_persistence_error",
            "Provenance rejection audit could not be persisted.",
        ) from exc


def run_create_model_asset(
    project_root: Path,
    *,
    model_id: str,
    display_name: str,
    category_id: str,
    manufacturer: str,
    model_number: str,
    keywords: list[str],
    tags: list[str],
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> int:
    principal = Principal(actor, frozenset({"expert"}), "cli")
    asset = create_model_asset(
        project_root,
        model_id=model_id,
        display_name=display_name,
        category_id=category_id,
        manufacturer=manufacturer,
        model_number=model_number,
        keywords=list(keywords),
        tags=list(tags),
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    print(model_asset_path(project_root, asset["model_id"]))
    return 0


def run_import_model(
    project_root: Path,
    *,
    model_id: str,
    version_id: str,
    source_path: Path,
    declared_unit: str,
    license_name: str,
    provenance_path: Path | None,
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> int:
    principal = Principal(actor, frozenset({"expert"}), "cli")
    try:
        provenance = _load_provenance(provenance_path)
    except ModelMatchingError:
        _record_provenance_rejection(
            project_root,
            principal=principal,
            model_id=model_id,
            version_id=version_id,
            source_path=source_path,
            declared_unit=declared_unit,
            license_name=license_name,
            provenance_path=provenance_path,
            operation_id=operation_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        raise
    version = import_model_version(
        project_root,
        model_id=model_id,
        version_id=version_id,
        source_path=source_path,
        declared_unit=declared_unit,
        license_name=license_name,
        provenance=provenance,
        principal=principal,
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
        mesh_reader=trimesh_mesh_reader,
    )
    print(
        model_version_dir(project_root, version["model_id"], version["version_id"])
        / "model_manifest.json"
    )
    return 0


def run_release_model_version(
    project_root: Path,
    *,
    model_id: str,
    version_id: str,
    release_id: str,
    action: str,
    expected_current_release_id: str | None,
    rollback_of_release_id: str | None,
    reason: str,
    actor: str,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> int:
    release = release_model_version(
        project_root,
        model_id=model_id,
        version_id=version_id,
        release_id=release_id,
        action=action,
        expected_current_release_id=expected_current_release_id,
        rollback_of_release_id=rollback_of_release_id,
        reason=reason,
        principal=Principal(actor, frozenset({"expert"}), "cli"),
        operation_id=operation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    print(json.dumps(release, ensure_ascii=False, sort_keys=True))
    return 0


def run_list_model_releases(project_root: Path, *, model_id: str) -> int:
    releases = list_model_releases(project_root, model_id)
    print(json.dumps(releases, ensure_ascii=False, sort_keys=True))
    return 0
