import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.las_sampling import sample_points_from_source
from pc_system.segmentation_benchmarks import load_benchmark_sample
from pc_system.segmentation_correspondence import match_point_labels
from pc_system.segmentation_provenance import fingerprint_points


MAX_POINT_PAGE_SIZE = 50_000


class CorrectionError(ValueError):
    """Phase 14 correction-domain error with a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _corrections_root(project_root: Path, asset_id: str) -> Path:
    return (
        project_root
        / "reports"
        / "segmentation_corrections"
        / validate_identifier(asset_id, "asset_id")
    )


def _session_dir(project_root: Path, asset_id: str, session_id: str) -> Path:
    return _corrections_root(project_root, asset_id) / validate_identifier(
        session_id, "session_id"
    )


def _read_json(path: Path, code: str) -> dict:
    if not path.is_file():
        raise CorrectionError(code, f"Correction artifact not found: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorrectionError("invalid_correction_artifact", str(exc)) from exc
    if not isinstance(payload, dict):
        raise CorrectionError(
            "invalid_correction_artifact", f"Expected a JSON object: {path.name}"
        )
    return payload


def _resolve_source(project_root: Path, source_uri: str) -> Path:
    path = Path(source_uri)
    if path.is_absolute() or path.exists():
        return path
    return project_root / path


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_expiry(ttl_seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    ).isoformat()


def _assignment_fingerprint(assignments: list[dict]) -> str:
    payload = json.dumps(
        assignments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _automatic_assignments(
    run_dir: Path, run: dict, points: list[dict]
) -> list[dict]:
    artifact = run.get("artifacts", {}).get("object_segments")
    if not artifact:
        raise CorrectionError(
            "object_segments_missing",
            "Completed segmentation run is missing its object report.",
        )
    report = _read_json(run_dir / artifact, "object_segments_missing")
    assigned: dict[int, dict] = {}
    for item in report.get("objects", []):
        membership_path = item.get("point_membership_artifact")
        if not isinstance(membership_path, str):
            raise CorrectionError(
                "membership_artifact_missing",
                f"Object is missing membership: {item.get('object_id')}",
            )
        membership = _read_json(
            run_dir / membership_path, "membership_artifact_missing"
        )
        indices = membership.get("source_point_indices")
        if not isinstance(indices, list):
            raise CorrectionError(
                "invalid_membership", "source_point_indices must be an array."
            )
        for raw_index in indices:
            if (
                isinstance(raw_index, bool)
                or not isinstance(raw_index, int)
                or raw_index < 0
                or raw_index >= len(points)
            ):
                raise CorrectionError(
                    "invalid_membership", f"Invalid source point index: {raw_index}"
                )
            if raw_index in assigned:
                raise CorrectionError(
                    "duplicate_membership",
                    f"Source point belongs to multiple objects: {raw_index}",
                )
            assigned[raw_index] = {
                "instance_id": validate_identifier(
                    str(item["object_id"]), "instance_id"
                ),
                "class_id": validate_identifier(
                    str(item.get("class_id", item.get("label", "object_candidate"))),
                    "class_id",
                ),
                "is_noise": False,
            }
    assignments = []
    for index, point in enumerate(points):
        assignment = assigned.get(
            index,
            {"instance_id": "noise", "class_id": "noise", "is_noise": True},
        )
        assignments.append(
            {
                "source_point_index": index,
                "x": float(point["x"]),
                "y": float(point["y"]),
                "z": float(point["z"]),
                **assignment,
            }
        )
    return assignments


def _overlay_benchmark_labels(
    project_root: Path,
    *,
    benchmark_id: str,
    sample_id: str,
    points: list[dict],
    assignments: list[dict],
) -> tuple[list[dict], dict]:
    try:
        manifest, labels = load_benchmark_sample(
            project_root,
            validate_identifier(benchmark_id, "benchmark_id"),
            validate_identifier(sample_id, "sample_id"),
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise CorrectionError("benchmark_not_found", str(exc)) from exc
    sample = next(
        (
            item
            for item in manifest.get("samples", [])
            if item.get("sample_id") == sample_id
        ),
        None,
    )
    if sample is None:
        raise CorrectionError(
            "benchmark_sample_not_found", f"Benchmark sample not found: {sample_id}"
        )
    try:
        matched, correspondence = match_point_labels(
            labels.get("point_labels", []),
            points,
            expected_fingerprint=str(sample["source_fingerprint"]),
            mode="strict_index",
            min_coverage=1.0,
        )
    except ValueError as exc:
        raise CorrectionError("benchmark_correspondence_failed", str(exc)) from exc
    by_index = {int(item["source_point_index"]): item for item in matched}
    overlaid = []
    for item in assignments:
        label = by_index.get(item["source_point_index"])
        if label is None:
            overlaid.append(dict(item))
            continue
        overlaid.append(
            {
                **item,
                "instance_id": validate_identifier(
                    str(label["instance_id"]), "instance_id"
                ),
                "class_id": validate_identifier(
                    str(label["class_id"]), "class_id"
                ),
                "is_noise": bool(label.get("is_noise", False)),
            }
        )
    return overlaid, {
        "benchmark_id": benchmark_id,
        "benchmark_version": manifest.get("benchmark_version"),
        "benchmark_split": manifest.get("split"),
        "label_version": manifest.get("label_version"),
        "correspondence": correspondence,
    }


def _object_document(assignments: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for item in assignments:
        if item["is_noise"]:
            continue
        groups.setdefault(item["instance_id"], []).append(item)
    objects = []
    for instance_id in sorted(groups):
        members = groups[instance_id]
        minimum = [
            min(point[axis] for point in members) for axis in ("x", "y", "z")
        ]
        maximum = [
            max(point[axis] for point in members) for axis in ("x", "y", "z")
        ]
        size = [max(high - low, 0.001) for low, high in zip(minimum, maximum)]
        objects.append(
            {
                "instance_id": instance_id,
                "class_id": members[0]["class_id"],
                "point_count": len(members),
                "source_point_indices": [
                    point["source_point_index"] for point in members
                ],
                "center": [
                    (low + high) / 2 for low, high in zip(minimum, maximum)
                ],
                "size": size,
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "review_state": "unreviewed",
            }
        )
    return {
        "schema_version": "1.0",
        "object_count": len(objects),
        "objects": objects,
    }


def create_correction_session(
    project_root: Path,
    *,
    asset_id: str,
    run_id: str,
    session_id: str,
    sample_id: str,
    actor: str,
    benchmark_id: str | None = None,
    lock_ttl_seconds: int = 900,
    baseline_release_id: str | None = None,
) -> dict:
    """Create one editable correction draft from a completed Phase 13A run."""

    asset_id = validate_identifier(asset_id, "asset_id")
    run_id = validate_identifier(run_id, "run_id")
    session_id = validate_identifier(session_id, "session_id")
    sample_id = validate_identifier(sample_id, "sample_id")
    if not isinstance(actor, str) or not actor.strip():
        raise CorrectionError("invalid_actor", "actor must be a non-empty string.")
    actor = actor.strip()
    if isinstance(lock_ttl_seconds, bool) or not isinstance(lock_ttl_seconds, int):
        raise CorrectionError(
            "invalid_lock_ttl", "lock_ttl_seconds must be a positive integer."
        )
    if lock_ttl_seconds <= 0:
        raise CorrectionError(
            "invalid_lock_ttl", "lock_ttl_seconds must be a positive integer."
        )
    if benchmark_id and baseline_release_id:
        raise CorrectionError(
            "multiple_baselines",
            "benchmark_id and baseline_release_id cannot be used together.",
        )
    if baseline_release_id is not None:
        raise CorrectionError(
            "release_baseline_not_available",
            "Correction release restoration is not available until publication support.",
        )
    final_dir = _session_dir(project_root, asset_id, session_id)
    if final_dir.exists():
        raise CorrectionError("session_exists", f"Session already exists: {session_id}")
    run_dir = (
        project_root / "reports" / "segmentation_runs" / asset_id / run_id
    )
    run_path = run_dir / "segmentation_run.json"
    if not run_path.is_file():
        raise CorrectionError(
            "segmentation_run_not_found", f"Segmentation run not found: {run_id}"
        )
    run = _read_json(run_path, "segmentation_run_not_found")
    if run.get("status") != "completed":
        raise CorrectionError(
            "segmentation_run_not_completed",
            "Only completed segmentation runs can be corrected.",
        )
    if run.get("asset_id") != asset_id:
        raise CorrectionError(
            "segmentation_run_asset_mismatch",
            "Segmentation run asset does not match correction asset.",
        )
    source_path = _resolve_source(project_root, str(run["source_uri"]))
    try:
        points = sample_points_from_source(
            source_path,
            max_points=int(
                run.get("config", {}).get(
                    "max_points", run.get("source_point_count", 10_000)
                )
            ),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise CorrectionError("source_points_unavailable", str(exc)) from exc
    if fingerprint_points(points) != run.get("source_fingerprint"):
        raise CorrectionError(
            "source_fingerprint_mismatch",
            "Segmentation source points no longer match the completed run.",
        )
    assignments = _automatic_assignments(run_dir, run, points)
    baseline_metadata = {
        "kind": "automatic_segmentation",
        "segmentation_run_id": run_id,
    }
    if benchmark_id:
        assignments, overlay = _overlay_benchmark_labels(
            project_root,
            benchmark_id=benchmark_id,
            sample_id=sample_id,
            points=points,
            assignments=assignments,
        )
        baseline_metadata = {
            "kind": "existing_labels",
            "segmentation_run_id": run_id,
            **overlay,
        }
    baseline = {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "session_id": session_id,
        "point_count": len(assignments),
        "source_fingerprint": run.get("source_fingerprint"),
        "assignments": assignments,
    }
    draft = {
        **baseline,
        "baseline_fingerprint": _assignment_fingerprint(assignments),
        "draft_fingerprint": _assignment_fingerprint(assignments),
    }
    created_at = _iso_now()
    session = {
        "schema_version": "1.0",
        "session_id": session_id,
        "asset_id": asset_id,
        "sample_id": sample_id,
        "segmentation_run_id": run_id,
        "status": "draft",
        "revision": 0,
        "active_editor": actor,
        "lock_ttl_seconds": lock_ttl_seconds,
        "lock_expires_at": _lock_expiry(lock_ttl_seconds),
        "created_at": created_at,
        "updated_at": created_at,
        "baseline": baseline_metadata,
        "draft_fingerprint": draft["draft_fingerprint"],
        "supersedes_release_id": None,
        "artifacts": {
            "baseline_labels": "baseline_labels.json",
            "events": "events.jsonl",
            "draft_labels": "draft_labels.json",
            "draft_objects": "draft_objects.json",
        },
    }
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = final_dir.with_name(
        f".{final_dir.name}.staging-{uuid.uuid4().hex}"
    )
    try:
        write_json(baseline, staging / "baseline_labels.json")
        write_json(draft, staging / "draft_labels.json")
        write_json(_object_document(assignments), staging / "draft_objects.json")
        (staging / "events.jsonl").write_text("", encoding="utf-8")
        write_json(session, staging / "correction_session.json")
        if final_dir.exists():
            raise CorrectionError(
                "session_exists", f"Session already exists: {session_id}"
            )
        os.replace(staging, final_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return session


def load_correction_session(
    project_root: Path, asset_id: str, session_id: str
) -> dict:
    return _read_json(
        _session_dir(project_root, asset_id, session_id)
        / "correction_session.json",
        "session_not_found",
    )


def load_correction_baseline(
    project_root: Path, asset_id: str, session_id: str
) -> dict:
    return _read_json(
        _session_dir(project_root, asset_id, session_id)
        / "baseline_labels.json",
        "session_not_found",
    )


def load_correction_objects(
    project_root: Path, asset_id: str, session_id: str
) -> dict:
    return _read_json(
        _session_dir(project_root, asset_id, session_id) / "draft_objects.json",
        "session_not_found",
    )


def load_correction_points(
    project_root: Path,
    asset_id: str,
    session_id: str,
    *,
    offset: int = 0,
    limit: int = 10_000,
) -> dict:
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
        or limit > MAX_POINT_PAGE_SIZE
    ):
        raise CorrectionError(
            "invalid_pagination",
            f"offset must be non-negative and limit must be 1-{MAX_POINT_PAGE_SIZE}.",
        )
    root = _session_dir(project_root, asset_id, session_id)
    baseline = _read_json(root / "baseline_labels.json", "session_not_found")
    draft = _read_json(root / "draft_labels.json", "session_not_found")
    baseline_by_index = {
        item["source_point_index"]: item for item in baseline["assignments"]
    }
    draft_items = draft["assignments"]
    page = []
    for item in draft_items[offset : offset + limit]:
        index = item["source_point_index"]
        original = baseline_by_index[index]
        page.append(
            {
                "source_point_index": index,
                "x": item["x"],
                "y": item["y"],
                "z": item["z"],
                "baseline": {
                    key: original[key]
                    for key in ("instance_id", "class_id", "is_noise")
                },
                "draft": {
                    key: item[key]
                    for key in ("instance_id", "class_id", "is_noise")
                },
            }
        )
    return {
        "asset_id": validate_identifier(asset_id, "asset_id"),
        "session_id": validate_identifier(session_id, "session_id"),
        "offset": offset,
        "limit": limit,
        "total": len(draft_items),
        "points": page,
    }


def list_correction_sessions(project_root: Path, asset_id: str) -> list[dict]:
    root = _corrections_root(project_root, asset_id)
    sessions = []
    if root.exists():
        for path in sorted(
            root.glob("*/correction_session.json"), key=lambda item: item.parent.name
        ):
            sessions.append(_read_json(path, "invalid_correction_artifact"))
    return sessions
