import json

import pytest

from pc_system.model_feature_index import (
    build_model_feature_index,
    load_model_feature_index,
    read_index_entries,
)
from pc_system.model_index_release import release_model_feature_index
from pc_system.model_matching_audit import (
    read_verified_operation_snapshot,
    verify_operation_chain,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_release import release_model_version
from pc_system.model_retrieval import (
    load_model_retrieval,
    retrieve_model_candidates,
)
from pc_system.model_retrieval_config import publish_retrieval_config
from pc_system.segmentation_correction_events import apply_correction_event
from pc_system.segmentation_correction_releases import (
    publish_correction_release,
    transition_correction_session,
)
from pc_system.segmentation_corrections import create_correction_session
from pc_system.segmentation_service import run_segmentation
from phase15b2_support import (
    EXPERT,
    FEATURE_V1,
    MAPPING_V1,
    SCORING_V1,
    _mesh_reader,
    prepare_released_models,
)


def _points(*, degenerate: bool = False) -> list[dict]:
    if degenerate:
        return [
            {"x": index / 100_000_000, "y": 0.0, "z": 0.0}
            for index in range(16)
        ]
    return [
        {"x": x, "y": y, "z": z}
        for x in (0.0, 0.6, 1.2, 1.8)
        for y in (0.0, 1.0)
        for z in (0.0, 0.5)
    ]


def _prepare_project(
    project,
    *,
    mapping: dict | None = None,
    class_id: str = "centrifugal-pump",
    degenerate: bool = False,
) -> dict:
    publish_retrieval_config(
        project,
        config_id="retrieval-v1",
        feature=FEATURE_V1,
        scoring=SCORING_V1,
        category_mapping=MAPPING_V1 if mapping is None else mapping,
        principal=EXPERT,
        operation_id="op-e2e-config",
        request_id="req-e2e-config",
        idempotency_key="idem-e2e-config",
    )
    prepared = prepare_released_models(project)
    points = _points(degenerate=degenerate)
    source_path = project / "scan-e2e.points.json"
    source_path.write_text(json.dumps(points), encoding="utf-8")
    run_segmentation(
        project,
        asset_id="scan-e2e",
        asset_version="v1",
        source_uri=str(source_path),
        points=points,
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 3.0,
            "min_points": 1,
        },
        run_id="run-e2e-001",
    )
    session = create_correction_session(
        project,
        asset_id="scan-e2e",
        run_id="run-e2e-001",
        session_id="session-e2e-001",
        sample_id="sample-e2e-001",
        actor="alice",
    )
    relabeled = apply_correction_event(
        project,
        asset_id="scan-e2e",
        session_id="session-e2e-001",
        actor="alice",
        expected_revision=session["revision"],
        client_request_id="request-e2e-relabel",
        operation={
            "type": "relabel",
            "instance_ids": ["obj-001"],
            "class_id": class_id,
        },
    )
    confirmed = apply_correction_event(
        project,
        asset_id="scan-e2e",
        session_id="session-e2e-001",
        actor="alice",
        expected_revision=relabeled["revision"],
        client_request_id="request-e2e-confirm",
        operation={"type": "confirm", "instance_ids": ["obj-001"]},
    )
    reviewed = transition_correction_session(
        project,
        asset_id="scan-e2e",
        session_id="session-e2e-001",
        action="submit",
        actor="alice",
        expected_revision=confirmed["revision"],
    )
    correction_release = publish_correction_release(
        project,
        asset_id="scan-e2e",
        session_id="session-e2e-001",
        release_id="release-e2e-001",
        reviewer="bob",
        expected_revision=reviewed["revision"],
        benchmark_split="development",
        license_name="internal",
    )
    index = build_model_feature_index(
        project,
        index_id="index-e2e-001",
        index_mode="production",
        config_id="retrieval-v1",
        historical_releases=None,
        principal=EXPERT,
        operation_id="op-e2e-index-001",
        request_id="req-e2e-index-001",
        idempotency_key="idem-e2e-index-001",
        mesh_reader=_mesh_reader,
    )
    index_release = release_model_feature_index(
        project,
        index_id=index["index_id"],
        release_id="index-release-e2e-001",
        action="activate",
        expected_current_release_id=None,
        rollback_of_release_id=None,
        reason="E2E production index",
        principal=EXPERT,
        operation_id="op-e2e-index-release-001",
        request_id="req-e2e-index-release-001",
        idempotency_key="idem-e2e-index-release-001",
    )
    return {
        **prepared,
        "correction_release": correction_release,
        "index": index,
        "index_release": index_release,
    }


def _retrieve(project, *, sequence: int = 1, **overrides) -> dict:
    values = {
        "retrieval_run_id": f"retrieval-e2e-{sequence:03d}",
        "source_kind": "correction_release",
        "asset_id": "scan-e2e",
        "source_id": "release-e2e-001",
        "instance_id": "obj-001",
        "index_release_id": None,
        "index_id": None,
        "top_k": 10,
        "keywords": [],
        "tags": [],
        "manufacturer": None,
        "model_number": None,
        "hint_source": None,
        "principal": EXPERT,
        "operation_id": f"op-e2e-retrieval-{sequence:03d}",
        "request_id": f"req-e2e-retrieval-{sequence:03d}",
        "idempotency_key": f"idem-e2e-retrieval-{sequence:03d}",
    }
    values.update(overrides)
    return retrieve_model_candidates(project, **values)


def _assert_all_operation_chains_verify(project) -> None:
    operation_root = project / "reports" / "model_matching_operations"
    operation_ids = sorted(path.name for path in operation_root.iterdir() if path.is_dir())
    assert operation_ids
    for operation_id in operation_ids:
        snapshot = read_verified_operation_snapshot(project, operation_id)
        assert verify_operation_chain(snapshot["events"]) is True


def test_published_object_to_explainable_top_k_and_index_rollback(tmp_path):
    prepared = _prepare_project(tmp_path)
    report = _retrieve(tmp_path)

    assert report["schema_version"] == "1.1"
    assert report["candidates"][0]["model_id"] == "pump-a"
    assert report["candidates"][0]["release_id"] == prepared["pump_v2_release"]["release_id"]
    assert (
        report["candidates"][0]["representation_id"]
        == prepared["pump_v2_representation"]["representation_id"]
    )
    indexed = next(
        entry
        for entry in read_index_entries(tmp_path, report["index_id"])
        if entry["model_id"] == "pump-a"
    )
    for field in (
        "release_id",
        "representation_id",
        "representation_fingerprint",
        "feature_id",
        "feature_vector_fingerprint",
    ):
        assert report["candidates"][0][field] == indexed[field]
    assert set(report["candidates"][0]["components"]) >= {
        "category",
        "dimensions",
        "shape",
    }

    second_index = build_model_feature_index(
        tmp_path,
        index_id="index-e2e-002",
        index_mode="production",
        config_id="retrieval-v1",
        historical_releases=None,
        principal=EXPERT,
        operation_id="op-e2e-index-002",
        request_id="req-e2e-index-002",
        idempotency_key="idem-e2e-index-002",
        mesh_reader=_mesh_reader,
    )
    second_release = release_model_feature_index(
        tmp_path,
        index_id=second_index["index_id"],
        release_id="index-release-e2e-002",
        action="activate",
        expected_current_release_id=prepared["index_release"]["release_id"],
        rollback_of_release_id=None,
        reason="E2E index upgrade",
        principal=EXPERT,
        operation_id="op-e2e-index-release-002",
        request_id="req-e2e-index-release-002",
        idempotency_key="idem-e2e-index-release-002",
    )
    release_model_feature_index(
        tmp_path,
        index_id=prepared["index"]["index_id"],
        release_id="index-release-e2e-003",
        action="rollback",
        expected_current_release_id=second_release["release_id"],
        rollback_of_release_id=prepared["index_release"]["release_id"],
        reason="E2E index rollback",
        principal=EXPERT,
        operation_id="op-e2e-index-release-003",
        request_id="req-e2e-index-release-003",
        idempotency_key="idem-e2e-index-release-003",
    )

    assert load_model_retrieval(
        tmp_path,
        asset_id="scan-e2e",
        source_id="release-e2e-001",
        instance_id="obj-001",
        retrieval_run_id=report["retrieval_run_id"],
    ) == report
    _assert_all_operation_chains_verify(tmp_path)


def test_legacy_phase14_release_uses_soft_scoring(tmp_path):
    _prepare_project(tmp_path)
    release_root = (
        tmp_path
        / "reports"
        / "segmentation_correction_releases"
        / "scan-e2e"
        / "release-e2e-001"
    )
    release_path = release_root / "correction_release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["artifacts"].pop("object_review_evidence")
    release_path.write_text(json.dumps(release), encoding="utf-8")
    (release_root / "object_review_evidence.json").unlink()

    report = _retrieve(tmp_path)

    assert report["category_filter"] == {
        "applied": False,
        "category_id": "pump",
        "degraded": True,
        "reason": "category_filter_not_trusted",
    }
    assert report["candidate_counts"]["after_filter"] == 2


def test_phase13a_input_can_use_explicit_challenger_index(tmp_path):
    prepared = _prepare_project(tmp_path)
    challenger = build_model_feature_index(
        tmp_path,
        index_id="index-e2e-challenger",
        index_mode="challenger",
        config_id="retrieval-v1",
        historical_releases=[
            {
                "model_id": "pump-a",
                "release_id": prepared["pump_v1_release"]["release_id"],
            }
        ],
        principal=EXPERT,
        operation_id="op-e2e-index-challenger",
        request_id="req-e2e-index-challenger",
        idempotency_key="idem-e2e-index-challenger",
        mesh_reader=_mesh_reader,
    )

    report = _retrieve(
        tmp_path,
        sequence=2,
        source_kind="segmentation_run",
        source_id="run-e2e-001",
        index_id=challenger["index_id"],
    )

    assert report["index_release_id"] is None
    assert report["index_id"] == challenger["index_id"]
    assert report["candidates"][0]["version_id"] == "v1"


def test_production_and_experimental_sources_cannot_cross_index_modes(tmp_path):
    prepared = _prepare_project(tmp_path)
    challenger = build_model_feature_index(
        tmp_path,
        index_id="index-e2e-boundary-challenger",
        index_mode="challenger",
        config_id="retrieval-v1",
        historical_releases=[
            {
                "model_id": "pump-a",
                "release_id": prepared["pump_v1_release"]["release_id"],
            }
        ],
        principal=EXPERT,
        operation_id="op-e2e-index-boundary-challenger",
        request_id="req-e2e-index-boundary-challenger",
        idempotency_key="idem-e2e-index-boundary-challenger",
        mesh_reader=_mesh_reader,
    )

    with pytest.raises(ModelMatchingError) as production_error:
        _retrieve(tmp_path, sequence=2, index_id=challenger["index_id"])
    with pytest.raises(ModelMatchingError) as experimental_error:
        _retrieve(
            tmp_path,
            sequence=3,
            source_kind="segmentation_run",
            source_id="run-e2e-001",
        )

    assert production_error.value.code == "invalid_retrieval_input"
    assert experimental_error.value.code == "invalid_retrieval_input"


def test_model_head_change_marks_production_index_stale(tmp_path):
    prepared = _prepare_project(tmp_path)
    release_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        release_id="release-pump-e2e-rollback",
        action="rollback",
        expected_current_release_id=prepared["pump_v2_release"]["release_id"],
        rollback_of_release_id=prepared["pump_v1_release"]["release_id"],
        reason="E2E model rollback",
        principal=EXPERT,
        operation_id="op-e2e-model-rollback",
        request_id="req-e2e-model-rollback",
        idempotency_key="idem-e2e-model-rollback",
    )

    with pytest.raises(ModelMatchingError) as error:
        load_model_feature_index(
            tmp_path,
            prepared["index"]["index_id"],
            require_current_heads=True,
        )

    assert error.value.code == "model_index_stale"


def test_no_candidate_error_is_stable_on_replay(tmp_path):
    _prepare_project(
        tmp_path,
        mapping={**MAPPING_V1, "mappings": {}},
        class_id="unknown-object",
        degenerate=True,
    )

    with pytest.raises(ModelMatchingError) as first:
        _retrieve(tmp_path)
    with pytest.raises(ModelMatchingError) as replay:
        _retrieve(tmp_path)

    assert first.value.code == "no_candidate_models"
    assert replay.value.code == "no_candidate_models"
    snapshot = read_verified_operation_snapshot(tmp_path, "op-e2e-retrieval-001")
    assert snapshot["operation"]["status"] == "failed"
    assert verify_operation_chain(snapshot["events"]) is True
