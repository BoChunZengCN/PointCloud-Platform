import json
import os
import shutil
import uuid
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.las_sampling import sample_points_from_source
from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_benchmarks import BENCHMARK_SPLITS
from pc_system.segmentation_evaluation import evaluate_segmentation_run
from pc_system.segmentation_regression import compare_evaluations
from pc_system.segmentation_correction_events import (
    materialize_correction,
    read_correction_events,
)
from pc_system.segmentation_corrections import (
    CorrectionError,
    _acquire_session_write_lock,
    _iso_now,
    _object_document,
    _release_session_write_lock,
    _session_dir,
    load_correction_baseline,
    load_correction_objects,
    load_correction_session,
)
from pc_system.segmentation_review_queue import build_correction_diff


TRANSITIONS = {
    ("draft", "submit"): "in_review",
    ("in_review", "return"): "draft",
    ("draft", "abandon"): "abandoned",
    ("in_review", "abandon"): "abandoned",
}


def _release_root(project_root: Path, asset_id: str) -> Path:
    return (
        project_root
        / "reports"
        / "segmentation_correction_releases"
        / validate_identifier(asset_id, "asset_id")
    )


def _release_dir(project_root: Path, asset_id: str, release_id: str) -> Path:
    return _release_root(project_root, asset_id) / validate_identifier(
        release_id, "release_id"
    )


def _read_json(path: Path, code: str) -> dict:
    if not path.is_file():
        raise CorrectionError(code, f"Artifact not found: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorrectionError("invalid_release_artifact", str(exc)) from exc
    if not isinstance(payload, dict):
        raise CorrectionError(
            "invalid_release_artifact", f"Expected JSON object: {path.name}"
        )
    return payload


def _transition_correction_session_unlocked(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    action: str,
    actor: str,
    expected_revision: int,
) -> dict:
    """Apply a validated draft/review lifecycle transition."""

    session = load_correction_session(project_root, asset_id, session_id)
    if not isinstance(actor, str) or not actor.strip():
        raise CorrectionError("invalid_actor", "actor must be a non-empty string.")
    if action in {"submit", "abandon"} and session.get("active_editor") != actor.strip():
        raise CorrectionError(
            "session_locked",
            f"Session is owned by {session.get('active_editor')}.",
        )
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision != session.get("revision")
    ):
        raise CorrectionError(
            "stale_revision",
            f"Expected revision {expected_revision}, current revision is {session.get('revision')}.",
        )
    target = TRANSITIONS.get((str(session.get("status")), action))
    if target is None:
        raise CorrectionError(
            "invalid_session_transition",
            f"Cannot {action} a session in state {session.get('status')}.",
        )
    now = _iso_now()
    updated = {
        **session,
        "status": target,
        "revision": int(session["revision"]) + 1,
        "updated_at": now,
        "last_lifecycle_action": {
            "action": action,
            "actor": actor.strip(),
            "timestamp": now,
            "from_status": session["status"],
            "to_status": target,
        },
    }
    write_json(
        updated,
        _session_dir(project_root, asset_id, session_id)
        / "correction_session.json",
    )
    if target in {"published", "abandoned"}:
        (
            _session_dir(project_root, asset_id, session_id).parent
            / f".active-{session['sample_id']}.lock"
        ).unlink(missing_ok=True)
    return updated


def transition_correction_session(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    action: str,
    actor: str,
    expected_revision: int,
) -> dict:
    lock_path = _acquire_session_write_lock(
        project_root, asset_id, session_id
    )
    try:
        return _transition_correction_session_unlocked(
            project_root,
            asset_id=asset_id,
            session_id=session_id,
            action=action,
            actor=actor,
            expected_revision=expected_revision,
        )
    finally:
        _release_session_write_lock(lock_path)


def _labels_from_assignments(draft: dict, objects: dict) -> dict:
    return {
        "schema_version": "1.0",
        "point_labels": [
            {
                "point_index": int(item["source_point_index"]),
                "x": float(item["x"]),
                "y": float(item["y"]),
                "z": float(item["z"]),
                "instance_id": str(item["instance_id"]),
                "class_id": str(item["class_id"]),
                "is_noise": bool(item["is_noise"]),
                "origin": item.get("origin", "unknown"),
            }
            for item in draft["assignments"]
        ],
        "boxes": [
            {
                "instance_id": item["instance_id"],
                "class_id": item["class_id"],
                "center": item["center"],
                "size": item["size"],
                "rotation": item["rotation"],
            }
            for item in objects["objects"]
        ],
    }


def _training_policy(
    *, release_id: str, benchmark_split: str, license_name: str
) -> dict:
    if benchmark_split == "development":
        eligibility = "blocked"
        reasons = ["evaluation_not_completed"]
    else:
        eligibility = "evaluation_only"
        reasons = [f"{benchmark_split}_split_is_not_training_input"]
    return {
        "schema_version": "1.0",
        "release_id": release_id,
        "benchmark_split": benchmark_split,
        "license": license_name,
        "eligibility": eligibility,
        "reasons": reasons,
        "starts_training": False,
        "promotes_model": False,
    }


def _task_document(
    *,
    release_id: str,
    evaluation_config: dict | None,
    baseline_evaluation_id: str | None,
    regression_thresholds: dict | None,
    search_config: dict | None,
) -> dict:
    evaluation_status = "planned" if evaluation_config is not None else "not_requested"
    regression_requested = baseline_evaluation_id is not None
    return {
        "schema_version": "1.0",
        "release_id": release_id,
        "tasks": {
            "evaluation": {
                "status": evaluation_status,
                "config": evaluation_config,
                "attempt_count": 0,
                "error": None,
            },
            "regression": {
                "status": "planned" if regression_requested else "not_requested",
                "baseline_evaluation_id": baseline_evaluation_id,
                "thresholds": regression_thresholds,
                "attempt_count": 0,
                "error": None,
            },
            "parameter_search": {
                "status": "planned" if search_config is not None else "not_requested",
                "config": search_config,
                "attempt_count": 0,
                "error": None,
            },
        },
    }


def _publish_correction_release_unlocked(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    release_id: str,
    reviewer: str,
    expected_revision: int,
    benchmark_split: str,
    license_name: str,
    evaluation_config: dict | None = None,
    baseline_evaluation_id: str | None = None,
    regression_thresholds: dict | None = None,
    search_config: dict | None = None,
) -> dict:
    """Freeze one reviewed revision into immutable release and feedback artifacts."""

    asset_id = validate_identifier(asset_id, "asset_id")
    session_id = validate_identifier(session_id, "session_id")
    release_id = validate_identifier(release_id, "release_id")
    if benchmark_split not in BENCHMARK_SPLITS:
        raise CorrectionError(
            "invalid_benchmark_split", f"Unsupported split: {benchmark_split}"
        )
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise CorrectionError(
            "invalid_reviewer", "reviewer must be a non-empty string."
        )
    if not isinstance(license_name, str) or not license_name.strip():
        raise CorrectionError(
            "invalid_license", "license_name must be a non-empty string."
        )
    if benchmark_split == "golden_regression" and search_config is not None:
        raise CorrectionError(
            "golden_search_forbidden",
            "Golden-regression data cannot be used for parameter search.",
        )
    if baseline_evaluation_id is not None and regression_thresholds is None:
        raise CorrectionError(
            "missing_regression_thresholds",
            "Regression comparison requires thresholds.",
        )
    if benchmark_split == "development" and evaluation_config is None:
        evaluation_config = {}
    release_dir = _release_dir(project_root, asset_id, release_id)
    derived_benchmark_id = validate_identifier(
        f"{release_id}-benchmark", "benchmark_id"
    )
    benchmark_dir = project_root / "benchmarks" / derived_benchmark_id
    feedback_dir = (
        project_root / "datasets" / "segmentation_feedback" / release_id
    )
    if release_dir.exists():
        raise CorrectionError("release_exists", f"Release already exists: {release_id}")
    if benchmark_dir.exists():
        raise CorrectionError(
            "derived_benchmark_exists",
            f"Derived benchmark already exists: {derived_benchmark_id}",
        )
    if feedback_dir.exists():
        raise CorrectionError(
            "feedback_release_exists",
            f"Feedback dataset already exists: {release_id}",
        )
    session = load_correction_session(project_root, asset_id, session_id)
    if session.get("status") != "in_review":
        raise CorrectionError(
            "invalid_session_state", "Only an in_review session can be published."
        )
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision != session.get("revision")
    ):
        raise CorrectionError(
            "stale_revision",
            f"Expected revision {expected_revision}, current revision is {session.get('revision')}.",
        )
    session_dir = _session_dir(project_root, asset_id, session_id)
    baseline = load_correction_baseline(project_root, asset_id, session_id)
    persisted_draft = _read_json(
        session_dir / "draft_labels.json", "session_not_found"
    )
    correction_events = read_correction_events(
        project_root, asset_id, session_id
    )
    draft = materialize_correction(baseline, correction_events)
    if (
        draft["draft_fingerprint"] != persisted_draft.get("draft_fingerprint")
        or draft["draft_fingerprint"] != session.get("draft_fingerprint")
    ):
        raise CorrectionError(
            "draft_replay_mismatch",
            "Persisted draft does not match deterministic event replay.",
        )
    indices = [item["source_point_index"] for item in draft["assignments"]]
    if len(indices) != len(set(indices)) or sorted(indices) != list(range(len(indices))):
        raise CorrectionError(
            "invalid_draft_indices",
            "Draft must contain each source point index exactly once.",
        )
    objects = _object_document(draft["assignments"])
    if draft.get("point_count") != baseline.get("point_count"):
        raise CorrectionError(
            "incomplete_draft", "Draft must cover the complete correction point set."
        )
    labels = _labels_from_assignments(draft, objects)
    before_labels = _labels_from_assignments(
        baseline, _object_document(baseline["assignments"])
    )
    run = _read_json(
        project_root
        / "reports"
        / "segmentation_runs"
        / asset_id
        / str(session["segmentation_run_id"])
        / "segmentation_run.json",
        "segmentation_run_not_found",
    )
    if baseline.get("source_fingerprint") != run.get("source_fingerprint"):
        raise CorrectionError(
            "source_fingerprint_mismatch",
            "Correction baseline source does not match the segmentation run.",
        )
    source_path = Path(str(run["source_uri"]))
    if not source_path.is_absolute() and not source_path.exists():
        source_path = project_root / source_path
    current_points = sample_points_from_source(
        source_path,
        max_points=int(
            run.get("config", {}).get(
                "max_points", run.get("source_point_count", 10_000)
            )
        ),
    )
    if fingerprint_points(current_points) != run.get("source_fingerprint"):
        raise CorrectionError(
            "source_fingerprint_mismatch",
            "Current source points no longer match the reviewed run.",
        )
    now = _iso_now()
    training_policy = _training_policy(
        release_id=release_id,
        benchmark_split=benchmark_split,
        license_name=license_name.strip(),
    )
    tasks = _task_document(
        release_id=release_id,
        evaluation_config=evaluation_config,
        baseline_evaluation_id=baseline_evaluation_id,
        regression_thresholds=regression_thresholds,
        search_config=search_config,
    )
    correction_diff = build_correction_diff(baseline, draft)
    provenance = {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "asset_version": run.get("asset_version"),
        "source_uri": run.get("source_uri"),
        "source_fingerprint": run.get("source_fingerprint"),
        "segmentation_run_id": session["segmentation_run_id"],
        "segmentation_config_fingerprint": run.get("config_fingerprint"),
        "segmentation_engine": run.get("executed_engine"),
        "editor": session.get("active_editor"),
        "session_id": session_id,
        "source_revision": expected_revision,
        "reviewer": reviewer.strip(),
        "published_at": now,
    }
    release = {
        "schema_version": "1.0",
        "release_id": release_id,
        "asset_id": asset_id,
        "sample_id": session["sample_id"],
        "session_id": session_id,
        "source_revision": expected_revision,
        "status": "published",
        "reviewer": reviewer.strip(),
        "published_at": now,
        "benchmark_split": benchmark_split,
        "license": license_name.strip(),
        "derived_benchmark_id": derived_benchmark_id,
        "supersedes_release_id": session.get("supersedes_release_id"),
        "source_fingerprint": run.get("source_fingerprint"),
        "artifacts": {
            "labels": "labels.json",
            "objects": "objects.json",
            "correction_diff": "correction_diff.json",
            "provenance": "provenance.json",
            "publication_tasks": "publication_tasks.json",
            "training_policy": "training_policy.json",
        },
    }
    benchmark_manifest = {
        "schema_version": "1.0",
        "benchmark_id": derived_benchmark_id,
        "benchmark_version": release_id,
        "split": benchmark_split,
        "scene_type": "corrected-segmentation",
        "point_density": max(float(draft["point_count"]), 1.0),
        "coordinate_unit": "m",
        "label_version": release_id,
        "license": license_name.strip(),
        "samples": [
            {
                "sample_id": session["sample_id"],
                "asset_id": asset_id,
                "asset_version": str(run.get("asset_version", "unknown")),
                "source_uri": str(run["source_uri"]),
                "source_fingerprint": str(run["source_fingerprint"]),
                "labels_path": f"samples/{session['sample_id']}/labels.json",
                "labels_format": "json",
            }
        ],
    }
    public_events = []
    for event in correction_events:
        public_events.append(
            {key: value for key, value in event.items() if key != "accepted_response"}
        )
    feedback_manifest = {
        "schema_version": "1.0",
        "release_id": release_id,
        "asset_id": asset_id,
        "session_id": session_id,
        "source_revision": expected_revision,
        "parent_release_id": session.get("supersedes_release_id"),
        "segmentation_run_id": session["segmentation_run_id"],
        "segmentation_config_fingerprint": run.get("config_fingerprint"),
        "editor": session.get("active_editor"),
        "reviewer": reviewer.strip(),
        "correction_diff": correction_diff,
        "source_fingerprint": run.get("source_fingerprint"),
        "benchmark_split": benchmark_split,
        "license": license_name.strip(),
        "training_eligibility": training_policy["eligibility"],
        "artifacts": {
            "before": "before_labels.json",
            "after": "after_labels.json",
            "operations": "operations.jsonl",
        },
    }
    token = uuid.uuid4().hex[:8]
    release_stage = release_dir.with_name(f".p14r-{token}")
    benchmark_stage = benchmark_dir.with_name(f".p14b-{token}")
    feedback_stage = feedback_dir.with_name(f".p14f-{token}")
    stages = [release_stage, benchmark_stage, feedback_stage]
    finalized: list[Path] = []
    try:
        write_json(release, release_stage / "correction_release.json")
        write_json(labels, release_stage / "labels.json")
        write_json(objects, release_stage / "objects.json")
        write_json(correction_diff, release_stage / "correction_diff.json")
        write_json(provenance, release_stage / "provenance.json")
        write_json(tasks, release_stage / "publication_tasks.json")
        write_json(training_policy, release_stage / "training_policy.json")

        write_json(benchmark_manifest, benchmark_stage / "benchmark.json")
        write_json(
            labels,
            benchmark_stage
            / "samples"
            / session["sample_id"]
            / "labels.json",
        )

        write_json(feedback_manifest, feedback_stage / "feedback_manifest.json")
        write_json(before_labels, feedback_stage / "before_labels.json")
        write_json(labels, feedback_stage / "after_labels.json")
        operations_path = feedback_stage / "operations.jsonl"
        operations_path.parent.mkdir(parents=True, exist_ok=True)
        operations_path.write_text(
            "".join(
                json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                for event in public_events
            ),
            encoding="utf-8",
        )

        for stage, destination in (
            (release_stage, release_dir),
            (benchmark_stage, benchmark_dir),
            (feedback_stage, feedback_dir),
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, destination)
            finalized.append(destination)
    except Exception:
        for stage in stages:
            if stage.exists():
                shutil.rmtree(stage)
        for destination in reversed(finalized):
            if destination.exists():
                shutil.rmtree(destination)
        raise
    updated_session = {
        **session,
        "status": "published",
        "revision": int(session["revision"]) + 1,
        "updated_at": now,
        "published_release_id": release_id,
        "published_revision": expected_revision,
    }
    try:
        write_json(updated_session, session_dir / "correction_session.json")
    except Exception:
        for destination in (release_dir, benchmark_dir, feedback_dir):
            if destination.exists():
                shutil.rmtree(destination)
        raise
    (
        session_dir.parent / f".active-{session['sample_id']}.lock"
    ).unlink(missing_ok=True)
    return release


def publish_correction_release(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    release_id: str,
    reviewer: str,
    expected_revision: int,
    benchmark_split: str,
    license_name: str,
    evaluation_config: dict | None = None,
    baseline_evaluation_id: str | None = None,
    regression_thresholds: dict | None = None,
    search_config: dict | None = None,
) -> dict:
    lock_path = _acquire_session_write_lock(
        project_root, asset_id, session_id
    )
    try:
        release = _publish_correction_release_unlocked(
            project_root,
            asset_id=asset_id,
            session_id=session_id,
            release_id=release_id,
            reviewer=reviewer,
            expected_revision=expected_revision,
            benchmark_split=benchmark_split,
            license_name=license_name,
            evaluation_config=evaluation_config,
            baseline_evaluation_id=baseline_evaluation_id,
            regression_thresholds=regression_thresholds,
            search_config=search_config,
        )
    finally:
        _release_session_write_lock(lock_path)
    retry_publication_tasks(
        project_root,
        asset_id=asset_id,
        release_id=release_id,
        actor=reviewer.strip(),
    )
    return release


def load_correction_release(
    project_root: Path, asset_id: str, release_id: str
) -> dict:
    return _read_json(
        _release_dir(project_root, asset_id, release_id)
        / "correction_release.json",
        "release_not_found",
    )


def list_correction_releases(project_root: Path, asset_id: str) -> list[dict]:
    root = _release_root(project_root, asset_id)
    releases = []
    if root.exists():
        for path in sorted(
            root.glob("*/correction_release.json"), key=lambda item: item.parent.name
        ):
            releases.append(_read_json(path, "invalid_release_artifact"))
    return releases


def retry_publication_tasks(
    project_root: Path, *, asset_id: str, release_id: str, actor: str
) -> dict:
    """Execute planned or failed downstream tasks without republishing labels."""

    if not isinstance(actor, str) or not actor.strip():
        raise CorrectionError("invalid_actor", "actor must be a non-empty string.")
    root = _release_dir(project_root, asset_id, release_id)
    release = load_correction_release(project_root, asset_id, release_id)
    tasks = _read_json(root / "publication_tasks.json", "release_not_found")
    tasks["last_retry_requested_by"] = actor.strip()
    tasks["last_retry_requested_at"] = _iso_now()
    write_json(tasks, root / "publication_tasks.json")

    evaluation_task = tasks["tasks"]["evaluation"]
    if evaluation_task["status"] in {"planned", "failed"}:
        attempt = int(evaluation_task.get("attempt_count", 0)) + 1
        evaluation_id = validate_identifier(
            f"{release_id}-eval-{attempt:04d}", "evaluation_id"
        )
        evaluation_task.update(
            {
                "status": "running",
                "attempt_count": attempt,
                "evaluation_id": evaluation_id,
                "error": None,
            }
        )
        write_json(tasks, root / "publication_tasks.json")
        try:
            evaluate_segmentation_run(
                project_root,
                asset_id=release["asset_id"],
                run_id=_read_json(
                    root / "provenance.json", "invalid_release_artifact"
                )["segmentation_run_id"],
                benchmark_id=release["derived_benchmark_id"],
                sample_id=release["sample_id"],
                evaluation_id=evaluation_id,
                config=dict(evaluation_task.get("config") or {}),
            )
            evaluation_task["status"] = "completed"
            evaluation_task["completed_at"] = _iso_now()
        except Exception as exc:
            evaluation_task["status"] = "failed"
            evaluation_task["error"] = {
                "code": "evaluation_task_failed",
                "message": str(exc),
            }
        write_json(tasks, root / "publication_tasks.json")

    regression_task = tasks["tasks"]["regression"]
    if regression_task["status"] in {"planned", "failed"}:
        attempt = int(regression_task.get("attempt_count", 0)) + 1
        comparison_id = validate_identifier(
            f"{release_id}-cmp-{attempt:04d}", "comparison_id"
        )
        regression_task.update(
            {
                "status": "running",
                "attempt_count": attempt,
                "comparison_id": comparison_id,
                "error": None,
            }
        )
        write_json(tasks, root / "publication_tasks.json")
        try:
            candidate_evaluation_id = evaluation_task.get("evaluation_id")
            if evaluation_task.get("status") != "completed" or not candidate_evaluation_id:
                raise ValueError("Regression requires a completed release evaluation.")
            compare_evaluations(
                project_root,
                asset_id=release["asset_id"],
                comparison_id=comparison_id,
                baseline_evaluation_id=regression_task["baseline_evaluation_id"],
                candidate_evaluation_id=candidate_evaluation_id,
                thresholds=dict(regression_task.get("thresholds") or {}),
            )
            regression_task["status"] = "completed"
            regression_task["completed_at"] = _iso_now()
        except Exception as exc:
            regression_task["status"] = "failed"
            regression_task["error"] = {
                "code": "regression_task_failed",
                "message": str(exc),
            }
        write_json(tasks, root / "publication_tasks.json")

    search_task = tasks["tasks"]["parameter_search"]
    if search_task["status"] in {"planned", "failed"}:
        if release["benchmark_split"] == "golden_regression":
            search_task["status"] = "failed"
            search_task["error"] = {
                "code": "golden_search_forbidden",
                "message": "Golden-regression data cannot be used for parameter search.",
            }
            write_json(tasks, root / "publication_tasks.json")
            return tasks
        attempt = int(search_task.get("attempt_count", 0)) + 1
        search_id = validate_identifier(
            f"{release_id}-search-{attempt:04d}", "search_id"
        )
        search_task.update(
            {
                "status": "running",
                "attempt_count": attempt,
                "search_id": search_id,
                "error": None,
            }
        )
        write_json(tasks, root / "publication_tasks.json")
        config_path = (
            project_root
            / "reports"
            / "segmentation_correction_tasks"
            / release["asset_id"]
            / release_id
            / f"search-{attempt:04d}.json"
        )
        try:
            write_json(dict(search_task.get("config") or {}), config_path)
            from pc_system.commands.phase13b import run_search_segmentation

            run_search_segmentation(
                project_root,
                asset_id=release["asset_id"],
                benchmark_id=release["derived_benchmark_id"],
                sample_id=release["sample_id"],
                search_id=search_id,
                config_path=config_path,
                baseline_evaluation_id=None,
            )
            search_task["status"] = "completed"
            search_task["completed_at"] = _iso_now()
        except Exception as exc:
            search_task["status"] = "failed"
            search_task["error"] = {
                "code": "parameter_search_task_failed",
                "message": str(exc),
            }
        write_json(tasks, root / "publication_tasks.json")
    policy = _read_json(root / "training_policy.json", "release_not_found")
    if release["benchmark_split"] == "development":
        compatible_licenses = {"internal", "cc0", "cc-by", "cc-by-4.0"}
        if (
            str(release["license"]).lower() in compatible_licenses
            and evaluation_task.get("status") == "completed"
        ):
            policy["eligibility"] = "eligible"
            policy["reasons"] = [
                "reviewed_development_labels",
                "compatible_license",
                "evaluation_completed",
            ]
        else:
            policy["eligibility"] = "blocked"
            policy["reasons"] = [
                "incompatible_license"
                if str(release["license"]).lower() not in compatible_licenses
                else "evaluation_not_completed"
            ]
        write_json(policy, root / "training_policy.json")
        feedback_path = (
            project_root
            / "datasets"
            / "segmentation_feedback"
            / release_id
            / "feedback_manifest.json"
        )
        if feedback_path.is_file():
            feedback = _read_json(
                feedback_path, "invalid_release_artifact"
            )
            feedback["training_eligibility"] = policy["eligibility"]
            feedback["training_policy_reasons"] = policy["reasons"]
            write_json(feedback, feedback_path)
    return tasks
