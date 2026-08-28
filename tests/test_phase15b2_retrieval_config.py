import json

import pytest

from pc_system.model_matching_audit import read_verified_operation_snapshot
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_retrieval_config import (
    build_retrieval_config,
    list_retrieval_configs,
    load_retrieval_config,
    publish_retrieval_config,
)
from phase15b2_support import AUDITOR, EXPERT, FEATURE_V1, MAPPING_V1, SCORING_V1


def _publish(root, *, sequence=1, feature=None, principal=EXPERT):
    return publish_retrieval_config(
        root,
        config_id="retrieval-v1",
        feature=FEATURE_V1 if feature is None else feature,
        scoring=SCORING_V1,
        category_mapping=MAPPING_V1,
        principal=principal,
        operation_id=f"op-config-{sequence:03d}",
        request_id=f"req-config-{sequence:03d}",
        idempotency_key=f"idem-config-{sequence:03d}",
    )


def test_build_config_is_canonical_and_order_independent():
    first = build_retrieval_config(FEATURE_V1, SCORING_V1, MAPPING_V1)
    second = build_retrieval_config(
        dict(reversed(list(FEATURE_V1.items()))),
        dict(reversed(list(SCORING_V1.items()))),
        dict(reversed(list(MAPPING_V1.items()))),
    )

    assert second == first
    assert first["config_id"] == "retrieval-v1"
    assert len(first["config_fingerprint"]) == 64
    assert sum(first["scoring_config"]["weights"].values()) == 1.0


def test_config_rejects_unknown_fields_before_non_finite_weight():
    with pytest.raises(ModelMatchingError) as error:
        build_retrieval_config(
            {**FEATURE_V1, "extra": True},
            {
                **SCORING_V1,
                "weights": {**SCORING_V1["weights"], "shape": float("nan")},
            },
            MAPPING_V1,
        )

    assert error.value.code == "feature_config_invalid"


@pytest.mark.parametrize(
    "feature",
    [
        {**FEATURE_V1, "radial_bins": True},
        {**FEATURE_V1, "minimum_points": 15},
        {
            **FEATURE_V1,
            "sampling": {**FEATURE_V1["sampling"], "point_count": 500_001},
        },
        {**FEATURE_V1, "maximum_points": 2_000_001},
    ],
)
def test_feature_config_bounds_are_strict(feature):
    with pytest.raises(ModelMatchingError) as error:
        build_retrieval_config(feature, SCORING_V1, MAPPING_V1)

    assert error.value.code == "feature_config_invalid"


@pytest.mark.parametrize(
    "scoring",
    [
        {
            **SCORING_V1,
            "weights": {**SCORING_V1["weights"], "shape": float("inf")},
        },
        {
            **SCORING_V1,
            "weights": {**SCORING_V1["weights"], "shape": 0.21},
        },
        {**SCORING_V1, "top_k_maximum": 51},
        {**SCORING_V1, "production_minimum_coverage": 1.1},
    ],
)
def test_scoring_config_values_are_strict(scoring):
    with pytest.raises(ModelMatchingError) as error:
        build_retrieval_config(FEATURE_V1, scoring, MAPPING_V1)

    assert error.value.code == "feature_config_invalid"


def test_category_mapping_normalization_collision_is_rejected():
    mapping = {
        **MAPPING_V1,
        "mappings": {"pump": "pump", "ｐｕｍｐ": "pump"},
    }

    with pytest.raises(ModelMatchingError) as error:
        build_retrieval_config(FEATURE_V1, SCORING_V1, mapping)

    assert error.value.code == "feature_config_invalid"


def test_publish_config_writes_verified_immutable_files_and_replays(tmp_path):
    first = _publish(tmp_path)
    replayed = _publish(tmp_path)
    root = tmp_path / "models" / "retrieval_configs" / "retrieval-v1"

    assert replayed == first
    assert set(path.name for path in root.iterdir()) == {
        "operation_owner.json",
        "feature_config.json",
        "scoring_config.json",
        "category_mapping.json",
        "retrieval_config.json",
    }
    assert load_retrieval_config(tmp_path, "retrieval-v1") == first
    assert list_retrieval_configs(tmp_path) == [first]
    snapshot = read_verified_operation_snapshot(tmp_path, "op-config-001")
    assert snapshot["operation"]["status"] == "completed"
    assert snapshot["events"][-1]["event_type"] == "operation.replayed"


def test_new_operation_reuses_same_published_config(tmp_path):
    first = _publish(tmp_path, sequence=1)
    second = _publish(tmp_path, sequence=2)

    assert second == first
    snapshot = read_verified_operation_snapshot(tmp_path, "op-config-002")
    assert [event["event_type"] for event in snapshot["events"]] == [
        "operation.started",
        "model_retrieval_config.reused",
        "operation.completed",
    ]


def test_same_config_id_with_different_content_is_rejected(tmp_path):
    _publish(tmp_path)
    changed = {
        **FEATURE_V1,
        "sampling": {**FEATURE_V1["sampling"], "random_seed": 7},
    }

    with pytest.raises(ModelMatchingError) as error:
        _publish(tmp_path, sequence=2, feature=changed)

    assert error.value.code == "feature_integrity_error"


def test_auditor_cannot_publish_retrieval_config(tmp_path):
    with pytest.raises(ModelMatchingError) as error:
        _publish(tmp_path, principal=AUDITOR)

    assert error.value.code == "permission_denied"


def test_tampered_config_component_is_rejected(tmp_path):
    _publish(tmp_path)
    path = (
        tmp_path
        / "models"
        / "retrieval_configs"
        / "retrieval-v1"
        / "feature_config.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["radial_bins"] = 8
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as error:
        load_retrieval_config(tmp_path, "retrieval-v1")

    assert error.value.code == "feature_integrity_error"


def test_missing_retrieval_config_has_stable_not_found_error(tmp_path):
    with pytest.raises(ModelMatchingError) as error:
        load_retrieval_config(tmp_path, "missing-config")

    assert error.value.code == "feature_not_found"
