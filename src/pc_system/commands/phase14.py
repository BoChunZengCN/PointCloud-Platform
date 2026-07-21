import json
from pathlib import Path

from pc_system.segmentation_correction_events import apply_correction_event
from pc_system.segmentation_correction_releases import (
    publish_correction_release,
    retry_publication_tasks,
    transition_correction_session,
)
from pc_system.segmentation_corrections import create_correction_session


def _load_json_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Configuration must be a JSON object: {path}")
    return payload


def run_create_segmentation_correction(
    project_root: Path,
    *,
    asset_id: str,
    run_id: str,
    session_id: str,
    sample_id: str,
    actor: str,
    benchmark_id: str | None,
    baseline_release_id: str | None,
) -> int:
    create_correction_session(
        project_root,
        asset_id=asset_id,
        run_id=run_id,
        session_id=session_id,
        sample_id=sample_id,
        actor=actor,
        benchmark_id=benchmark_id,
        baseline_release_id=baseline_release_id,
    )
    print(
        project_root
        / "reports"
        / "segmentation_corrections"
        / asset_id
        / session_id
        / "correction_session.json"
    )
    return 0


def run_apply_segmentation_correction(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    actor: str,
    expected_revision: int,
    client_request_id: str,
    operation_path: Path,
) -> int:
    apply_correction_event(
        project_root,
        asset_id=asset_id,
        session_id=session_id,
        actor=actor,
        expected_revision=expected_revision,
        client_request_id=client_request_id,
        operation=_load_json_object(operation_path),
    )
    print(
        project_root
        / "reports"
        / "segmentation_corrections"
        / asset_id
        / session_id
        / "correction_session.json"
    )
    return 0


def run_submit_segmentation_correction(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    actor: str,
    expected_revision: int,
) -> int:
    transition_correction_session(
        project_root,
        asset_id=asset_id,
        session_id=session_id,
        action="submit",
        actor=actor,
        expected_revision=expected_revision,
    )
    print(
        project_root
        / "reports"
        / "segmentation_corrections"
        / asset_id
        / session_id
        / "correction_session.json"
    )
    return 0


def run_publish_segmentation_correction(
    project_root: Path,
    *,
    asset_id: str,
    session_id: str,
    publication_path: Path,
) -> int:
    publication = _load_json_object(publication_path)
    release = publish_correction_release(
        project_root,
        asset_id=asset_id,
        session_id=session_id,
        release_id=publication.get("release_id"),
        reviewer=publication.get("reviewer"),
        expected_revision=publication.get("expected_revision"),
        benchmark_split=publication.get("benchmark_split"),
        license_name=publication.get("license"),
        evaluation_config=publication.get("evaluation_config"),
        baseline_evaluation_id=publication.get("baseline_evaluation_id"),
        regression_thresholds=publication.get("regression_thresholds"),
        search_config=publication.get("search_config"),
    )
    print(
        project_root
        / "reports"
        / "segmentation_correction_releases"
        / asset_id
        / release["release_id"]
        / "correction_release.json"
    )
    return 0


def run_retry_segmentation_publication(
    project_root: Path,
    *,
    asset_id: str,
    release_id: str,
    actor: str,
) -> int:
    retry_publication_tasks(
        project_root,
        asset_id=asset_id,
        release_id=release_id,
        actor=actor,
    )
    print(
        project_root
        / "reports"
        / "segmentation_correction_releases"
        / asset_id
        / release_id
        / "publication_tasks.json"
    )
    return 0
