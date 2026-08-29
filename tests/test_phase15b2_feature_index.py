import pytest

from pc_system.model_feature_index import (
    build_model_feature_index,
    list_model_feature_indexes,
    load_model_feature_index,
    read_index_entries,
)
from pc_system.model_matching_audit import load_operation
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
        operation_id="op-config-index",
        request_id="req-config-index",
        idempotency_key="idem-config-index",
    )
    return prepare_released_models(project)


def _build(project, *, sequence=1, mode="production", historical=None, mesh_reader=_mesh_reader):
    return build_model_feature_index(
        project,
        index_id=f"index-{sequence:03d}",
        index_mode=mode,
        config_id="retrieval-v1",
        historical_releases=historical,
        principal=EXPERT,
        operation_id=f"op-index-{sequence:03d}",
        request_id=f"req-index-{sequence:03d}",
        idempotency_key=f"idem-index-{sequence:03d}",
        mesh_reader=mesh_reader,
    )


def test_production_index_contains_only_current_model_releases(tmp_path):
    prepared = _prepare(tmp_path)

    index = _build(tmp_path)
    entries = list(read_index_entries(tmp_path, index["index_id"]))

    assert [(row["model_id"], row["version_id"]) for row in entries] == [
        ("pump-a", "v2"),
        ("valve-a", "v1"),
    ]
    assert [row["release_id"] for row in entries] == [
        prepared["pump_v2_release"]["release_id"],
        prepared["valve_v1_release"]["release_id"],
    ]
    assert index["index_mode"] == "production"
    assert index["coverage"]["coverage"] == 1.0
    assert index["coverage"]["indexed_count"] == 2
    assert index["coverage"]["eligible_count"] == 2
    assert load_model_feature_index(
        tmp_path, index["index_id"], require_current_heads=True
    ) == index
    assert list_model_feature_indexes(tmp_path) == [index]


def test_challenger_index_contains_only_explicit_historical_release(tmp_path):
    prepared = _prepare(tmp_path)

    index = _build(
        tmp_path,
        mode="challenger",
        historical=[
            {
                "model_id": "pump-a",
                "release_id": prepared["pump_v1_release"]["release_id"],
            }
        ],
    )
    entries = list(read_index_entries(tmp_path, index["index_id"]))

    assert [(row["model_id"], row["version_id"], row["source_mode"]) for row in entries] == [
        ("pump-a", "v1", "challenger"),
    ]
    assert index["current_heads"] == []
    assert index["coverage"]["eligible_count"] == 1


def test_production_rejects_historical_release_selection(tmp_path):
    prepared = _prepare(tmp_path)

    with pytest.raises(ModelMatchingError) as error:
        _build(
            tmp_path,
            historical=[
                {
                    "model_id": "pump-a",
                    "release_id": prepared["pump_v1_release"]["release_id"],
                }
            ],
        )

    assert error.value.code == "feature_config_invalid"


def test_missing_representation_is_created_by_audited_child_operation(tmp_path):
    _prepare(tmp_path)

    index = _build(tmp_path)
    valve = next(row for row in read_index_entries(tmp_path, index["index_id"]) if row["model_id"] == "valve-a")

    child_id = valve["sampling_operation_id"]
    assert load_operation(tmp_path, child_id)["status"] == "completed"
    assert child_id.startswith("op-auto-sample-")
    assert load_operation(tmp_path, valve["feature_operation_id"])["status"] == "completed"


def test_same_index_request_replays_and_new_operation_reuses(tmp_path):
    _prepare(tmp_path)
    first = _build(tmp_path)
    assert _build(tmp_path) == first

    reused = build_model_feature_index(
        tmp_path,
        index_id="index-001",
        index_mode="production",
        config_id="retrieval-v1",
        historical_releases=None,
        principal=EXPERT,
        operation_id="op-index-reuse",
        request_id="req-index-reuse",
        idempotency_key="idem-index-reuse",
        mesh_reader=_mesh_reader,
    )

    assert reused == first
    assert load_operation(tmp_path, "op-index-reuse")["status"] == "completed"


def test_ordinary_model_failure_is_excluded_and_reduces_coverage(tmp_path):
    _prepare(tmp_path)

    def failing_reader(_path):
        raise ValueError("unsupported mesh")

    index = _build(tmp_path, mesh_reader=failing_reader)

    assert [(row["model_id"], row["version_id"]) for row in read_index_entries(tmp_path, index["index_id"])] == [
        ("pump-a", "v2")
    ]
    assert index["coverage"]["coverage"] == 0.5
    assert index["coverage"]["excluded_count"] == 1
    assert index["exclusions"][0]["model_id"] == "valve-a"
    assert index["exclusions"][0]["child_operation_id"].startswith("op-auto-sample-")


def test_entries_are_deterministic_and_tampering_is_rejected(tmp_path):
    _prepare(tmp_path)
    first = _build(tmp_path, sequence=1)
    second = _build(tmp_path, sequence=2)
    first_path = tmp_path / "models" / "feature_indexes" / first["index_id"] / "entries.jsonl"
    second_path = tmp_path / "models" / "feature_indexes" / second["index_id"] / "entries.jsonl"

    assert first_path.read_bytes() == second_path.read_bytes()
    rows = first_path.read_text(encoding="utf-8").splitlines()
    first_path.write_text("\n".join([rows[1], rows[0]]) + "\n", encoding="utf-8")
    with pytest.raises(ModelMatchingError) as error:
        list(read_index_entries(tmp_path, first["index_id"]))
    assert error.value.code == "model_index_integrity_error"


def test_production_index_detects_current_model_head_change(tmp_path):
    prepared = _prepare(tmp_path)
    index = _build(tmp_path)
    release_model_version(
        tmp_path,
        model_id="pump-a",
        version_id="v1",
        release_id="release-pump-rollback",
        action="rollback",
        expected_current_release_id=prepared["pump_v2_release"]["release_id"],
        rollback_of_release_id=prepared["pump_v1_release"]["release_id"],
        reason="Rollback",
        principal=EXPERT,
        operation_id="op-release-pump-rollback",
        request_id="req-release-pump-rollback",
        idempotency_key="idem-release-pump-rollback",
    )

    with pytest.raises(ModelMatchingError) as error:
        load_model_feature_index(tmp_path, index["index_id"], require_current_heads=True)

    assert error.value.code == "model_index_stale"
    assert load_model_feature_index(
        tmp_path, index["index_id"], require_current_heads=False
    )["index_id"] == index["index_id"]
