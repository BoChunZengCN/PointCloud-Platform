import json

import pytest

from pc_system.model_feature_index import build_model_feature_index
from pc_system.model_index_release import (
    list_model_feature_index_releases,
    load_current_model_feature_index_release,
    release_model_feature_index,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_release import release_model_version
from pc_system.model_retrieval_config import publish_retrieval_config
from phase15b2_support import (
    EXPERT,
    FEATURE_V1,
    MAPPING_V1,
    SCORING_V1,
    _mesh_reader,
    prepare_released_models,
)


def _prepare(project):
    publish_retrieval_config(
        project,
        config_id="retrieval-v1",
        feature=FEATURE_V1,
        scoring=SCORING_V1,
        category_mapping=MAPPING_V1,
        principal=EXPERT,
        operation_id="op-config-release-index",
        request_id="req-config-release-index",
        idempotency_key="idem-config-release-index",
    )
    return prepare_released_models(project)


def _index(project, sequence, *, mode="production", mesh_reader=_mesh_reader, historical=None):
    return build_model_feature_index(
        project,
        index_id=f"index-{sequence:03d}",
        index_mode=mode,
        config_id="retrieval-v1",
        historical_releases=historical,
        principal=EXPERT,
        operation_id=f"op-build-index-{sequence:03d}",
        request_id=f"req-build-index-{sequence:03d}",
        idempotency_key=f"idem-build-index-{sequence:03d}",
        mesh_reader=mesh_reader,
    )


def _release(project, *, index_id, sequence, action="activate", expected=None, rollback_of=None):
    return release_model_feature_index(
        project,
        index_id=index_id,
        release_id=f"index-release-{sequence:03d}",
        action=action,
        expected_current_release_id=expected,
        rollback_of_release_id=rollback_of,
        reason="Production index change",
        principal=EXPERT,
        operation_id=f"op-index-release-{sequence:03d}",
        request_id=f"req-index-release-{sequence:03d}",
        idempotency_key=f"idem-index-release-{sequence:03d}",
    )


def test_activate_upgrade_and_rollback_preserve_release_history(tmp_path):
    _prepare(tmp_path)
    first_index = _index(tmp_path, 1)
    second_index = _index(tmp_path, 2)
    first = _release(tmp_path, index_id=first_index["index_id"], sequence=1)
    second = _release(
        tmp_path,
        index_id=second_index["index_id"],
        sequence=2,
        expected=first["release_id"],
    )
    rolled_back = _release(
        tmp_path,
        index_id=first_index["index_id"],
        sequence=3,
        action="rollback",
        expected=second["release_id"],
        rollback_of=first["release_id"],
    )

    assert rolled_back["index_id"] == first_index["index_id"]
    assert rolled_back["previous_release_id"] == second["release_id"]
    assert load_current_model_feature_index_release(tmp_path) == rolled_back
    assert [item["release_id"] for item in list_model_feature_index_releases(tmp_path)] == [
        first["release_id"],
        second["release_id"],
        rolled_back["release_id"],
    ]
    assert _release(tmp_path, index_id=first_index["index_id"], sequence=1) == first
    assert load_current_model_feature_index_release(tmp_path) == rolled_back


def test_release_request_replays_without_duplicate(tmp_path):
    _prepare(tmp_path)
    index = _index(tmp_path, 1)
    first = _release(tmp_path, index_id=index["index_id"], sequence=1)

    assert _release(tmp_path, index_id=index["index_id"], sequence=1) == first
    assert list_model_feature_index_releases(tmp_path) == [first]


def test_low_coverage_and_challenger_indexes_cannot_activate(tmp_path):
    prepared = _prepare(tmp_path)

    def failing_reader(_path):
        raise ValueError("unsupported mesh")

    low = _index(tmp_path, 1, mesh_reader=failing_reader)
    challenger = _index(
        tmp_path,
        2,
        mode="challenger",
        historical=[
            {
                "model_id": "pump-a",
                "release_id": prepared["pump_v1_release"]["release_id"],
            }
        ],
    )
    with pytest.raises(ModelMatchingError) as coverage_error:
        _release(tmp_path, index_id=low["index_id"], sequence=1)
    assert coverage_error.value.code == "model_index_coverage_rejected"

    with pytest.raises(ModelMatchingError) as mode_error:
        _release(tmp_path, index_id=challenger["index_id"], sequence=2)
    assert mode_error.value.code == "model_index_release_conflict"


def test_expected_current_release_conflict_does_not_change_projection(tmp_path):
    _prepare(tmp_path)
    first_index = _index(tmp_path, 1)
    second_index = _index(tmp_path, 2)
    first = _release(tmp_path, index_id=first_index["index_id"], sequence=1)

    with pytest.raises(ModelMatchingError) as error:
        _release(
            tmp_path,
            index_id=second_index["index_id"],
            sequence=2,
            expected="wrong-release",
        )

    assert error.value.code == "model_index_release_conflict"
    assert load_current_model_feature_index_release(tmp_path) == first


def test_old_index_cannot_be_rolled_back_after_model_head_changes(tmp_path):
    prepared = _prepare(tmp_path)
    index = _index(tmp_path, 1)
    first = _release(tmp_path, index_id=index["index_id"], sequence=1)
    release_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        release_id="release-pump-rollback-for-index",
        action="rollback",
        expected_current_release_id=prepared["pump_v2_release"]["release_id"],
        rollback_of_release_id=prepared["pump_v1_release"]["release_id"],
        reason="Rollback model",
        principal=EXPERT,
        operation_id="op-model-rollback-for-index",
        request_id="req-model-rollback-for-index",
        idempotency_key="idem-model-rollback-for-index",
    )

    with pytest.raises(ModelMatchingError) as error:
        _release(
            tmp_path,
            index_id=index["index_id"],
            sequence=2,
            action="rollback",
            expected=first["release_id"],
            rollback_of=first["release_id"],
        )

    assert error.value.code == "model_index_stale"


def test_current_projection_tampering_is_rejected(tmp_path):
    _prepare(tmp_path)
    index = _index(tmp_path, 1)
    _release(tmp_path, index_id=index["index_id"], sequence=1)
    path = tmp_path / "models" / "current_feature_index.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["current_index_id"] = "tampered-index"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as error:
        load_current_model_feature_index_release(tmp_path)

    assert error.value.code == "model_index_integrity_error"
