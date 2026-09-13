"""扫描参考模板在检索、配准和不可变绑定链中的行为。"""

import copy

import pytest

import pc_system.model_registration_input as input_module
import pc_system.model_representation as representation_module
import pc_system.model_retrieval as retrieval_module
from phase15c_support import DeterministicRegistrationEngine, EXPERT, REGISTRATION_V1
from phase15d_support import OPERATOR
from test_phase15b2_retrieval import _prepare_project, _retrieve
from test_phase15e_reference_index import _build, _publish_config, _released_scan

from pc_system.model_index_release import release_model_feature_index
from pc_system.model_match_decision import decide_model_match, load_decision_context
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_registration import register_model_candidate
from pc_system.model_registration_config import publish_registration_config
from pc_system.model_representation import load_model_representation, load_representation_points
from pc_system.model_release import release_model_version


def _prepare_scanned_retrieval(root):
    """发布包含一个扫描参考模板的生产索引，并检索已有场景对象。"""
    _prepare_project(root)
    _publish_config(root)
    _released_scan(root)
    index = _build(root, index_id="index-scanned-matching")
    release_model_feature_index(
        root,
        index_id=index["index_id"],
        release_id="index-release-scanned-matching",
        action="activate",
        expected_current_release_id="index-release-retrieval-001",
        rollback_of_release_id=None,
        reason="启用包含扫描参考模板的索引",
        principal=EXPERT,
        operation_id="op-index-release-scanned-matching",
        request_id="req-index-release-scanned-matching",
        idempotency_key="idem-index-release-scanned-matching",
    )
    report = _retrieve(root, sequence=2)
    rank = next(
        index + 1
        for index, candidate in enumerate(report["candidates"])
        if candidate["model_id"] == "scan-pump"
    )
    return report, rank


def _publish_registration_config(root):
    config = copy.deepcopy(REGISTRATION_V1)
    config["quality_gates"]["maximum_dimension_relative_error"] = 1.0
    return publish_registration_config(
        root,
        config_id="registration-scanned-v1",
        config=config,
        principal=EXPERT,
        operation_id="op-registration-config-scanned",
        request_id="req-registration-config-scanned",
        idempotency_key="idem-registration-config-scanned",
    )


def test_v12_candidates_freeze_scanned_representation_type_and_reject_missing_or_tampered_type(
    tmp_path, monkeypatch
):
    """删除或替换新候选的来源类型时，冻结输入不能把它当作 CAD。"""
    report, rank = _prepare_scanned_retrieval(tmp_path)
    candidate = report["candidates"][rank - 1]

    assert report["schema_version"] == "1.2"
    assert candidate["representation_type"] == "scanned_reference"

    for replacement in (None, "cad_sampled"):
        changed = copy.deepcopy(report)
        if replacement is None:
            del changed["candidates"][rank - 1]["representation_type"]
        else:
            changed["candidates"][rank - 1]["representation_type"] = replacement
        monkeypatch.setattr(input_module, "load_model_retrieval", lambda *_args, **_kwargs: changed)
        with pytest.raises(ModelMatchingError) as caught:
            input_module.load_registration_input(
                tmp_path,
                asset_id=report["asset_id"],
                source_id=report["source_id"],
                instance_id=report["instance_id"],
                retrieval_run_id=report["retrieval_run_id"],
                candidate_rank=rank,
                principal=EXPERT,
            )
        assert getattr(caught.value, "code", None) in {
            "registration_input_incomplete",
            "artifact_integrity_failed",
        }


def test_scanned_frozen_input_reads_verified_local_metre_points_without_cad_loader(
    tmp_path, monkeypatch
):
    """扫描候选必须从统一表达快照取得局部米制点，而非 CAD 采样读取器。"""
    report, rank = _prepare_scanned_retrieval(tmp_path)
    candidate = report["candidates"][rank - 1]
    representation = load_model_representation(
        tmp_path,
        candidate["model_id"],
        candidate["version_id"],
        candidate["representation_id"],
    )
    expected_points = load_representation_points(tmp_path, representation)

    monkeypatch.setattr(
        representation_module,
        "load_sampled_representation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scanned reference must not use the CAD representation loader")
        ),
    )
    frozen = input_module.load_registration_input(
        tmp_path,
        asset_id=report["asset_id"],
        source_id=report["source_id"],
        instance_id=report["instance_id"],
        retrieval_run_id=report["retrieval_run_id"],
        candidate_rank=rank,
        principal=EXPERT,
    )

    assert frozen["coordinate_unit"] == "m"
    assert frozen["candidate_evidence"]["representation_type"] == "scanned_reference"
    assert frozen["model_points"] == expected_points


def test_scanned_registration_report_and_binding_stay_fixed_across_template_release_changes(
    tmp_path,
):
    """新报告固定类型和局部到对象刚体矩阵，之后的模板发布不改变既有绑定。"""
    report, rank = _prepare_scanned_retrieval(tmp_path)
    _publish_registration_config(tmp_path)
    registration = register_model_candidate(
        tmp_path,
        registration_id="registration-scanned-1",
        asset_id=report["asset_id"],
        source_id=report["source_id"],
        instance_id=report["instance_id"],
        retrieval_run_id=report["retrieval_run_id"],
        candidate_rank=rank,
        config_id="registration-scanned-v1",
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        principal=EXPERT,
        operation_id="op-registration-scanned-1",
        request_id="req-registration-scanned-1",
        idempotency_key="idem-registration-scanned-1",
    )
    identity = {key: registration[key] for key in ("asset_id", "source_id", "instance_id", "retrieval_run_id")}
    context = load_decision_context(tmp_path, **identity)
    decided = decide_model_match(
        tmp_path,
        decision_id="decision-scanned-1",
        case_id=context["case_id"],
        decision="confirmed",
        decision_reason="现场人工核验扫描参考姿态",
        verification_scope="operational_pose",
        registration_id=registration["registration_id"],
        candidate_rank=rank,
        expected_case_revision=context["case_revision"],
        binding_id="binding-scanned-1",
        principal=OPERATOR,
        operation_id="op-decision-scanned-1",
        request_id="req-decision-scanned-1",
        idempotency_key="idem-decision-scanned-1",
    )
    frozen_binding = copy.deepcopy(decided["binding"])

    assert registration["schema_version"] == "1.1"
    assert registration["candidate_representation_type"] == "scanned_reference"
    assert registration["coordinate_unit"] == "m"
    assert registration["rigid_transform_4x4"][0][3] == pytest.approx(1.0)

    # 发布和回滚模板是独立历史操作，绑定仍引用其已验证的原版本和矩阵。
    from phase15e_support import prepared_scan, release_request, review_request
    from pc_system.reference_review import review_reference_version

    prepared_scan(tmp_path, version_id="v2")
    review_reference_version(tmp_path, **review_request(version_id="v2", sequence="2"))
    release_model_version(
        tmp_path,
        **release_request(
            sequence="2",
            version_id="v2",
            release_id="release-2",
            expected_current_release_id="release-1",
        ),
    )
    release_model_version(
        tmp_path,
        **release_request(
            sequence="3",
            version_id="v1",
            release_id="release-3",
            action="rollback",
            expected_current_release_id="release-2",
            rollback_of_release_id="release-1",
        ),
    )

    current = load_decision_context(tmp_path, **identity)
    assert current["current_binding"] == frozen_binding
    assert current["current_binding"]["model_version_id"] == "v1"


def test_legacy_retrieval_and_registration_contracts_remain_readable():
    """版本化读取仍接受历史 1.0/1.1 检索及 1.0 配准报告契约。"""
    validate = retrieval_module._validate_retrieval_contract_version
    assert validate({"schema_version": "1.0"}, {"schema_version": "1.0", "candidates": []}) == "1.0"
    assert validate(
        {"schema_version": "1.1"},
        {
            "schema_version": "1.1",
            "candidates": [{
                "release_id": "release-1", "representation_id": "representation-1",
                "representation_fingerprint": "a", "feature_id": "feature-1",
                "feature_vector_fingerprint": "b",
            }],
        },
    ) == "1.1"


def test_v10_index_keeps_legacy_candidates_and_registration_report_untyped(tmp_path):
    """旧索引的真实链路不得因内部索引字段而升级检索或配准工件。"""
    _prepare_project(tmp_path)
    report = _retrieve(tmp_path)

    assert report["schema_version"] == "1.1"
    assert all("representation_type" not in candidate for candidate in report["candidates"])

    frozen = input_module.load_registration_input(
        tmp_path,
        asset_id=report["asset_id"],
        source_id=report["source_id"],
        instance_id=report["instance_id"],
        retrieval_run_id=report["retrieval_run_id"],
        candidate_rank=1,
        principal=EXPERT,
    )
    _publish_registration_config(tmp_path)
    registration = register_model_candidate(
        tmp_path,
        registration_id="registration-legacy-1",
        asset_id=report["asset_id"],
        source_id=report["source_id"],
        instance_id=report["instance_id"],
        retrieval_run_id=report["retrieval_run_id"],
        candidate_rank=1,
        config_id="registration-scanned-v1",
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        principal=EXPERT,
        operation_id="op-registration-legacy-1",
        request_id="req-registration-legacy-1",
        idempotency_key="idem-registration-legacy-1",
    )

    assert "representation_type" not in frozen["candidate_evidence"]
    assert registration["schema_version"] == "1.0"
    assert "candidate_representation_type" not in registration
