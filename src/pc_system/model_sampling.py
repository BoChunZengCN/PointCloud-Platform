import bisect
import hashlib
import json
import math
import os
import tempfile
from enum import Enum
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_import import fingerprint_file, load_model_version
from pc_system.model_library import model_version_dir
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import (
    _fsync_directory,
    _load_json,
    _record_failure,
    _require_plain,
)
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_mesh import MeshReader, read_mesh_geometry_m

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_mesh import _validated_geometry


_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "algorithm",
        "point_count",
        "random_seed",
        "coordinate_unit",
        "coordinate_precision_decimals",
    }
)


def _sampling_error(message: str) -> ModelMatchingError:
    return ModelMatchingError("invalid_sampling_config", message)


def build_sampling_config(point_count: int, random_seed: int) -> dict:
    if (
        type(point_count) is not int
        or not 1 <= point_count <= 500_000
        or type(random_seed) is not int
        or not 0 <= random_seed <= 9_223_372_036_854_775_807
    ):
        raise _sampling_error("Sampling point count or random seed is invalid.")
    return {
        "schema_version": "1.0",
        "algorithm": "sha256_area_weighted_v1",
        "point_count": point_count,
        "random_seed": random_seed,
        "coordinate_unit": "m",
        "coordinate_precision_decimals": 12,
    }


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_config(config: object) -> dict:
    if type(config) is not dict or set(config) != _CONFIG_FIELDS:
        raise _sampling_error("Sampling configuration structure is invalid.")
    if (
        type(config.get("schema_version")) is not str
        or type(config.get("algorithm")) is not str
        or type(config.get("point_count")) is not int
        or type(config.get("random_seed")) is not int
        or type(config.get("coordinate_unit")) is not str
        or type(config.get("coordinate_precision_decimals")) is not int
    ):
        raise _sampling_error("Sampling configuration types are invalid.")
    canonical = build_sampling_config(
        config.get("point_count"), config.get("random_seed")
    )
    if config != canonical:
        raise _sampling_error("Sampling configuration values are invalid.")
    return canonical


def sampling_config_fingerprint(config: dict) -> str:
    canonical = _validate_config(config)
    return hashlib.sha256(_canonical_bytes(canonical)).hexdigest()


def _uniform(config_fingerprint: str, sample_index: int, lane: int) -> float:
    payload = (
        b"phase15b1"
        + bytes.fromhex(config_fingerprint)
        + sample_index.to_bytes(8, "big")
        + bytes([lane])
    )
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return min(value / 2**64, math.nextafter(1.0, 0.0))


def _triangle_area(a: list[float], b: list[float], c: list[float]) -> float:
    ab = [b[index] - a[index] for index in range(3)]
    ac = [c[index] - a[index] for index in range(3)]
    cross = [
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    ]
    return math.sqrt(sum(value * value for value in cross)) / 2.0


def _triangles(vertices: list[list[float]], faces: list[list[int]]):
    result = []
    cumulative = 0.0
    for face in faces:
        for index in range(1, len(face) - 1):
            triangle = (face[0], face[index], face[index + 1])
            area = _triangle_area(*(vertices[item] for item in triangle))
            if area == 0.0:
                continue
            if not math.isfinite(area):
                raise ModelMatchingError(
                    "invalid_model_geometry", "Mesh triangle area is not finite."
                )
            cumulative += area
            result.append((triangle, cumulative))
    if not result or not math.isfinite(cumulative) or cumulative <= 0.0:
        raise ModelMatchingError(
            "invalid_model_geometry", "Mesh has no non-degenerate surface area."
        )
    return result, cumulative


def _rounded(value: float) -> float:
    if not math.isfinite(value):
        raise ModelMatchingError(
            "invalid_model_geometry", "Sampled coordinate is not finite."
        )
    rounded = round(value, 12)
    if not math.isfinite(rounded):
        raise ModelMatchingError(
            "invalid_model_geometry", "Sampled coordinate is not finite."
        )
    return 0.0 if rounded == 0.0 else rounded


def sample_mesh_surface(vertices_m, faces, config: dict) -> dict:
    canonical = _validate_config(config)
    vertices, normalized_faces = _validated_geometry(
        {"vertices": vertices_m, "faces": faces}
    )
    triangles, total_area = _triangles(vertices, normalized_faces)
    cumulative = [item[1] for item in triangles]
    fingerprint = sampling_config_fingerprint(canonical)
    points = []
    for sample_index in range(canonical["point_count"]):
        target = _uniform(fingerprint, sample_index, 0) * total_area
        triangle_index = bisect.bisect_right(cumulative, target)
        triangle = triangles[min(triangle_index, len(triangles) - 1)][0]
        a, b, c = (vertices[index] for index in triangle)
        root = math.sqrt(_uniform(fingerprint, sample_index, 1))
        v = _uniform(fingerprint, sample_index, 2)
        weights = (1.0 - root, root * (1.0 - v), root * v)
        try:
            point = [
                _rounded(
                    math.fsum(
                        (
                            weights[0] * a[axis],
                            weights[1] * b[axis],
                            weights[2] * c[axis],
                        )
                    )
                )
                for axis in range(3)
            ]
        except (OverflowError, ValueError) as exc:
            raise ModelMatchingError(
                "invalid_model_geometry", "Sampled coordinate is not finite."
            ) from exc
        points.append(point)
    return {
        "schema_version": "1.0",
        "coordinate_unit": "m",
        "point_count": canonical["point_count"],
        "points": points,
    }


_REPRESENTATION_FIELDS = frozenset(
    {
        "schema_version",
        "representation_id",
        "representation_type",
        "model_id",
        "source_version_id",
        "source_manifest_fingerprint",
        "source_geometry_fingerprint",
        "geometry_fingerprint",
        "generation_config",
        "generation_config_fingerprint",
        "point_count",
        "coordinate_unit",
        "artifact_uri",
        "operation_id",
        "generated_by",
        "generated_at",
        "status",
    }
)
_REPRESENTATION_TEXT_FIELDS = _REPRESENTATION_FIELDS - {
    "generation_config",
    "point_count",
}
_POINTS_FIELDS = frozenset(
    {"schema_version", "coordinate_unit", "point_count", "points"}
)
_OWNER_FIELDS = frozenset(
    {
        "schema_version",
        "model_id",
        "version_id",
        "representation_id",
        "operation_id",
        "request_id",
        "request_fingerprint",
    }
)


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_json_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")


def _read_plain_bytes(path: Path) -> bytes:
    _require_plain(path, directory=False)
    return path.read_bytes()


def _file_fingerprint(path: Path) -> str:
    return hashlib.sha256(_read_plain_bytes(path)).hexdigest()


def _publish_sampling_json(path: Path, value: dict) -> bool:
    """Publish without replacement while keeping Windows temporary paths short."""
    payload = _canonical_json_bytes(value)
    temporary_path: Path | None = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=".s-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            return False
        published = True
        _fsync_directory(path.parent)
        return True
    except OSError as exc:
        if published:
            raise ModelMatchingError(
                "publication_recovery_required",
                "Sampling data is visible but durability must be recovered.",
            ) from exc
        raise
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _publish_exact_json(
    path: Path,
    value: dict,
    *,
    conflict_code: str,
    conflict_message: str,
) -> None:
    if _publish_sampling_json(path, value):
        return
    try:
        actual = _read_plain_bytes(path)
    except (ModelMatchingError, OSError) as exc:
        raise ModelMatchingError(conflict_code, conflict_message) from exc
    if actual != _canonical_json_bytes(value):
        raise ModelMatchingError(conflict_code, conflict_message)
    try:
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ModelMatchingError(
            "publication_recovery_required",
            "Sampling data is visible but durability must be recovered.",
        ) from exc


def _representation_root(
    root: Path, model_id: str, version_id: str, representation_id: str
) -> Path:
    return (
        root
        / "models"
        / model_id
        / "representations"
        / version_id
        / "cad_sampled"
        / representation_id
    )


def _representation_result(value: dict) -> dict:
    return {
        "model_id": value["model_id"],
        "version_id": value["source_version_id"],
        "representation_id": value["representation_id"],
    }


def _source_event_details(value: dict, owner_fingerprint: str) -> dict:
    config = value["generation_config"]
    return {
        "model_id": value["model_id"],
        "version_id": value["source_version_id"],
        "representation_id": value["representation_id"],
        "producer_operation_id": value["operation_id"],
        "owner_fingerprint": owner_fingerprint,
        "source_manifest_fingerprint": value["source_manifest_fingerprint"],
        "source_geometry_fingerprint": value["source_geometry_fingerprint"],
        "generation_config_fingerprint": value["generation_config_fingerprint"],
        "random_seed": config["random_seed"],
        "point_count": value["point_count"],
    }


def _points_event_details(value: dict, owner_fingerprint: str) -> dict:
    return {
        **_source_event_details(value, owner_fingerprint),
        "geometry_fingerprint": value["geometry_fingerprint"],
    }


def _published_event_details(
    value: dict, owner_fingerprint: str, representation_fingerprint: str
) -> dict:
    return {
        **_points_event_details(value, owner_fingerprint),
        "representation_fingerprint": representation_fingerprint,
    }


def _expected_request_payload(value: dict) -> dict:
    return {
        "model_id": value["model_id"],
        "version_id": value["source_version_id"],
        "generation_config": value["generation_config"],
        "representation_id": value["representation_id"],
    }


def _record_production_events(
    root: Path,
    operation_id: str,
    representation: dict,
    owner_fingerprint: str,
    representation_fingerprint: str,
) -> dict:
    result = _representation_result(representation)
    ensure_operation_event(
        root,
        operation_id,
        "model_sampling.source_verified",
        _source_event_details(representation, owner_fingerprint),
    )
    ensure_operation_event(
        root,
        operation_id,
        "model_sampling.points_generated",
        _points_event_details(representation, owner_fingerprint),
    )
    ensure_operation_event(
        root,
        operation_id,
        "model_sampling.representation_published",
        _published_event_details(
            representation, owner_fingerprint, representation_fingerprint
        ),
    )
    complete_operation(root, operation_id, result)
    return result


def _reuse_event_details(root: Path, representation: dict) -> dict:
    path = _representation_root(
        root,
        representation["model_id"],
        representation["source_version_id"],
        representation["representation_id"],
    )
    return {
        "model_id": representation["model_id"],
        "version_id": representation["source_version_id"],
        "representation_id": representation["representation_id"],
        "producer_operation_id": representation["operation_id"],
        "owner_fingerprint": _file_fingerprint(
            path / "operation_owner.json"
        ),
        "geometry_fingerprint": representation["geometry_fingerprint"],
        "representation_fingerprint": _file_fingerprint(
            path / "representation.json"
        ),
        "generation_config_fingerprint": representation[
            "generation_config_fingerprint"
        ],
    }


def _record_reuse_event(
    root: Path, operation_id: str, representation: dict
) -> dict:
    result = _representation_result(representation)
    ensure_operation_event(
        root,
        operation_id,
        "model_sampling.representation_reused",
        _reuse_event_details(root, representation),
    )
    complete_operation(root, operation_id, result)
    return result


def _validate_owner(
    value: object,
    *,
    model_id: str,
    version_id: str,
    representation_id: str,
) -> dict:
    if type(value) is not dict or set(value) != _OWNER_FIELDS:
        raise ValueError("invalid owner structure")
    if any(type(value[field]) is not str for field in _OWNER_FIELDS):
        raise ValueError("invalid owner types")
    if (
        value["schema_version"] != "1.0"
        or value["model_id"] != model_id
        or value["version_id"] != version_id
        or value["representation_id"] != representation_id
        or not _is_sha256(value["request_fingerprint"])
    ):
        raise ValueError("invalid owner identity")
    for field in (
        "model_id",
        "version_id",
        "representation_id",
        "operation_id",
        "request_id",
    ):
        if validate_identifier(value[field], field) != value[field]:
            raise ValueError("invalid owner identifier")
    return value


def _read_owner(
    path: Path,
    *,
    model_id: str,
    version_id: str,
    representation_id: str,
) -> dict:
    return _validate_owner(
        _load_json(path),
        model_id=model_id,
        version_id=version_id,
        representation_id=representation_id,
    )


def _load_representation(
    root: Path,
    model_id: str,
    version_id: str,
    representation_id: str,
    *,
    require_audit: bool,
) -> dict:
    path = _representation_root(root, model_id, version_id, representation_id)
    try:
        _require_plain(path, directory=True)
        owner_path = path / "operation_owner.json"
        representation_path = path / "representation.json"
        points_path = path / "sampled_points.json"
        owner = _read_owner(
            owner_path,
            model_id=model_id,
            version_id=version_id,
            representation_id=representation_id,
        )
        value = _load_json(representation_path)
        points = _load_json(points_path)
        owner_fingerprint = _file_fingerprint(owner_path)
        representation_fingerprint = _file_fingerprint(representation_path)
        manifest = load_model_version(root, model_id, version_id)
        if type(value) is not dict or set(value) != _REPRESENTATION_FIELDS:
            raise ValueError("invalid representation structure")
        if any(type(value[field]) is not str for field in _REPRESENTATION_TEXT_FIELDS):
            raise ValueError("invalid representation types")
        if type(value["point_count"]) is not int:
            raise ValueError("invalid representation point count")
        if (
            value["schema_version"] != "1.0"
            or value["representation_id"] != representation_id
            or value["representation_type"] != "cad_sampled"
            or value["model_id"] != model_id
            or value["source_version_id"] != version_id
            or value["artifact_uri"] != "sampled_points.json"
            or value["coordinate_unit"] != "m"
            or value["status"] != "ready"
            or value["operation_id"] != owner["operation_id"]
            or not all(
                _is_sha256(value[field])
                for field in (
                    "source_manifest_fingerprint",
                    "source_geometry_fingerprint",
                    "geometry_fingerprint",
                    "generation_config_fingerprint",
                )
            )
        ):
            raise ValueError("invalid representation identity")
        config = _validate_config(value["generation_config"])
        if (
            _read_plain_bytes(owner_path) != _canonical_json_bytes(owner)
            or _read_plain_bytes(representation_path)
            != _canonical_json_bytes(value)
            or type(points) is not dict
            or set(points) != _POINTS_FIELDS
            or type(points.get("schema_version")) is not str
            or type(points.get("coordinate_unit")) is not str
            or type(points.get("point_count")) is not int
            or points.get("schema_version") != "1.0"
            or points.get("coordinate_unit") != "m"
            or sampling_config_fingerprint(config)
            != value["generation_config_fingerprint"]
            or representation_id
            != f"cad-sampled-{value['generation_config_fingerprint']}"
            or value["point_count"] != config["point_count"]
            or value["source_manifest_fingerprint"]
            != fingerprint_file(
                model_version_dir(root, model_id, version_id)
                / "model_manifest.json"
            )
            or value["source_geometry_fingerprint"]
            != manifest["artifact_fingerprints"]["source_geometry"]
            or value["geometry_fingerprint"]
            != _file_fingerprint(points_path)
            or _read_plain_bytes(points_path) != _canonical_json_bytes(points)
            or points.get("point_count") != value["point_count"]
            or type(points.get("points")) is not list
            or len(points["points"]) != value["point_count"]
            or any(
                type(point) is not list
                or len(point) != 3
                or any(
                    type(item) is not float or not math.isfinite(item)
                    for item in point
                )
                for point in points["points"]
            )
        ):
            raise ValueError("representation evidence differs")
        if require_audit:
            snapshot = read_verified_operation_snapshot(
                root, value["operation_id"]
            )
            operation = snapshot["operation"]
            events = snapshot["events"]
            production_events = [
                event
                for event in events
                if event["event_type"] != "operation.replayed"
            ]
            request_fingerprint = hashlib.sha256(
                _canonical_bytes(_expected_request_payload(value))
            ).hexdigest()
            result = _representation_result(value)
            if (
                operation["operation_type"] != "model_sampling.generate"
                or operation["status"] != "completed"
                or operation.get("result") != result
                or operation["request_id"] != owner["request_id"]
                or operation["request_fingerprint"] != request_fingerprint
                or owner["request_fingerprint"] != request_fingerprint
                or [event["event_type"] for event in production_events]
                != [
                    "operation.started",
                    "model_sampling.source_verified",
                    "model_sampling.points_generated",
                    "model_sampling.representation_published",
                    "operation.completed",
                ]
                or any(
                    event["event_type"] not in {
                        "operation.started",
                        "operation.replayed",
                        "model_sampling.source_verified",
                        "model_sampling.points_generated",
                        "model_sampling.representation_published",
                        "operation.completed",
                    }
                    for event in events
                )
                or production_events[0]["actor_id"] != value["generated_by"]
                or production_events[0]["timestamp"] != value["generated_at"]
                or production_events[0]["details"]
                != {
                    "request_id": owner["request_id"],
                    "request_fingerprint": request_fingerprint,
                }
                or production_events[1]["details"]
                != _source_event_details(value, owner_fingerprint)
                or production_events[2]["details"]
                != _points_event_details(value, owner_fingerprint)
                or production_events[3]["details"]
                != _published_event_details(
                    value, owner_fingerprint, representation_fingerprint
                )
                or production_events[4]["details"] != {"result": result}
            ):
                raise ValueError("representation audit differs")
        return json.loads(json.dumps(value, ensure_ascii=False))
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise ModelMatchingError(
            "model_representation_integrity_error",
            "Sampled representation evidence is invalid.",
        ) from exc
    except (FileNotFoundError, KeyError, TypeError, ValueError, OSError) as exc:
        raise ModelMatchingError(
            "model_representation_integrity_error",
            "Sampled representation evidence is invalid.",
        ) from exc


def load_sampled_representation(
    project_root: Path,
    model_id: str,
    version_id: str,
    representation_id: str,
) -> dict:
    try:
        model_id = validate_identifier(model_id, "model_id")
        version_id = validate_identifier(version_id, "version_id")
        representation_id = validate_identifier(
            representation_id, "representation_id"
        )
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "model_representation_not_found", "Invalid representation identity."
        ) from exc
    return _load_representation(
        Path(project_root),
        model_id,
        version_id,
        representation_id,
        require_audit=True,
    )


def list_sampled_representations(
    project_root: Path, model_id: str, version_id: str
) -> list[dict]:
    root = Path(project_root)
    load_model_version(root, model_id, version_id)
    parent = (
        root
        / "models"
        / model_id
        / "representations"
        / version_id
        / "cad_sampled"
    )
    try:
        candidates = list(parent.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for candidate in sorted(candidates, key=lambda item: item.name):
        if not (candidate / "representation.json").is_file():
            continue
        result.append(
            _load_representation(
                root,
                model_id,
                version_id,
                candidate.name,
                require_audit=True,
            )
        )
    return result


class _CandidateState(Enum):
    ABSENT = "absent"
    OWNED_RECOVERABLE = "owned_recoverable"
    VERIFIED_PUBLISHED = "verified_published"
    UNCERTAIN = "uncertain"


def _classify_candidate(
    root: Path,
    *,
    model_id: str,
    version_id: str,
    representation_id: str,
    expected_owner: dict,
) -> tuple[_CandidateState, dict | None]:
    candidate = _representation_root(
        root, model_id, version_id, representation_id
    )
    try:
        _require_plain(candidate, directory=True)
    except FileNotFoundError:
        return _CandidateState.ABSENT, None
    except (ModelMatchingError, OSError):
        return _CandidateState.UNCERTAIN, None
    owner_path = candidate / "operation_owner.json"
    try:
        actual_owner = _read_owner(
            owner_path,
            model_id=model_id,
            version_id=version_id,
            representation_id=representation_id,
        )
        owner_bytes = _read_plain_bytes(owner_path)
    except (ModelMatchingError, OSError, TypeError, ValueError):
        return _CandidateState.UNCERTAIN, None
    if actual_owner == expected_owner:
        if owner_bytes != _canonical_json_bytes(expected_owner):
            return _CandidateState.UNCERTAIN, None
        return _CandidateState.OWNED_RECOVERABLE, None
    try:
        representation = _load_representation(
            root,
            model_id,
            version_id,
            representation_id,
            require_audit=True,
        )
    except ModelMatchingError:
        return _CandidateState.UNCERTAIN, None
    return _CandidateState.VERIFIED_PUBLISHED, representation


def _build_representation(
    root: Path,
    *,
    model_id: str,
    version_id: str,
    representation_id: str,
    config: dict,
    operation: dict,
    mesh_reader: MeshReader,
) -> tuple[dict, dict]:
    manifest = load_model_version(root, model_id, version_id)
    version_root = model_version_dir(root, model_id, version_id)
    source_path = version_root / manifest["artifacts"]["source"]
    vertices, faces = read_mesh_geometry_m(
        source_path, manifest["declared_unit"], reader=mesh_reader
    )
    points = sample_mesh_surface(vertices, faces, config)
    snapshot = read_verified_operation_snapshot(root, operation["operation_id"])
    start = snapshot["events"][0]
    representation = {
        "schema_version": "1.0",
        "representation_id": representation_id,
        "representation_type": "cad_sampled",
        "model_id": model_id,
        "source_version_id": version_id,
        "source_manifest_fingerprint": _file_fingerprint(
            version_root / "model_manifest.json"
        ),
        "source_geometry_fingerprint": manifest["artifact_fingerprints"][
            "source_geometry"
        ],
        "geometry_fingerprint": hashlib.sha256(
            _canonical_json_bytes(points)
        ).hexdigest(),
        "generation_config": config,
        "generation_config_fingerprint": sampling_config_fingerprint(config),
        "point_count": config["point_count"],
        "coordinate_unit": "m",
        "artifact_uri": "sampled_points.json",
        "operation_id": operation["operation_id"],
        "generated_by": start["actor_id"],
        "generated_at": start["timestamp"],
        "status": "ready",
    }
    return points, representation


def _verify_source_evidence(root: Path, representation: dict) -> None:
    model_id = representation["model_id"]
    version_id = representation["source_version_id"]
    manifest = load_model_version(root, model_id, version_id)
    version_root = model_version_dir(root, model_id, version_id)
    if (
        _file_fingerprint(version_root / "model_manifest.json")
        != representation["source_manifest_fingerprint"]
        or manifest["artifact_fingerprints"]["source_geometry"]
        != representation["source_geometry_fingerprint"]
    ):
        raise ModelMatchingError(
            "model_version_integrity_error",
            "Model source changed during sampling publication.",
        )


def _publish_producer_representation(
    root: Path,
    *,
    state: _CandidateState,
    owner: dict,
    points: dict,
    representation: dict,
) -> dict:
    candidate = _representation_root(
        root,
        representation["model_id"],
        representation["source_version_id"],
        representation["representation_id"],
    )
    if state is _CandidateState.ABSENT:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        _require_plain(candidate.parent, directory=True)
        try:
            candidate.mkdir()
        except FileExistsError as exc:
            raise ModelMatchingError(
                "operation_busy", "Sampling candidate changed during publication."
            ) from exc
        try:
            _fsync_directory(candidate.parent)
        except OSError as exc:
            raise ModelMatchingError(
                "publication_recovery_required",
                "Sampling candidate visibility requires recovery.",
            ) from exc
    elif state is not _CandidateState.OWNED_RECOVERABLE:
        raise ModelMatchingError(
            "operation_busy", "Sampling candidate cannot be produced safely."
        )
    owner_path = candidate / "operation_owner.json"
    points_path = candidate / "sampled_points.json"
    representation_path = candidate / "representation.json"
    _publish_exact_json(
        owner_path,
        owner,
        conflict_code="operation_busy",
        conflict_message="Sampling candidate owner differs.",
    )
    _verify_source_evidence(root, representation)
    _publish_exact_json(
        points_path,
        points,
        conflict_code="model_representation_integrity_error",
        conflict_message="Existing sampled points differ.",
    )
    _publish_exact_json(
        representation_path,
        representation,
        conflict_code="model_representation_integrity_error",
        conflict_message="Existing representation differs.",
    )
    loaded = _load_representation(
        root,
        representation["model_id"],
        representation["source_version_id"],
        representation["representation_id"],
        require_audit=False,
    )
    if loaded != representation:
        raise ModelMatchingError(
            "model_representation_integrity_error",
            "Visible representation differs from the producer evidence.",
        )
    owner_fingerprint = _file_fingerprint(owner_path)
    representation_fingerprint = _file_fingerprint(representation_path)
    _record_production_events(
        root,
        representation["operation_id"],
        representation,
        owner_fingerprint,
        representation_fingerprint,
    )
    return _load_representation(
        root,
        representation["model_id"],
        representation["source_version_id"],
        representation["representation_id"],
        require_audit=True,
    )


def sample_model_version(
    project_root: Path,
    *,
    model_id: str,
    version_id: str,
    point_count: int,
    random_seed: int,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
    mesh_reader: MeshReader,
) -> dict:
    root = Path(project_root)
    config = build_sampling_config(point_count, random_seed)
    config_fingerprint = sampling_config_fingerprint(config)
    representation_id = f"cad-sampled-{config_fingerprint}"
    request_payload = {
        "model_id": model_id,
        "version_id": version_id,
        "generation_config": config,
        "representation_id": representation_id,
    }
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type="model_sampling.generate",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(
            error.get("code", "invalid_sampling_config"),
            error.get("message", "Sampling failed."),
        )
    try:
        require_any_role(principal, {"expert"})
    except ModelMatchingError as error:
        if replayed and operation["status"] != "running":
            raise
        _record_failure(root, operation["operation_id"], error)
        raise
    if replayed and operation["status"] == "completed":
        return _load_representation(
            root,
            model_id,
            version_id,
            representation_id,
            require_audit=True,
        )
    owner = {
        "schema_version": "1.0",
        "model_id": model_id,
        "version_id": version_id,
        "representation_id": representation_id,
        "operation_id": operation["operation_id"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
    }

    def classify_locked() -> tuple[_CandidateState, dict | None]:
        return _classify_candidate(
            root,
            model_id=model_id,
            version_id=version_id,
            representation_id=representation_id,
            expected_owner=owner,
        )

    def raise_publication_error(exc: Exception) -> None:
        if isinstance(exc, ModelMatchingError):
            if exc.code == "audit_persistence_error":
                raise ModelMatchingError(
                    "publication_recovery_required",
                    "Sampling publication recovery is required.",
                ) from exc
            raise exc
        raise ModelMatchingError(
            "model_representation_integrity_error",
            "Sampling publication failed.",
        ) from exc

    try:
        with model_resource_lock(
            root, "sampling", model_id, version_id, representation_id
        ):
            state, existing = classify_locked()
            if state is _CandidateState.VERIFIED_PUBLISHED:
                assert existing is not None
                _record_reuse_event(root, operation["operation_id"], existing)
                return existing
            if state is _CandidateState.UNCERTAIN:
                raise ModelMatchingError(
                    "operation_busy",
                    "Sampling candidate evidence is uncertain.",
                )
    except Exception as exc:
        raise_publication_error(exc)

    try:
        points, representation = _build_representation(
            root,
            model_id=model_id,
            version_id=version_id,
            representation_id=representation_id,
            config=config,
            operation=operation,
            mesh_reader=mesh_reader,
        )
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, ModelMatchingError)
            else ModelMatchingError(
                "model_representation_integrity_error",
                "Sampling preparation failed.",
            )
        )
        if state is _CandidateState.OWNED_RECOVERABLE:
            raise error
        _record_failure(root, operation["operation_id"], error)
        raise error

    try:
        with model_resource_lock(
            root, "sampling", model_id, version_id, representation_id
        ):
            state, existing = classify_locked()
            if state is _CandidateState.VERIFIED_PUBLISHED:
                assert existing is not None
                _record_reuse_event(root, operation["operation_id"], existing)
                return existing
            if state is _CandidateState.UNCERTAIN:
                raise ModelMatchingError(
                    "operation_busy",
                    "Sampling candidate evidence is uncertain.",
                )
            return _publish_producer_representation(
                root,
                state=state,
                owner=owner,
                points=points,
                representation=representation,
            )
    except Exception as exc:
        raise_publication_error(exc)
