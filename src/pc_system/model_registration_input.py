import hashlib
import json
from pathlib import Path

from pc_system.model_feature_store import load_feature
from pc_system.model_library import load_model_asset
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import _require_plain, list_model_releases
from pc_system.model_retrieval import load_model_retrieval
from pc_system.model_retrieval_input import load_retrieval_object
from pc_system.model_sampling import (
    _canonical_json_bytes,
    load_sampled_representation,
)


_CANDIDATE_FIELDS = {
    "model_id",
    "version_id",
    "release_id",
    "representation_id",
    "representation_fingerprint",
    "feature_id",
    "feature_vector_fingerprint",
}
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


def _incomplete(message: str) -> ModelMatchingError:
    return ModelMatchingError("registration_input_incomplete", message)


def _integrity(message: str) -> ModelMatchingError:
    return ModelMatchingError("artifact_integrity_failed", message)


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _read_canonical_artifact(path: Path) -> tuple[dict, str]:
    try:
        _require_plain(path, directory=False)
        if path.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise ValueError("registration artifact is too large")
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
        if type(value) is not dict or payload != _canonical_json_bytes(value):
            raise ValueError("registration artifact is not canonical")
        return value, hashlib.sha256(payload).hexdigest()
    except (OSError, UnicodeError, ValueError, ModelMatchingError) as exc:
        raise _integrity("Registration source artifact is invalid.") from exc


def _load_retrieval(
    root: Path,
    *,
    asset_id: str,
    source_id: str,
    instance_id: str,
    retrieval_run_id: str,
) -> dict:
    try:
        return load_model_retrieval(
            root,
            asset_id=asset_id,
            source_id=source_id,
            instance_id=instance_id,
            retrieval_run_id=retrieval_run_id,
        )
    except ModelMatchingError as exc:
        if exc.code in {"operation_busy", "retrieval_object_not_found"}:
            raise
        raise _integrity("Retrieval evidence is invalid.") from exc


def _required_candidate(report: dict, candidate_rank: int) -> dict:
    if type(candidate_rank) is not int or type(candidate_rank) is bool:
        raise _incomplete("Candidate rank must be a one-based integer.")
    if report.get("schema_version") != "1.1":
        raise _incomplete("Retrieval evidence does not freeze registration inputs.")
    candidates = report.get("candidates")
    if (
        type(candidates) is not list
        or candidate_rank < 1
        or candidate_rank > len(candidates)
    ):
        raise _incomplete("Candidate rank does not exist in the retrieval report.")
    candidate = candidates[candidate_rank - 1]
    if type(candidate) is not dict or any(
        type(candidate.get(field)) is not str or not candidate[field]
        for field in _CANDIDATE_FIELDS
    ):
        raise _incomplete("Candidate registration evidence is incomplete.")
    return candidate


def _object_points(value: dict) -> list[list[float]]:
    try:
        if value["coordinate_unit"] != "m" or type(value["points"]) is not list:
            raise ValueError("object point evidence is invalid")
        return [
            [float(point["x"]), float(point["y"]), float(point["z"])]
            for point in value["points"]
        ]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise _incomplete("Object point evidence is incomplete.") from exc


def _load_model_evidence(root: Path, candidate: dict) -> tuple[dict, dict, list]:
    model_id = candidate["model_id"]
    version_id = candidate["version_id"]
    representation_id = candidate["representation_id"]
    try:
        representation = load_sampled_representation(
            root, model_id, version_id, representation_id
        )
        releases = list_model_releases(root, model_id)
        release = next(
            item
            for item in releases
            if item["release_id"] == candidate["release_id"]
        )
        asset = load_model_asset(root, model_id)
        feature = load_feature(
            root,
            feature_type="model",
            identity={
                "model_id": model_id,
                "version_id": version_id,
                "representation_id": representation_id,
                "feature_id": candidate["feature_id"],
            },
        )
    except (ModelMatchingError, StopIteration) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise _integrity("Candidate model evidence is invalid.") from exc

    directory = (
        root
        / "models"
        / model_id
        / "representations"
        / version_id
        / "cad_sampled"
        / representation_id
    )
    visible_representation, representation_fingerprint = _read_canonical_artifact(
        directory / "representation.json"
    )
    sampled_points, points_fingerprint = _read_canonical_artifact(
        directory / "sampled_points.json"
    )
    try:
        if (
            visible_representation != representation
            or representation_fingerprint
            != candidate["representation_fingerprint"]
            or points_fingerprint != representation["geometry_fingerprint"]
            or release["version_id"] != version_id
            or feature["feature_vector_fingerprint"]
            != candidate["feature_vector_fingerprint"]
            or feature["source"]["representation_fingerprint"]
            != representation_fingerprint
            or feature["source"]["representation_geometry_fingerprint"]
            != points_fingerprint
            or sampled_points["coordinate_unit"] != "m"
            or sampled_points["point_count"] != representation["point_count"]
            or type(sampled_points["points"]) is not list
        ):
            raise ValueError("candidate evidence differs")
    except (KeyError, TypeError, ValueError) as exc:
        raise _integrity("Candidate model evidence differs.") from exc
    return asset, feature, sampled_points["points"]


def load_registration_input(
    project_root: Path,
    *,
    asset_id: str,
    source_id: str,
    instance_id: str,
    retrieval_run_id: str,
    candidate_rank: int,
    principal: Principal,
) -> dict:
    """Freeze a verified retrieval candidate and both point clouds for registration."""

    require_any_role(principal, {"expert"})
    root = Path(project_root)
    report = _load_retrieval(
        root,
        asset_id=asset_id,
        source_id=source_id,
        instance_id=instance_id,
        retrieval_run_id=retrieval_run_id,
    )
    candidate = _required_candidate(report, candidate_rank)
    try:
        observed = load_retrieval_object(
            root,
            source_kind=report["source_kind"],
            asset_id=asset_id,
            source_id=source_id,
            instance_id=instance_id,
            principal=principal,
        )
    except ModelMatchingError as exc:
        if exc.code == "operation_busy":
            raise
        raise _integrity("Object evidence is invalid.") from exc
    if observed.get("object_fingerprint") != report.get("object_fingerprint"):
        raise ModelMatchingError(
            "object_fingerprint_stale",
            "Object point cloud changed after candidate retrieval.",
        )

    model_asset, feature, model_points = _load_model_evidence(root, candidate)
    retrieval_evidence = {
        key: report[key]
        for key in (
            "schema_version",
            "retrieval_run_id",
            "asset_id",
            "source_id",
            "instance_id",
            "source_kind",
            "object_fingerprint",
            "query_feature_id",
            "query_feature_fingerprint",
            "index_release_id",
            "index_id",
            "config_id",
            "config_fingerprint",
            "report_fingerprint",
            "operation_id",
        )
    }
    candidate_evidence = {
        "candidate_rank": candidate_rank,
        **json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False)),
        "category_id": model_asset["category_id"],
        "feature_operation_id": feature["operation_id"],
    }
    frozen = {
        "retrieval_evidence": retrieval_evidence,
        "candidate_evidence": candidate_evidence,
        "object_fingerprint": observed["object_fingerprint"],
        "model_points": model_points,
        "object_points": _object_points(observed),
        "symmetry_transforms": [],
        "coordinate_unit": "m",
    }
    try:
        return json.loads(
            json.dumps(frozen, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise _integrity("Registration input cannot be frozen safely.") from exc
