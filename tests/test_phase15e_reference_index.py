import json

import pytest

from phase15b2_support import (
    EXPERT,
    FEATURE_V1,
    MAPPING_V1,
    SCORING_V1,
    prepare_released_models,
)
from phase15e_support import prepared_scan, release_request, review_request
from pc_system.model_feature_index import build_model_feature_index, read_index_entries
from pc_system.model_index_release import release_model_feature_index
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_release import release_model_version
from pc_system.model_retrieval_config import publish_retrieval_config
from pc_system.reference_review import review_reference_version


FEATURE_V11 = {
    **FEATURE_V1,
    "schema_version": "1.1",
    "config_id": "retrieval-v11",
    "scanned_sampling": {
        "algorithm": "sha256_point_subset_v1",
        "point_count": 16,
        "random_seed": 20260903,
    },
}
SCORING_V11 = {**SCORING_V1, "config_id": "retrieval-v11"}
MAPPING_V11 = {**MAPPING_V1, "config_id": "retrieval-v11"}


def _publish_config(root, feature=FEATURE_V11):
    return publish_retrieval_config(
        root,
        config_id=feature["config_id"],
        feature=feature,
        scoring={**SCORING_V11, "config_id": feature["config_id"]},
        category_mapping={**MAPPING_V11, "config_id": feature["config_id"]},
        principal=EXPERT,
        operation_id=f"op-config-{feature['config_id']}",
        request_id=f"req-config-{feature['config_id']}",
        idempotency_key=f"idem-config-{feature['config_id']}",
    )


def _released_scan(root, *, approved=True):
    prepared_scan(root)
    if approved:
        review_reference_version(root, **review_request())
    return release_model_version(root, **release_request())


def _build(root, *, index_id="index-scans", mode="production", historical=None, config_id="retrieval-v11"):
    return build_model_feature_index(
        root,
        index_id=index_id,
        index_mode=mode,
        config_id=config_id,
        historical_releases=historical,
        principal=EXPERT,
        operation_id=f"op-{index_id}",
        request_id=f"req-{index_id}",
        idempotency_key=f"idem-{index_id}",
    )


def test_verified_scanned_and_cad_releases_share_a_v11_production_index(tmp_path):
    """Removing source-family dispatch or type binding must fail this mixed index."""
    _publish_config(tmp_path)
    prepare_released_models(tmp_path)
    _released_scan(tmp_path)

    index = _build(tmp_path)
    entries = list(read_index_entries(tmp_path, index["index_id"]))

    assert {item["representation_type"] for item in entries} == {
        "cad_sampled", "scanned_reference"
    }
    assert {item["model_id"] for item in entries} == {"pump-a", "valve-a", "scan-pump"}
    scan = next(item for item in entries if item["model_id"] == "scan-pump")
    assert scan["version_id"] == "v1"
    assert scan["source_review_id"] == "review-1"
    assert scan["source_quality_status"] == "passed"
    assert index["schema_version"] == "1.1"


def test_unreviewed_scanned_release_is_not_silently_indexed(tmp_path):
    """A missing approval must become coverage evidence, never a directory-based inclusion."""
    _publish_config(tmp_path)
    prepared_scan(tmp_path)

    with pytest.raises(ModelMatchingError) as caught:
        release_model_version(tmp_path, **release_request())
    assert caught.value.code == "reference_review_required"
    assert not (tmp_path / "models" / "feature_indexes").exists()


def test_same_scanned_source_and_config_produce_the_same_representation(tmp_path):
    """Changing selection order or using random sampling must fail deterministic identity."""
    from pc_system.reference_representation import build_reference_representation

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    first = build_reference_representation(
        tmp_path, model_id="scan-pump", version_id="v1", config=FEATURE_V11,
        principal=EXPERT, operation_id="op-reference-a", request_id="req-reference-a",
        idempotency_key="idem-reference-a",
    )
    second = build_reference_representation(
        tmp_path, model_id="scan-pump", version_id="v1", config=FEATURE_V11,
        principal=EXPERT, operation_id="op-reference-b", request_id="req-reference-b",
        idempotency_key="idem-reference-b",
    )

    assert second == first
    assert first["representation_type"] == "scanned_reference"
    assert first["point_count"] == 16


def test_v10_config_explicitly_rejects_scanned_release(tmp_path):
    """Defaulting old CAD sampling for scans would make this test incorrectly pass."""
    _publish_config(tmp_path, FEATURE_V1)
    _released_scan(tmp_path)

    index = _build(tmp_path, index_id="index-v10", config_id="retrieval-v1")

    assert index["coverage"]["eligible_count"] == 1
    assert index["coverage"]["indexed_count"] == 0
    assert index["exclusions"] == [{
        "model_id": "scan-pump", "version_id": "v1",
        "code": "feature_config_invalid", "child_operation_id": index["exclusions"][0]["child_operation_id"],
    }]


@pytest.mark.parametrize("replacement", ["cad_sampled", None])
def test_tampered_or_missing_representation_type_is_rejected_when_reading_index(tmp_path, replacement):
    """Dropping type from a feature/index identity must expose this substitution."""
    _publish_config(tmp_path)
    _released_scan(tmp_path)
    index = _build(tmp_path)
    entry = next(item for item in read_index_entries(tmp_path, "index-scans") if item["model_id"] == "scan-pump")
    path = (tmp_path / "models" / "scan-pump" / "representations" / "v1"
            / "scanned_reference" / entry["representation_id"] / "representation.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if replacement is None:
        del value["representation_type"]
    else:
        value["representation_type"] = replacement
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as caught:
        list(read_index_entries(tmp_path, index["index_id"]))
    assert caught.value.code == "model_index_integrity_error"


def test_v11_index_release_keeps_the_verified_index_schema(tmp_path):
    """Downgrading a mixed-source release record must fail this schema binding."""
    _publish_config(tmp_path)
    _released_scan(tmp_path)
    _build(tmp_path)

    release = release_model_feature_index(
        tmp_path, index_id="index-scans", release_id="index-release-1", action="activate",
        expected_current_release_id=None, rollback_of_release_id=None, reason="发布已核验混合索引",
        principal=EXPERT, operation_id="op-index-release-1", request_id="req-index-release-1",
        idempotency_key="idem-index-release-1",
    )

    assert release["schema_version"] == "1.1"
