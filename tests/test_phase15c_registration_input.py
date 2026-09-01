import json

import pytest

import pc_system.model_registration_input as input_module
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_registration_input import load_registration_input
from phase15c_support import AUDITOR, EXPERT, prepare_schema_1_1_retrieval


def _arguments(retrieval):
    return {
        "asset_id": retrieval["asset_id"],
        "source_id": retrieval["source_id"],
        "instance_id": retrieval["instance_id"],
        "retrieval_run_id": retrieval["retrieval_run_id"],
        "candidate_rank": 1,
        "principal": EXPERT,
    }


def test_registration_input_loads_exact_ranked_representation(tmp_path):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)

    frozen = load_registration_input(tmp_path, **_arguments(retrieval))

    candidate = retrieval["candidates"][0]
    assert frozen["retrieval_evidence"]["schema_version"] == "1.1"
    assert frozen["candidate_evidence"]["candidate_rank"] == 1
    assert frozen["candidate_evidence"]["representation_id"] == candidate[
        "representation_id"
    ]
    assert frozen["candidate_evidence"]["representation_fingerprint"] == candidate[
        "representation_fingerprint"
    ]
    assert frozen["candidate_evidence"]["feature_vector_fingerprint"] == candidate[
        "feature_vector_fingerprint"
    ]
    assert frozen["object_fingerprint"] == retrieval["object_fingerprint"]
    assert frozen["coordinate_unit"] == "m"
    assert frozen["model_points"]
    assert frozen["object_points"]
    assert all(len(point) == 3 for point in frozen["model_points"])
    assert all(len(point) == 3 for point in frozen["object_points"])
    assert frozen["symmetry_transforms"] == []


def test_legacy_retrieval_cannot_start_formal_registration(tmp_path, monkeypatch):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)
    legacy = {**retrieval, "schema_version": "1.0"}
    monkeypatch.setattr(input_module, "load_model_retrieval", lambda *a, **k: legacy)

    with pytest.raises(ModelMatchingError) as captured:
        load_registration_input(tmp_path, **_arguments(retrieval))

    assert captured.value.code == "registration_input_incomplete"


@pytest.mark.parametrize("candidate_rank", [0, 2, True])
def test_candidate_rank_must_select_an_existing_one_based_candidate(
    tmp_path, candidate_rank
):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)
    arguments = _arguments(retrieval)
    arguments["candidate_rank"] = candidate_rank

    with pytest.raises(ModelMatchingError) as captured:
        load_registration_input(tmp_path, **arguments)

    assert captured.value.code == "registration_input_incomplete"


def test_changed_object_fingerprint_is_stale(tmp_path, monkeypatch):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)
    original = input_module.load_retrieval_object

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        return {**value, "object_fingerprint": "f" * 64}

    monkeypatch.setattr(input_module, "load_retrieval_object", changed)

    with pytest.raises(ModelMatchingError) as captured:
        load_registration_input(tmp_path, **_arguments(retrieval))

    assert captured.value.code == "object_fingerprint_stale"


@pytest.mark.parametrize("artifact", ["representation.json", "sampled_points.json"])
def test_model_representation_tampering_fails_closed(tmp_path, artifact):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)
    candidate = retrieval["candidates"][0]
    path = (
        tmp_path
        / "models"
        / candidate["model_id"]
        / "representations"
        / candidate["version_id"]
        / "cad_sampled"
        / candidate["representation_id"]
        / artifact
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    if artifact == "representation.json":
        value["point_count"] += 1
    else:
        value["points"][0][0] += 0.25
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as captured:
        load_registration_input(tmp_path, **_arguments(retrieval))

    assert captured.value.code == "artifact_integrity_failed"


def test_candidate_feature_evidence_mismatch_fails_closed(tmp_path, monkeypatch):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)
    changed = json.loads(json.dumps(retrieval))
    changed["candidates"][0]["feature_vector_fingerprint"] = "f" * 64
    monkeypatch.setattr(input_module, "load_model_retrieval", lambda *a, **k: changed)

    with pytest.raises(ModelMatchingError) as captured:
        load_registration_input(tmp_path, **_arguments(retrieval))

    assert captured.value.code == "artifact_integrity_failed"


def test_only_experts_can_freeze_registration_input(tmp_path):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)
    arguments = _arguments(retrieval)
    arguments["principal"] = AUDITOR

    with pytest.raises(ModelMatchingError) as captured:
        load_registration_input(tmp_path, **arguments)

    assert captured.value.code == "permission_denied"
