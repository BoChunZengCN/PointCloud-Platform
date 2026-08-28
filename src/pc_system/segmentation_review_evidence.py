import hashlib
import json
import math
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.segmentation_corrections import CorrectionError


_MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


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
        raise CorrectionError(
            "invalid_review_evidence", "Review evidence is not canonical JSON."
        ) from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object_points(assignments: object) -> dict[str, list[dict]]:
    if not isinstance(assignments, list):
        raise CorrectionError(
            "invalid_review_evidence", "Draft assignments must be an array."
        )
    groups: dict[str, list[dict]] = {}
    seen_indices: set[int] = set()
    for value in assignments:
        if not isinstance(value, dict):
            raise CorrectionError(
                "invalid_review_evidence", "Draft assignment must be an object."
            )
        try:
            index = value["source_point_index"]
            instance_id = validate_identifier(value["instance_id"], "instance_id")
            class_id = validate_identifier(value["class_id"], "class_id")
            is_noise = value["is_noise"]
            coordinates = [value[axis] for axis in ("x", "y", "z")]
        except (KeyError, TypeError, ValueError) as exc:
            raise CorrectionError(
                "invalid_review_evidence", "Draft assignment identity is invalid."
            ) from exc
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index in seen_indices
            or type(is_noise) is not bool
            or any(
                type(coordinate) not in {int, float}
                or not math.isfinite(float(coordinate))
                for coordinate in coordinates
            )
        ):
            raise CorrectionError(
                "invalid_review_evidence", "Draft assignment values are invalid."
            )
        seen_indices.add(index)
        if is_noise:
            continue
        groups.setdefault(instance_id, []).append(
            {
                "source_point_index": index,
                "x": float(coordinates[0]),
                "y": float(coordinates[1]),
                "z": float(coordinates[2]),
                "instance_id": instance_id,
                "class_id": class_id,
                "is_noise": False,
                "origin": value.get("origin", "automatic_segmentation"),
            }
        )
    for points in groups.values():
        points.sort(key=lambda item: item["source_point_index"])
    return groups


def _latest_confirmation(active_events: list[dict], instance_id: str) -> dict | None:
    matching = []
    for event in active_events:
        operation = event.get("operation")
        if (
            isinstance(operation, dict)
            and operation.get("type") == "confirm"
            and instance_id in operation.get("instance_ids", [])
        ):
            matching.append(event)
    return matching[-1] if matching else None


def build_object_review_evidence(
    *,
    asset_id: str,
    release_id: str,
    source_fingerprint: str,
    draft: dict,
    objects: dict,
    active_events: list[dict],
) -> dict:
    asset_id = validate_identifier(asset_id, "asset_id")
    release_id = validate_identifier(release_id, "release_id")
    if (
        not isinstance(source_fingerprint, str)
        or len(source_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in source_fingerprint)
    ):
        raise CorrectionError(
            "invalid_review_evidence", "Source fingerprint is invalid."
        )
    if not isinstance(draft, dict) or not isinstance(objects, dict):
        raise CorrectionError(
            "invalid_review_evidence", "Review evidence inputs are invalid."
        )
    object_values = objects.get("objects")
    confirmed_values = draft.get("confirmed_instance_ids")
    if not isinstance(object_values, list) or not isinstance(confirmed_values, list):
        raise CorrectionError(
            "invalid_review_evidence", "Review evidence structure is invalid."
        )
    try:
        confirmed = {
            validate_identifier(value, "instance_id") for value in confirmed_values
        }
    except (TypeError, ValueError) as exc:
        raise CorrectionError(
            "invalid_review_evidence", "Confirmed object identities are invalid."
        ) from exc
    groups = _object_points(draft.get("assignments"))
    evidence_objects = []
    seen_objects: set[str] = set()
    if any(not isinstance(item, dict) for item in object_values):
        raise CorrectionError(
            "invalid_review_evidence", "Review object must be an object."
        )
    for value in sorted(object_values, key=lambda item: item.get("instance_id", "")):
        try:
            instance_id = validate_identifier(value["instance_id"], "instance_id")
            class_id = validate_identifier(value["class_id"], "class_id")
        except (KeyError, TypeError, ValueError) as exc:
            raise CorrectionError(
                "invalid_review_evidence", "Review object identity is invalid."
            ) from exc
        points = groups.get(instance_id)
        if instance_id in seen_objects or not points:
            raise CorrectionError(
                "invalid_review_evidence", "Review object membership is invalid."
            )
        seen_objects.add(instance_id)
        if any(point["class_id"] != class_id for point in points):
            raise CorrectionError(
                "invalid_review_evidence", "Review object class is inconsistent."
            )
        confirmation = _latest_confirmation(active_events, instance_id)
        is_confirmed = instance_id in confirmed
        if is_confirmed and confirmation is None:
            raise CorrectionError(
                "invalid_review_evidence", "Confirmed object has no active event."
            )
        human_edited = any(
            point.get("origin") == "human_correction" for point in points
        )
        evidence_objects.append(
            {
                "instance_id": instance_id,
                "class_id": class_id,
                "object_fingerprint": _fingerprint(
                    [
                        {
                            key: point[key]
                            for key in (
                                "source_point_index",
                                "x",
                                "y",
                                "z",
                                "instance_id",
                                "class_id",
                                "is_noise",
                            )
                        }
                        for point in points
                    ]
                ),
                "point_count": len(points),
                "review_state": "confirmed" if is_confirmed else "unreviewed",
                "classification_source": (
                    "human_confirmed"
                    if is_confirmed
                    else "human_edited_unconfirmed"
                    if human_edited
                    else "automatic_segmentation"
                ),
                "confirmation_event_sequence": (
                    confirmation.get("resulting_revision") if confirmation else None
                ),
                "confirmation_request_id": (
                    confirmation.get("client_request_id") if confirmation else None
                ),
            }
        )
    if seen_objects != set(groups):
        raise CorrectionError(
            "invalid_review_evidence", "Review objects do not cover draft objects."
        )
    return {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "release_id": release_id,
        "source_fingerprint": source_fingerprint,
        "objects_fingerprint": _fingerprint(evidence_objects),
        "objects": evidence_objects,
    }


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_evidence(value: dict, asset_id: str, release_id: str) -> dict:
    if set(value) != {
        "schema_version",
        "asset_id",
        "release_id",
        "source_fingerprint",
        "objects_fingerprint",
        "objects",
    } or value.get("schema_version") != "1.0":
        raise CorrectionError(
            "invalid_review_evidence", "Review evidence structure is invalid."
        )
    objects = value.get("objects")
    if (
        value.get("asset_id") != asset_id
        or value.get("release_id") != release_id
        or not _is_sha256(value.get("source_fingerprint"))
        or not _is_sha256(value.get("objects_fingerprint"))
        or not isinstance(objects, list)
        or value["objects_fingerprint"] != _fingerprint(objects)
    ):
        raise CorrectionError(
            "invalid_review_evidence", "Review evidence identity is invalid."
        )
    identities = []
    for item in objects:
        if not isinstance(item, dict) or set(item) != {
            "instance_id",
            "class_id",
            "object_fingerprint",
            "point_count",
            "review_state",
            "classification_source",
            "confirmation_event_sequence",
            "confirmation_request_id",
        }:
            raise CorrectionError(
                "invalid_review_evidence", "Review object structure is invalid."
            )
        try:
            instance_id = validate_identifier(item["instance_id"], "instance_id")
            validate_identifier(item["class_id"], "class_id")
        except (TypeError, ValueError) as exc:
            raise CorrectionError(
                "invalid_review_evidence", "Review object identity is invalid."
            ) from exc
        point_count = item["point_count"]
        review_state = item["review_state"]
        classification_source = item["classification_source"]
        sequence = item["confirmation_event_sequence"]
        request_id = item["confirmation_request_id"]
        if (
            instance_id in identities
            or not _is_sha256(item["object_fingerprint"])
            or isinstance(point_count, bool)
            or not isinstance(point_count, int)
            or point_count <= 0
            or review_state not in {"confirmed", "unreviewed"}
            or classification_source
            not in {
                "human_confirmed",
                "human_edited_unconfirmed",
                "automatic_segmentation",
            }
        ):
            raise CorrectionError(
                "invalid_review_evidence", "Review object values are invalid."
            )
        if review_state == "confirmed":
            try:
                validate_identifier(request_id, "confirmation_request_id")
            except (TypeError, ValueError) as exc:
                raise CorrectionError(
                    "invalid_review_evidence", "Confirmation evidence is invalid."
                ) from exc
            if (
                classification_source != "human_confirmed"
                or isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence <= 0
            ):
                raise CorrectionError(
                    "invalid_review_evidence", "Confirmation evidence is invalid."
                )
        elif sequence is not None or request_id is not None:
            raise CorrectionError(
                "invalid_review_evidence", "Unreviewed object has confirmation evidence."
            )
        identities.append(instance_id)
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise CorrectionError(
            "invalid_review_evidence", "Review objects are not canonical."
        )
    return dict(value)


def load_object_review_evidence(
    project_root: Path, asset_id: str, release_id: str
) -> dict | None:
    root = (
        Path(project_root)
        / "reports"
        / "segmentation_correction_releases"
        / validate_identifier(asset_id, "asset_id")
        / validate_identifier(release_id, "release_id")
    )
    if not root.is_dir():
        raise CorrectionError("release_not_found", f"Release not found: {release_id}")
    path = root / "object_review_evidence.json"
    if not path.exists():
        return None
    try:
        if path.stat().st_size > _MAX_EVIDENCE_BYTES:
            raise CorrectionError(
                "invalid_review_evidence", "Review evidence exceeds the size limit."
            )
        value = json.loads(path.read_text(encoding="utf-8"))
    except CorrectionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorrectionError(
            "invalid_review_evidence", "Review evidence could not be read."
        ) from exc
    if not isinstance(value, dict):
        raise CorrectionError(
            "invalid_review_evidence", "Review evidence must be an object."
        )
    return _validate_evidence(value, asset_id, release_id)
