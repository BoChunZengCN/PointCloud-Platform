import json

import pytest

import pc_system.model_feature_store as feature_store
from pc_system.model_feature_store import (
    list_features,
    load_feature,
    publish_model_feature,
    publish_object_feature,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_retrieval_config import publish_retrieval_config
from pc_system.segmentation_service import run_segmentation
from phase15b2_support import (
    EXPERT,
    FEATURE_V1,
    MAPPING_V1,
    SCORING_V1,
    prepare_released_models,
)


class SimulatedInterruption(BaseException):
    pass


def _prepare(project):
    publish_retrieval_config(
        project,
        config_id="retrieval-v1",
        feature=FEATURE_V1,
        scoring=SCORING_V1,
        category_mapping=MAPPING_V1,
        principal=EXPERT,
        operation_id="op-config-feature",
        request_id="req-config-feature",
        idempotency_key="idem-config-feature",
    )
    return prepare_released_models(project)


def _model_request(prepared, *, sequence=1):
    return {
        "model_id": "pump-a",
        "version_id": "v2",
        "representation_id": prepared["pump_v2_representation"]["representation_id"],
        "config_id": "retrieval-v1",
        "principal": EXPERT,
        "operation_id": f"op-model-feature-{sequence:03d}",
        "request_id": f"req-model-feature-{sequence:03d}",
        "idempotency_key": f"idem-model-feature-{sequence:03d}",
    }


def _model_identity(feature, prepared):
    return {
        "model_id": "pump-a",
        "version_id": "v2",
        "representation_id": prepared["pump_v2_representation"]["representation_id"],
        "feature_id": feature["feature_id"],
    }


def _prepare_experimental_object(project):
    points = [
        {"x": x, "y": y, "z": z}
        for x in (0.0, 0.6, 1.2, 1.8)
        for y in (0.0, 1.0)
        for z in (0.0, 0.5)
    ]
    run_segmentation(
        project,
        asset_id="scan-feature",
        asset_version="v1",
        source_uri="scan-feature.points.json",
        points=points,
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 3.0,
            "min_points": 1,
            "max_points": 100,
        },
        run_id="run-feature-001",
    )


def test_model_and_object_features_are_published_and_verified(tmp_path):
    prepared = _prepare(tmp_path)
    model = publish_model_feature(tmp_path, **_model_request(prepared))
    _prepare_experimental_object(tmp_path)
    obj = publish_object_feature(
        tmp_path,
        source_kind="segmentation_run",
        asset_id="scan-feature",
        source_id="run-feature-001",
        instance_id="obj-001",
        config_id="retrieval-v1",
        principal=EXPERT,
        operation_id="op-object-feature-001",
        request_id="req-object-feature-001",
        idempotency_key="idem-object-feature-001",
    )

    assert model["feature_type"] == "model"
    assert model["status"] == "ready"
    assert model["features"]["point_count"] == 16
    assert load_feature(
        tmp_path, feature_type="model", identity=_model_identity(model, prepared)
    ) == model
    assert list_features(
        tmp_path,
        feature_type="model",
        identity={key: value for key, value in _model_identity(model, prepared).items() if key != "feature_id"},
    ) == [model]
    assert obj["feature_type"] == "object"
    assert obj["source"]["category_trust"] == "algorithm_only"
    assert obj["features"]["point_count"] == 16


def test_same_operation_replays_and_new_operation_reuses_model_feature(tmp_path):
    prepared = _prepare(tmp_path)
    first = publish_model_feature(tmp_path, **_model_request(prepared, sequence=1))
    replayed = publish_model_feature(tmp_path, **_model_request(prepared, sequence=1))
    reused = publish_model_feature(tmp_path, **_model_request(prepared, sequence=2))

    assert replayed == first
    assert reused == first
    assert len(
        list_features(
            tmp_path,
            feature_type="model",
            identity={key: value for key, value in _model_identity(first, prepared).items() if key != "feature_id"},
        )
    ) == 1


def test_feature_manifest_is_hidden_until_audit_completion_and_recovers(tmp_path, monkeypatch):
    prepared = _prepare(tmp_path)
    original = feature_store._publish_exact_json

    def interrupt_after_write(path, value, **kwargs):
        result = original(path, value, **kwargs)
        if path.name == "feature.json":
            raise SimulatedInterruption()
        return result

    monkeypatch.setattr(feature_store, "_publish_exact_json", interrupt_after_write)
    with pytest.raises(SimulatedInterruption):
        publish_model_feature(tmp_path, **_model_request(prepared))
    assert list_features(
        tmp_path,
        feature_type="model",
        identity={
            "model_id": "pump-a",
            "version_id": "v2",
            "representation_id": prepared["pump_v2_representation"]["representation_id"],
        },
    ) == []

    monkeypatch.setattr(feature_store, "_publish_exact_json", original)
    recovered = publish_model_feature(tmp_path, **_model_request(prepared))
    assert recovered["status"] == "ready"


def test_foreign_incomplete_feature_candidate_is_busy(tmp_path, monkeypatch):
    prepared = _prepare(tmp_path)
    original = feature_store._publish_exact_json

    def interrupt_after_owner(path, value, **kwargs):
        result = original(path, value, **kwargs)
        if path.name == "operation_owner.json":
            raise SimulatedInterruption()
        return result

    monkeypatch.setattr(feature_store, "_publish_exact_json", interrupt_after_owner)
    with pytest.raises(SimulatedInterruption):
        publish_model_feature(tmp_path, **_model_request(prepared, sequence=1))
    monkeypatch.setattr(feature_store, "_publish_exact_json", original)

    with pytest.raises(ModelMatchingError) as error:
        publish_model_feature(tmp_path, **_model_request(prepared, sequence=2))

    assert error.value.code == "operation_busy"


def test_source_or_owner_tampering_invalidates_feature(tmp_path):
    prepared = _prepare(tmp_path)
    feature = publish_model_feature(tmp_path, **_model_request(prepared))
    identity = _model_identity(feature, prepared)
    feature_root = (
        tmp_path
        / "models"
        / "pump-a"
        / "features"
        / "v2"
        / identity["representation_id"]
        / identity["feature_id"]
    )
    owner_path = feature_root / "operation_owner.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["operation_id"] = "op-tampered"
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as owner_error:
        load_feature(tmp_path, feature_type="model", identity=identity)
    assert owner_error.value.code == "feature_integrity_error"

    owner_path.write_text(json.dumps({**owner, "operation_id": feature["operation_id"]}), encoding="utf-8")
    points_path = (
        tmp_path
        / "models"
        / "pump-a"
        / "representations"
        / "v2"
        / "cad_sampled"
        / identity["representation_id"]
        / "sampled_points.json"
    )
    points = json.loads(points_path.read_text(encoding="utf-8"))
    points["points"][0][0] += 1.0
    points_path.write_text(json.dumps(points), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as source_error:
        load_feature(tmp_path, feature_type="model", identity=identity)
    assert source_error.value.code == "feature_integrity_error"
