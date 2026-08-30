import json

import pytest

from pc_system.model_feature_index import build_model_feature_index
from pc_system.model_index_release import release_model_feature_index
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_retrieval import (
    load_model_retrieval,
    retrieve_model_candidates,
    score_candidate,
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


FEATURES = {
    "observed_spans_m": [1.0, 1.0, 1.0],
    "span_ratios": [1.0, 1.0, 1.0],
    "observed_box_volume_m3": 1.0,
    "principal_value_ratios": [0.5, 0.3, 0.2],
    "radial_histogram": [0.5, 0.3, 0.2],
    "voxel_occupancy": 0.5,
    "point_count": 16,
    "quality": {"status": "usable", "reasons": []},
}


def _query(**overrides):
    value = {
        "category_id": "pump",
        "keyword_terms": ["centrifugal"],
        "tag_terms": ["industrial"],
        "manufacturer": "Acme",
        "model_number": "A-100",
        "features": FEATURES,
    }
    value.update(overrides)
    return value


def _candidate(**overrides):
    value = {
        "model_id": "pump-a",
        "version_id": "v1",
        "category_id": "pump",
        "keyword_terms": ["centrifugal"],
        "tag_terms": ["industrial"],
        "manufacturer": "Acme",
        "model_number": "A-100",
        "features": FEATURES,
    }
    value.update(overrides)
    return value


def test_smaller_model_is_penalized_more_than_larger_model():
    smaller = score_candidate(
        _query(),
        _candidate(features={**FEATURES, "observed_spans_m": [0.5, 0.5, 0.5]}),
        SCORING_V1,
    )
    larger = score_candidate(
        _query(),
        _candidate(features={**FEATURES, "observed_spans_m": [2.0, 2.0, 2.0]}),
        SCORING_V1,
    )

    assert smaller["components"]["dimensions"]["score"] < larger["components"]["dimensions"]["score"]


def test_score_components_are_explainable_and_missing_weights_are_renormalized():
    result = score_candidate(_query(), _candidate(), SCORING_V1)
    metadata_only = score_candidate(
        _query(features={**FEATURES, "quality": {"status": "metadata_only", "reasons": ["geometry_degenerate"]}}),
        _candidate(features={**FEATURES, "quality": {"status": "metadata_only", "reasons": ["geometry_degenerate"]}}),
        SCORING_V1,
    )

    assert result["score"] == 1.0
    assert set(result["components"]) == {
        "category", "terms", "manufacturer_model", "dimensions", "shape", "occupancy"
    }
    assert sum(result["effective_weights"].values()) == pytest.approx(
        1.0, abs=1e-12
    )
    assert metadata_only["risks"] == ["geometry_degenerate", "metadata_only"]
    assert set(metadata_only["effective_weights"]) == {"category", "terms", "manufacturer_model"}


def test_weighted_terms_shape_and_occupancy_scores_are_bounded():
    result = score_candidate(
        _query(keyword_terms=["pump"], tag_terms=["critical"]),
        _candidate(
            keyword_terms=["pump", "spare"],
            tag_terms=["critical", "indoor"],
            features={
                **FEATURES,
                "principal_value_ratios": [0.6, 0.3, 0.1],
                "radial_histogram": [0.4, 0.4, 0.2],
                "voxel_occupancy": 0.75,
            },
        ),
        SCORING_V1,
    )

    assert 0.0 < result["components"]["terms"]["score"] < 1.0
    assert result["components"]["shape"]["score"] == 0.9
    assert result["components"]["occupancy"]["score"] == 0.75


def _points():
    return [
        {"x": x, "y": y, "z": z}
        for x in (0.0, 0.6, 1.2, 1.8)
        for y in (0.0, 1.0)
        for z in (0.0, 0.5)
    ]


def _prepare_project(project, *, mapping=None):
    publish_retrieval_config(
        project,
        config_id="retrieval-v1",
        feature=FEATURE_V1,
        scoring=SCORING_V1,
        category_mapping=MAPPING_V1 if mapping is None else mapping,
        principal=EXPERT,
        operation_id="op-config-retrieval",
        request_id="req-config-retrieval",
        idempotency_key="idem-config-retrieval",
    )
    prepared = prepare_released_models(project)
    source_path = project / "scan-retrieval.points.json"
    source_path.write_text(json.dumps(_points()), encoding="utf-8")
    run_segmentation(
        project,
        asset_id="scan-retrieval",
        asset_version="v1",
        source_uri=str(source_path),
        points=_points(),
        config={"engine": "builtin_geometric", "distance_threshold": 3.0, "min_points": 1},
        run_id="run-retrieval-001",
    )
    session = create_correction_session(
        project,
        asset_id="scan-retrieval",
        run_id="run-retrieval-001",
        session_id="session-retrieval-001",
        sample_id="sample-retrieval-001",
        actor="alice",
    )
    relabeled = apply_correction_event(
        project,
        asset_id="scan-retrieval",
        session_id="session-retrieval-001",
        actor="alice",
        expected_revision=session["revision"],
        client_request_id="request-relabel-retrieval",
        operation={"type": "relabel", "instance_ids": ["obj-001"], "class_id": "centrifugal-pump"},
    )
    confirmed = apply_correction_event(
        project,
        asset_id="scan-retrieval",
        session_id="session-retrieval-001",
        actor="alice",
        expected_revision=relabeled["revision"],
        client_request_id="request-confirm-retrieval",
        operation={"type": "confirm", "instance_ids": ["obj-001"]},
    )
    reviewed = transition_correction_session(
        project,
        asset_id="scan-retrieval",
        session_id="session-retrieval-001",
        action="submit",
        actor="alice",
        expected_revision=confirmed["revision"],
    )
    publish_correction_release(
        project,
        asset_id="scan-retrieval",
        session_id="session-retrieval-001",
        release_id="release-retrieval-001",
        reviewer="bob",
        expected_revision=reviewed["revision"],
        benchmark_split="development",
        license_name="internal",
    )
    index = build_model_feature_index(
        project,
        index_id="index-production-001",
        index_mode="production",
        config_id="retrieval-v1",
        historical_releases=None,
        principal=EXPERT,
        operation_id="op-index-retrieval",
        request_id="req-index-retrieval",
        idempotency_key="idem-index-retrieval",
        mesh_reader=_mesh_reader,
    )
    release = release_model_feature_index(
        project,
        index_id=index["index_id"],
        release_id="index-release-retrieval-001",
        action="activate",
        expected_current_release_id=None,
        rollback_of_release_id=None,
        reason="Retrieval production index",
        principal=EXPERT,
        operation_id="op-index-release-retrieval",
        request_id="req-index-release-retrieval",
        idempotency_key="idem-index-release-retrieval",
    )
    return prepared, release


def _retrieve(project, *, sequence=1, **overrides):
    values = {
        "retrieval_run_id": f"retrieval-{sequence:03d}",
        "source_kind": "correction_release",
        "asset_id": "scan-retrieval",
        "source_id": "release-retrieval-001",
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
        "operation_id": f"op-retrieval-{sequence:03d}",
        "request_id": f"req-retrieval-{sequence:03d}",
        "idempotency_key": f"idem-retrieval-{sequence:03d}",
    }
    values.update(overrides)
    return retrieve_model_candidates(project, **values)


def test_production_retrieval_applies_confirmed_category_filter_and_is_replayable(tmp_path):
    _prepare_project(tmp_path)

    report = _retrieve(tmp_path)

    assert report["category_filter"]["applied"] is True
    assert report["category_filter"]["category_id"] == "pump"
    assert report["candidate_counts"] == {"before_filter": 2, "after_filter": 1, "scored": 1, "returned": 1}
    assert report["candidates"][0]["model_id"] == "pump-a"
    assert _retrieve(tmp_path) == report
    assert load_model_retrieval(
        tmp_path,
        asset_id="scan-retrieval",
        source_id="release-retrieval-001",
        instance_id="obj-001",
        retrieval_run_id="retrieval-001",
    ) == report
    report_path = (
        tmp_path
        / "reports"
        / "model_retrieval"
        / "scan-retrieval"
        / "release-retrieval-001"
        / "obj-001"
        / "retrieval-001"
        / "retrieval_report.json"
    )
    tampered = json.loads(report_path.read_text(encoding="utf-8"))
    tampered["report_fingerprint"] = "f" * 64
    report_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as error:
        load_model_retrieval(
            tmp_path,
            asset_id="scan-retrieval",
            source_id="release-retrieval-001",
            instance_id="obj-001",
            retrieval_run_id="retrieval-001",
        )
    assert error.value.code == "feature_integrity_error"


def test_category_filter_degrades_to_full_scan_when_category_has_no_candidates(tmp_path):
    _prepare_project(
        tmp_path,
        mapping={
            **MAPPING_V1,
            "mappings": {"centrifugal-pump": "missing-category"},
        },
    )

    report = _retrieve(tmp_path)

    assert report["category_filter"] == {
        "applied": False,
        "category_id": "missing-category",
        "degraded": True,
        "reason": "category_filter_empty",
    }
    assert report["candidate_counts"]["after_filter"] == 2
    assert [item["model_id"] for item in report["candidates"]] == ["pump-a", "valve-a"]


def test_hints_require_source_and_top_k_is_bounded(tmp_path):
    _prepare_project(tmp_path)

    with pytest.raises(ModelMatchingError) as hint_error:
        _retrieve(tmp_path, sequence=2, keywords=["pump"])
    assert hint_error.value.code == "invalid_retrieval_input"
    with pytest.raises(ModelMatchingError) as top_k_error:
        _retrieve(tmp_path, sequence=3, top_k=51)
    assert top_k_error.value.code == "invalid_retrieval_input"


def test_experimental_retrieval_requires_explicit_challenger_index(tmp_path):
    prepared, _release = _prepare_project(tmp_path)
    challenger = build_model_feature_index(
        tmp_path,
        index_id="index-challenger-001",
        index_mode="challenger",
        config_id="retrieval-v1",
        historical_releases=[
            {
                "model_id": "pump-a",
                "release_id": prepared["pump_v1_release"]["release_id"],
            }
        ],
        principal=EXPERT,
        operation_id="op-index-challenger-retrieval",
        request_id="req-index-challenger-retrieval",
        idempotency_key="idem-index-challenger-retrieval",
        mesh_reader=_mesh_reader,
    )

    report = _retrieve(
        tmp_path,
        sequence=4,
        retrieval_run_id="retrieval-experimental-001",
        source_kind="segmentation_run",
        source_id="run-retrieval-001",
        index_id=challenger["index_id"],
    )

    assert report["index_release_id"] is None
    assert report["index_id"] == challenger["index_id"]
    assert report["category_filter"]["reason"] == "category_filter_not_trusted"
