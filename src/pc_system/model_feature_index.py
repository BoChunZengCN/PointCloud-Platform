import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterator
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_feature_store import publish_model_feature
from pc_system.model_library import list_model_assets, load_model_asset, model_asset_path
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    load_operation,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_mesh import trimesh_mesh_reader
from pc_system.model_release import (
    _record_failure,
    _require_plain,
    list_model_releases,
    load_current_model_release,
)
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_retrieval_config import load_retrieval_config
from pc_system.model_sampling import (
    _file_fingerprint,
    _publish_exact_json,
    list_sampled_representations,
    sample_model_version,
)


_MAX_LINE_BYTES = 64 * 1024
_MAX_ENTRY_COUNT = 100_000
_MAX_INDEX_BYTES = 1024 * 1024 * 1024
_FATAL_CHILD_CODES = {
    "audit_persistence_error",
    "audit_integrity_error",
    "operation_busy",
    "publication_recovery_required",
}
_TOKEN_SPLIT = re.compile(r"[^\w]+", flags=re.UNICODE)


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
            "model_index_integrity_error", "Index data is not canonical JSON."
        ) from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _identifier(value: object, label: str, *, invalid_code: str = "feature_config_invalid") -> str:
    try:
        return validate_identifier(value, label)
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(invalid_code, "Model index identity is invalid.") from exc


def _child_identity(prefix: str, *values: str) -> tuple[str, str, str]:
    digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:32]
    return (
        f"op-{prefix}-{digest}",
        f"req-{prefix}-{digest}",
        f"idem-{prefix}-{digest}",
    )


def _release_fingerprint(release: dict) -> str:
    return _fingerprint(release)


def _tokenize(asset: dict) -> list[str]:
    values = [
        asset.get("display_name", ""),
        asset.get("manufacturer", ""),
        asset.get("model_number", ""),
        asset.get("category_id", ""),
    ]
    values.extend(asset.get("keywords", []))
    values.extend(asset.get("tags", []))
    tokens: set[str] = set()
    for raw in values:
        if type(raw) is not str:
            continue
        normalized = unicodedata.normalize("NFKC", raw).casefold().strip()
        if not normalized:
            continue
        tokens.add(normalized)
        tokens.update(part for part in _TOKEN_SPLIT.split(normalized) if part)
    return sorted(tokens)


def _matching_config(config: dict) -> dict:
    feature = config["feature_config"]
    return {
        "schema_version": "1.0",
        "algorithm": feature["sampling"]["algorithm"],
        "point_count": feature["sampling"]["point_count"],
        "random_seed": feature["sampling"]["random_seed"],
        "coordinate_unit": "m",
        "coordinate_precision_decimals": 12,
    }


def _select_representation(
    root: Path,
    *,
    model_id: str,
    version_id: str,
    config: dict,
    principal: Principal,
    parent_operation_id: str,
    mesh_reader,
) -> tuple[dict, str]:
    expected = _matching_config(config)
    matching = [
        item
        for item in list_sampled_representations(root, model_id, version_id)
        if item["generation_config"] == expected
    ]
    if len(matching) > 1:
        identities = {item["representation_id"] for item in matching}
        if len(identities) > 1:
            raise ModelMatchingError(
                "model_index_integrity_error", "Matching representations are ambiguous."
            )
    child = _child_identity(
        "auto-sample",
        parent_operation_id,
        model_id,
        version_id,
        config["config_fingerprint"],
    )
    if matching:
        return matching[0], matching[0]["operation_id"]
    representation = sample_model_version(
        root,
        model_id=model_id,
        version_id=version_id,
        point_count=expected["point_count"],
        random_seed=expected["random_seed"],
        principal=principal,
        operation_id=child[0],
        request_id=child[1],
        idempotency_key=child[2],
        mesh_reader=mesh_reader,
    )
    return representation, child[0]


def _build_entry(
    root: Path,
    *,
    asset: dict,
    release: dict,
    source_mode: str,
    config: dict,
    principal: Principal,
    parent_operation_id: str,
    mesh_reader,
) -> dict:
    model_id = asset["model_id"]
    version_id = release["version_id"]
    representation, sampling_operation_id = _select_representation(
        root,
        model_id=model_id,
        version_id=version_id,
        config=config,
        principal=principal,
        parent_operation_id=parent_operation_id,
        mesh_reader=mesh_reader,
    )
    feature_child = _child_identity(
        "auto-feature",
        parent_operation_id,
        model_id,
        version_id,
        config["config_fingerprint"],
    )
    feature = publish_model_feature(
        root,
        model_id=model_id,
        version_id=version_id,
        representation_id=representation["representation_id"],
        config_id=config["config_id"],
        principal=principal,
        operation_id=feature_child[0],
        request_id=feature_child[1],
        idempotency_key=feature_child[2],
    )
    representation_path = (
        root
        / "models"
        / model_id
        / "representations"
        / version_id
        / "cad_sampled"
        / representation["representation_id"]
        / "representation.json"
    )
    return {
        "schema_version": "1.0",
        "category_id": asset["category_id"],
        "model_id": model_id,
        "version_id": version_id,
        "release_id": release["release_id"],
        "source_mode": source_mode,
        "model_asset_fingerprint": _file_fingerprint(model_asset_path(root, model_id)),
        "release_fingerprint": _release_fingerprint(release),
        "representation_id": representation["representation_id"],
        "representation_fingerprint": _file_fingerprint(representation_path),
        "sampling_operation_id": sampling_operation_id,
        "feature_id": feature["feature_id"],
        "feature_vector_fingerprint": feature["feature_vector_fingerprint"],
        "feature_operation_id": feature["operation_id"],
        "features": feature["features"],
        "display_name": asset["display_name"],
        "manufacturer": asset["manufacturer"],
        "model_number": asset["model_number"],
        "terms": _tokenize(asset),
    }


def _production_sources(root: Path) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    sources = []
    heads = []
    missing = []
    for asset in list_model_assets(root):
        release = load_current_model_release(root, asset["model_id"])
        if release is None:
            missing.append({"model_id": asset["model_id"], "code": "model_release_missing"})
            continue
        sources.append((asset, release))
        heads.append(
            {
                "model_id": asset["model_id"],
                "release_id": release["release_id"],
                "version_id": release["version_id"],
                "release_fingerprint": _release_fingerprint(release),
            }
        )
    return sources, heads, missing


def _challenger_sources(root: Path, values: object) -> list[tuple[dict, dict]]:
    if type(values) is not list or not values:
        raise ModelMatchingError(
            "feature_config_invalid", "Challenger releases must be explicit."
        )
    result = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        if type(value) is not dict or set(value) != {"model_id", "release_id"}:
            raise ModelMatchingError(
                "feature_config_invalid", "Challenger release identity is invalid."
            )
        model_id = _identifier(value["model_id"], "model_id")
        release_id = _identifier(value["release_id"], "release_id")
        identity = (model_id, release_id)
        if identity in seen:
            raise ModelMatchingError(
                "feature_config_invalid", "Challenger release is duplicated."
            )
        seen.add(identity)
        releases = [item for item in list_model_releases(root, model_id) if item["release_id"] == release_id]
        if len(releases) != 1:
            raise ModelMatchingError(
                "feature_not_found", "Challenger model release does not exist."
            )
        result.append((load_model_asset(root, model_id), releases[0]))
    return sorted(result, key=lambda item: (item[0]["model_id"], item[1]["release_id"]))


def _entries_bytes(entries: list[dict]) -> bytes:
    return b"".join(_canonical_bytes(entry) + b"\n" for entry in entries)


def _publish_exact_bytes(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".i-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            _require_plain(path, directory=False)
            if path.read_bytes() != payload:
                raise ModelMatchingError(
                    "model_index_integrity_error", "Index entries conflict."
                )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _index_root(root: Path, index_id: str) -> Path:
    return root / "models" / "feature_indexes" / index_id


def _result(manifest: dict) -> dict:
    return {
        "index_id": manifest["index_id"],
        "entries_fingerprint": manifest["entries_fingerprint"],
    }


def _validate_audit(root: Path, manifest: dict, owner: dict) -> None:
    try:
        snapshot = read_verified_operation_snapshot(root, manifest["operation_id"])
        operation = snapshot["operation"]
        events = snapshot["events"]
        published = [event for event in events if event["event_type"] == "model_feature_index.published"]
        if (
            operation["operation_type"] != "model_feature_index.build"
            or operation["status"] != "completed"
            or operation.get("result") != _result(manifest)
            or operation["request_id"] != owner["request_id"]
            or operation["request_fingerprint"] != owner["request_fingerprint"]
            or events[0]["actor_id"] != manifest["generated_by"]
            or events[0]["timestamp"] != manifest["generated_at"]
            or len(published) != 1
            or published[0]["details"] != _result(manifest)
        ):
            raise ValueError("index audit differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_index_integrity_error", "Index audit evidence is invalid."
        ) from exc


def _read_json(path: Path) -> dict:
    try:
        _require_plain(path, directory=False)
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("index artifact too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, RecursionError, UnicodeError, ValueError, ModelMatchingError) as exc:
        raise ModelMatchingError(
            "model_index_integrity_error", "Index artifact could not be read."
        ) from exc
    if type(value) is not dict:
        raise ModelMatchingError("model_index_integrity_error", "Index artifact is invalid.")
    return value


def load_model_feature_index(
    project_root: Path, index_id: str, *, require_current_heads: bool
) -> dict:
    root = Path(project_root)
    index_id = _identifier(index_id, "index_id", invalid_code="model_index_integrity_error")
    directory = _index_root(root, index_id)
    try:
        _require_plain(directory, directory=True)
    except FileNotFoundError as exc:
        raise ModelMatchingError("feature_not_found", "Model feature index does not exist.") from exc
    owner = _read_json(directory / "operation_owner.json")
    exclusions = _read_json(directory / "exclusions.json")
    coverage = _read_json(directory / "coverage.json")
    manifest = _read_json(directory / "index_manifest.json")
    try:
        if (
            manifest["schema_version"] != "1.0"
            or manifest["index_id"] != index_id
            or manifest["index_mode"] not in {"production", "challenger"}
            or manifest["status"] != "ready"
            or manifest["exclusions"] != exclusions["exclusions"]
            or manifest["coverage"] != coverage
            or manifest["entries_fingerprint"] != _file_fingerprint(directory / "entries.jsonl")
            or manifest["entry_count"] > _MAX_ENTRY_COUNT
        ):
            raise ValueError("index manifest differs")
        config = load_retrieval_config(root, manifest["config_id"])
        if config["config_fingerprint"] != manifest["config_fingerprint"]:
            raise ValueError("index config differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_index_integrity_error", "Model feature index evidence differs."
        ) from exc
    _validate_audit(root, manifest, owner)
    if require_current_heads and manifest["index_mode"] == "production":
        _sources, current, _missing = _production_sources(root)
        if current != manifest["current_heads"]:
            raise ModelMatchingError("model_index_stale", "Current model heads changed.")
    return json.loads(json.dumps(manifest, ensure_ascii=False))


def read_index_entries(project_root: Path, index_id: str) -> Iterator[dict]:
    root = Path(project_root)
    manifest = load_model_feature_index(root, index_id, require_current_heads=False)
    path = _index_root(root, manifest["index_id"]) / "entries.jsonl"
    try:
        _require_plain(path, directory=False)
        if path.stat().st_size > _MAX_INDEX_BYTES:
            raise ValueError("index exceeds size limit")
        entries = []
        with path.open("rb") as handle:
            for raw in handle:
                if len(raw) > _MAX_LINE_BYTES or not raw.endswith(b"\n"):
                    raise ValueError("index line is invalid")
                if len(entries) >= _MAX_ENTRY_COUNT:
                    raise ValueError("index has too many rows")
                value = json.loads(raw)
                if type(value) is not dict or _canonical_bytes(value) + b"\n" != raw:
                    raise ValueError("index row is not canonical")
                entries.append(value)
        keys = [(item["category_id"], item["model_id"], item["version_id"]) for item in entries]
        if keys != sorted(keys) or len(entries) != manifest["entry_count"]:
            raise ValueError("index rows are not canonical")
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_index_integrity_error", "Model feature index entries are invalid."
        ) from exc
    yield from entries


def list_model_feature_indexes(project_root: Path) -> list[dict]:
    root = Path(project_root) / "models" / "feature_indexes"
    try:
        candidates = sorted(root.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        return []
    result = []
    for candidate in candidates:
        if (candidate / "index_manifest.json").is_file():
            result.append(
                load_model_feature_index(project_root, candidate.name, require_current_heads=False)
            )
    return result


def build_model_feature_index(
    project_root: Path,
    *,
    index_id: str,
    index_mode: str,
    config_id: str,
    historical_releases: list[dict] | None,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
    mesh_reader=trimesh_mesh_reader,
) -> dict:
    root = Path(project_root)
    index_id = _identifier(index_id, "index_id")
    if index_mode not in {"production", "challenger"}:
        raise ModelMatchingError("feature_config_invalid", "Index mode is invalid.")
    if index_mode == "production" and historical_releases is not None:
        raise ModelMatchingError(
            "feature_config_invalid", "Production index cannot select history."
        )
    config = load_retrieval_config(root, config_id)
    require_any_role(principal, {"expert"})
    request_payload = {
        "index_id": index_id,
        "index_mode": index_mode,
        "config_fingerprint": config["config_fingerprint"],
        "historical_releases": historical_releases,
    }
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type="model_feature_index.build",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(error.get("code", "model_index_integrity_error"), error.get("message", "Index build failed."))
    try:
        if replayed and operation["status"] == "completed":
            return load_model_feature_index(root, index_id, require_current_heads=False)
        if index_mode == "production":
            sources, current_heads, missing = _production_sources(root)
        else:
            sources = _challenger_sources(root, historical_releases)
            current_heads, missing = [], []
        entries = []
        exclusions = list(missing)
        for asset, release in sources:
            sampling_child = _child_identity(
                "auto-sample",
                operation_id,
                asset["model_id"],
                release["version_id"],
                config["config_fingerprint"],
            )[0]
            try:
                entries.append(
                    _build_entry(
                        root,
                        asset=asset,
                        release=release,
                        source_mode=index_mode,
                        config=config,
                        principal=principal,
                        parent_operation_id=operation_id,
                        mesh_reader=mesh_reader,
                    )
                )
            except ModelMatchingError as exc:
                if exc.code in _FATAL_CHILD_CODES:
                    raise
                exclusions.append(
                    {
                        "model_id": asset["model_id"],
                        "version_id": release["version_id"],
                        "code": exc.code,
                        "child_operation_id": sampling_child,
                    }
                )
            except Exception:
                exclusions.append(
                    {
                        "model_id": asset["model_id"],
                        "version_id": release["version_id"],
                        "code": "feature_integrity_error",
                        "child_operation_id": sampling_child,
                    }
                )
        entries.sort(key=lambda item: (item["category_id"], item["model_id"], item["version_id"]))
        exclusions.sort(key=lambda item: (item["model_id"], item.get("version_id", ""), item["code"]))
        eligible = len(sources)
        indexed = len(entries)
        coverage_value = 1.0 if eligible == 0 else round(indexed / eligible, 12)
        coverage = {
            "schema_version": "1.0",
            "eligible_count": eligible,
            "indexed_count": indexed,
            "excluded_count": len(exclusions),
            "missing_release_count": len(missing),
            "coverage": coverage_value,
        }
        entries_payload = _entries_bytes(entries)
        snapshot = read_verified_operation_snapshot(root, operation_id)
        started = snapshot["events"][0]
        manifest = {
            "schema_version": "1.0",
            "index_id": index_id,
            "index_mode": index_mode,
            "config_id": config["config_id"],
            "config_fingerprint": config["config_fingerprint"],
            "entry_count": len(entries),
            "entries_fingerprint": hashlib.sha256(entries_payload).hexdigest(),
            "exclusions": exclusions,
            "coverage": coverage,
            "current_heads": current_heads,
            "operation_id": operation_id,
            "generated_by": started["actor_id"],
            "generated_at": started["timestamp"],
            "status": "ready",
        }
        owner = {
            "schema_version": "1.0",
            "index_id": index_id,
            "operation_id": operation_id,
            "request_id": operation["request_id"],
            "request_fingerprint": operation["request_fingerprint"],
        }
        directory = _index_root(root, index_id)
        with model_resource_lock(root, "feature-index", index_id):
            directory.mkdir(parents=True, exist_ok=True)
            if (directory / "index_manifest.json").is_file():
                visible = load_model_feature_index(root, index_id, require_current_heads=False)
                if visible["entries_fingerprint"] != manifest["entries_fingerprint"]:
                    raise ModelMatchingError("model_index_integrity_error", "Index identity conflicts.")
                ensure_operation_event(
                    root,
                    operation_id,
                    "model_feature_index.reused",
                    {**_result(visible), "producer_operation_id": visible["operation_id"]},
                )
                complete_operation(root, operation_id, _result(visible))
                return visible
            _publish_exact_json(directory / "operation_owner.json", owner, conflict_code="operation_busy", conflict_message="Index owner conflicts.")
            _publish_exact_bytes(directory / "entries.jsonl", entries_payload)
            _publish_exact_json(directory / "exclusions.json", {"exclusions": exclusions}, conflict_code="model_index_integrity_error", conflict_message="Index exclusions conflict.")
            _publish_exact_json(directory / "coverage.json", coverage, conflict_code="model_index_integrity_error", conflict_message="Index coverage conflicts.")
            _publish_exact_json(directory / "index_manifest.json", manifest, conflict_code="model_index_integrity_error", conflict_message="Index manifest conflicts.")
            ensure_operation_event(root, operation_id, "model_feature_index.published", _result(manifest))
            complete_operation(root, operation_id, _result(manifest))
            return load_model_feature_index(root, index_id, require_current_heads=False)
    except Exception as exc:
        error = exc if isinstance(exc, ModelMatchingError) else ModelMatchingError(
            "model_index_integrity_error", "Model feature index build failed."
        )
        current = load_operation(root, operation_id)
        if current["status"] == "running" and error.code not in {"operation_busy", "publication_recovery_required"}:
            _record_failure(root, operation_id, error)
        if error is exc:
            raise
        raise error from exc
