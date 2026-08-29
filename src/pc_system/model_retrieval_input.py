import hashlib
import json
import math
import stat
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.segmentation_corrections import CorrectionError
from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_review_evidence import load_object_review_evidence


_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_POINTS = 2_000_000
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def _error(code: str, message: str) -> ModelMatchingError:
    return ModelMatchingError(code, message)


def _canonical_fingerprint(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("invalid_retrieval_input", "Retrieval input is not canonical JSON.") from exc
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path, *, missing_code: str, invalid_code: str) -> dict:
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)
            or info.st_size > _MAX_SOURCE_BYTES
        ):
            raise ValueError("unsafe retrieval input artifact")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _error(missing_code, "Retrieval source does not exist.") from exc
    except (OSError, RecursionError, UnicodeError, ValueError) as exc:
        raise _error(invalid_code, "Retrieval source artifact is invalid.") from exc
    if type(value) is not dict:
        raise _error(invalid_code, "Retrieval source artifact must be an object.")
    return value


def _identity(asset_id: object, source_id: object, instance_id: object) -> tuple[str, str, str]:
    try:
        return (
            validate_identifier(asset_id, "asset_id"),
            validate_identifier(source_id, "source_id"),
            validate_identifier(instance_id, "instance_id"),
        )
    except (TypeError, ValueError) as exc:
        raise _error("invalid_retrieval_input", "Retrieval source identity is invalid.") from exc


def _coordinate(point: dict, axis: str) -> float:
    value = point.get(axis)
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise _error("invalid_retrieval_input", "Retrieval point is invalid.")
    return float(value)


def _release_labels(value: dict) -> list[dict]:
    if value.get("schema_version") != "1.0" or type(value.get("point_labels")) is not list:
        raise _error("invalid_retrieval_input", "Release labels are invalid.")
    labels = value["point_labels"]
    if not 0 < len(labels) <= _MAX_POINTS:
        raise _error("invalid_retrieval_input", "Release point count is invalid.")
    normalized = []
    seen: set[int] = set()
    for item in labels:
        if type(item) is not dict:
            raise _error("invalid_retrieval_input", "Release point label is invalid.")
        index = item.get("point_index")
        is_noise = item.get("is_noise")
        try:
            instance_id = validate_identifier(item.get("instance_id"), "instance_id")
            class_id = validate_identifier(item.get("class_id"), "class_id")
        except (TypeError, ValueError) as exc:
            raise _error("invalid_retrieval_input", "Release label identity is invalid.") from exc
        if (
            type(index) is not int
            or index < 0
            or index in seen
            or type(is_noise) is not bool
        ):
            raise _error("invalid_retrieval_input", "Release label values are invalid.")
        seen.add(index)
        normalized.append(
            {
                "source_point_index": index,
                "x": _coordinate(item, "x"),
                "y": _coordinate(item, "y"),
                "z": _coordinate(item, "z"),
                "instance_id": instance_id,
                "class_id": class_id,
                "is_noise": is_noise,
            }
        )
    normalized.sort(key=lambda item: item["source_point_index"])
    if [item["source_point_index"] for item in normalized] != list(range(len(normalized))):
        raise _error("invalid_retrieval_input", "Release labels must cover the source exactly.")
    return normalized


def _load_release_object(
    root: Path, asset_id: str, release_id: str, instance_id: str
) -> dict:
    release_root = (
        root
        / "reports"
        / "segmentation_correction_releases"
        / asset_id
        / release_id
    )
    release = _read_json(
        release_root / "correction_release.json",
        missing_code="retrieval_object_not_found",
        invalid_code="invalid_retrieval_input",
    )
    if (
        release.get("schema_version") != "1.0"
        or release.get("asset_id") != asset_id
        or release.get("release_id") != release_id
        or release.get("status") != "published"
        or type(release.get("artifacts")) is not dict
    ):
        raise _error("invalid_retrieval_input", "Correction release is not published.")
    labels = _release_labels(
        _read_json(
            release_root / "labels.json",
            missing_code="invalid_retrieval_input",
            invalid_code="invalid_retrieval_input",
        )
    )
    source_fingerprint = release.get("source_fingerprint")
    all_points = [
        {axis: item[axis] for axis in ("x", "y", "z")} for item in labels
    ]
    try:
        observed_source_fingerprint = fingerprint_points(all_points)
    except ValueError as exc:
        raise _error("invalid_retrieval_input", "Release source points are invalid.") from exc
    if observed_source_fingerprint != source_fingerprint:
        raise _error("invalid_retrieval_input", "Release source fingerprint differs.")
    selected = [
        item for item in labels if not item["is_noise"] and item["instance_id"] == instance_id
    ]
    if not selected:
        raise _error("retrieval_object_not_found", "Retrieval object does not exist.")
    classes = {item["class_id"] for item in selected}
    if len(classes) != 1:
        raise _error("invalid_retrieval_input", "Retrieval object class is inconsistent.")
    object_fingerprint = _canonical_fingerprint(selected)
    declared_evidence = "object_review_evidence" in release["artifacts"]
    try:
        evidence = load_object_review_evidence(root, asset_id, release_id)
    except CorrectionError as exc:
        raise _error(
            "object_review_evidence_invalid", "Object review evidence is invalid."
        ) from exc
    if evidence is None:
        if declared_evidence:
            raise _error(
                "object_review_evidence_invalid", "Declared review evidence is missing."
            )
        category_trust = "legacy_unknown"
        classification_source = "legacy_unknown"
    else:
        if evidence.get("source_fingerprint") != source_fingerprint:
            raise _error(
                "object_review_evidence_invalid", "Review evidence source differs."
            )
        matches = [item for item in evidence["objects"] if item["instance_id"] == instance_id]
        if len(matches) != 1:
            raise _error(
                "object_review_evidence_invalid", "Review object evidence is missing."
            )
        review = matches[0]
        if (
            review["class_id"] != next(iter(classes))
            or review["point_count"] != len(selected)
            or review["object_fingerprint"] != object_fingerprint
        ):
            raise _error(
                "object_review_evidence_invalid", "Review object evidence differs."
            )
        classification_source = review["classification_source"]
        category_trust = (
            "human_confirmed"
            if review["review_state"] == "confirmed"
            else classification_source
        )
    return {
        "schema_version": "1.0",
        "source_kind": "correction_release",
        "asset_id": asset_id,
        "source_id": release_id,
        "instance_id": instance_id,
        "class_id": next(iter(classes)),
        "category_trust": category_trust,
        "classification_source": classification_source,
        "coordinate_unit": "m",
        "source_fingerprint": source_fingerprint,
        "object_fingerprint": object_fingerprint,
        "point_count": len(selected),
        "points": [{axis: item[axis] for axis in ("x", "y", "z")} for item in selected],
    }


def _safe_artifact(root: Path, relative: object) -> Path:
    if type(relative) is not str:
        raise _error("invalid_retrieval_input", "Membership artifact is invalid.")
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise _error("invalid_retrieval_input", "Membership artifact path is unsafe.")
    return root.joinpath(*path.parts)


def _load_run_object(
    root: Path,
    asset_id: str,
    run_id: str,
    instance_id: str,
    principal: Principal | None,
    *,
    enforce_expert: bool = True,
) -> dict:
    if enforce_expert:
        if principal is None:
            raise _error("permission_denied", "Experimental retrieval requires an expert.")
        require_any_role(principal, {"expert"})
    run_root = root / "reports" / "segmentation_runs" / asset_id / run_id
    run = _read_json(
        run_root / "segmentation_run.json",
        missing_code="retrieval_object_not_found",
        invalid_code="invalid_retrieval_input",
    )
    if (
        run.get("schema_version") != "1.0"
        or run.get("asset_id") != asset_id
        or run.get("run_id") != run_id
        or run.get("status") != "completed"
        or type(run.get("source_fingerprint")) is not str
    ):
        raise _error("invalid_retrieval_input", "Segmentation run is not completed.")
    report = _read_json(
        run_root / "object_segments.json",
        missing_code="invalid_retrieval_input",
        invalid_code="invalid_retrieval_input",
    )
    objects = report.get("objects")
    if type(objects) is not list:
        raise _error("invalid_retrieval_input", "Segmentation object report is invalid.")
    matches = [item for item in objects if type(item) is dict and item.get("object_id") == instance_id]
    if len(matches) != 1:
        raise _error("retrieval_object_not_found", "Retrieval object does not exist.")
    item = matches[0]
    try:
        class_id = validate_identifier(item.get("label"), "class_id")
    except (TypeError, ValueError) as exc:
        raise _error("invalid_retrieval_input", "Segmentation class is invalid.") from exc
    membership = _read_json(
        _safe_artifact(run_root, item.get("point_membership_artifact")),
        missing_code="invalid_retrieval_input",
        invalid_code="invalid_retrieval_input",
    )
    points = membership.get("points")
    indices = membership.get("source_point_indices")
    if (
        membership.get("schema_version") != "1.0"
        or membership.get("object_id") != instance_id
        or type(points) is not list
        or type(indices) is not list
        or not 0 < len(points) == len(indices) <= _MAX_POINTS
        or membership.get("point_count") != len(points)
        or any(type(index) is not int or index < 0 for index in indices)
        or len(set(indices)) != len(indices)
    ):
        raise _error("invalid_retrieval_input", "Object membership is invalid.")
    normalized_points = [
        {axis: _coordinate(point, axis) for axis in ("x", "y", "z")}
        if type(point) is dict
        else None
        for point in points
    ]
    if any(point is None for point in normalized_points):
        raise _error("invalid_retrieval_input", "Object membership point is invalid.")
    fingerprint_input = [
        {
            "source_point_index": index,
            **point,
            "instance_id": instance_id,
            "class_id": class_id,
            "is_noise": False,
        }
        for index, point in sorted(zip(indices, normalized_points), key=lambda pair: pair[0])
    ]
    return {
        "schema_version": "1.0",
        "source_kind": "segmentation_run",
        "asset_id": asset_id,
        "source_id": run_id,
        "instance_id": instance_id,
        "class_id": class_id,
        "category_trust": "algorithm_only",
        "classification_source": "automatic_segmentation",
        "coordinate_unit": "m",
        "source_fingerprint": run["source_fingerprint"],
        "object_fingerprint": _canonical_fingerprint(fingerprint_input),
        "point_count": len(normalized_points),
        "points": normalized_points,
    }


def load_retrieval_object(
    project_root: Path,
    *,
    source_kind: str,
    asset_id: str,
    source_id: str,
    instance_id: str,
    principal: Principal | None = None,
) -> dict:
    asset_id, source_id, instance_id = _identity(asset_id, source_id, instance_id)
    root = Path(project_root)
    if source_kind == "correction_release":
        return _load_release_object(root, asset_id, source_id, instance_id)
    if source_kind == "segmentation_run":
        return _load_run_object(root, asset_id, source_id, instance_id, principal)
    raise _error("invalid_retrieval_input", "Retrieval source kind is invalid.")


def _reload_retrieval_object(
    project_root: Path,
    *,
    source_kind: str,
    asset_id: str,
    source_id: str,
    instance_id: str,
) -> dict:
    asset_id, source_id, instance_id = _identity(asset_id, source_id, instance_id)
    root = Path(project_root)
    if source_kind == "correction_release":
        return _load_release_object(root, asset_id, source_id, instance_id)
    if source_kind == "segmentation_run":
        return _load_run_object(
            root,
            asset_id,
            source_id,
            instance_id,
            None,
            enforce_expert=False,
        )
    raise _error("invalid_retrieval_input", "Retrieval source kind is invalid.")
