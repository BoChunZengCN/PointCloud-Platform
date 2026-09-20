"""Phase 15E 扫描参考模板的工程全链验收。"""

import copy
import hashlib
import json

from phase15b2_support import EXPERT, FEATURE_V1, MAPPING_V1, SCORING_V1
from phase15c_support import DeterministicRegistrationEngine, REGISTRATION_V1
from phase15d_support import OPERATOR

from pc_system.model_feature_index import build_model_feature_index, read_index_entries
from pc_system.model_index_release import release_model_feature_index
from pc_system.model_library import create_model_asset
from pc_system.model_match_decision import decide_model_match, load_decision_context
from pc_system.model_registration import register_model_candidate
from pc_system.model_registration_config import publish_registration_config
from pc_system.model_registration_input import load_registration_input
from pc_system.model_release import load_current_model_release, release_model_version
from pc_system.model_retrieval import load_model_retrieval, retrieve_model_candidates
from pc_system.model_retrieval_config import publish_retrieval_config
from pc_system.reference_import import import_reference_version
from pc_system.reference_review import review_reference_version
from pc_system.segmentation_correction_events import apply_correction_event
from pc_system.segmentation_correction_releases import (
    publish_correction_release,
    transition_correction_session,
)
from pc_system.segmentation_corrections import create_correction_session
from pc_system.segmentation_service import run_segmentation


FEATURE_V11 = {
    **FEATURE_V1,
    "schema_version": "1.1",
    "config_id": "integration-retrieval-v11",
    "scanned_sampling": {
        "algorithm": "sha256_point_subset_v1",
        "point_count": 16,
        "random_seed": 20260903,
    },
}


def _write_ply(path, points):
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex " + str(len(points))
        + "\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
        + "".join(f"{x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in points),
        encoding="ascii",
    )


def _reference_points(*, variant="positive", offset=0.0):
    """生成扫描模板源；负例是独立的不同尺寸几何，而不是改名或复制。"""
    if variant == "positive":
        axes = ((0.0, 0.6, 1.2, 1.8), (0.0, 0.35, 0.7, 1.05), (0.0, 0.18, 0.36, 0.54))
    else:
        axes = ((0.0, 0.6, 1.2, 1.8), (0.0, 0.6, 1.2, 1.8), (0.0, 0.12, 0.24, 0.36))
    return [
        (x + offset, y + offset, z + offset)
        for x in axes[0] for y in axes[1] for z in axes[2]
    ]


def _scene_points_from_an_independent_scan():
    """另一次扫描过程：刚体平移、逆序与有界扰动，绝不复制模板文件。"""
    raw = _reference_points(variant="positive")
    return [
        {"x": x + 1.0 + ((index % 3) - 1) * 0.0004,
         "y": y + 2.0 + ((index % 5) - 2) * 0.0003,
         "z": z + 3.0 + ((index % 7) - 3) * 0.0002}
        for index, (x, y, z) in enumerate(reversed(raw))
    ]


def _sha256_tree(root, immutable_roots):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for immutable_root in immutable_roots
        for path in (root / immutable_root).rglob("*") if path.is_file()
    }


def _create_and_release_scan(root, *, model_id, version_id, points, sequence, supersedes_version_id=None):
    source = root / f"{model_id}-{version_id}.ply"
    _write_ply(source, points)
    imported = import_reference_version(
        root,
        model_id=model_id,
        version_id=version_id,
        source_path=source,
        declared_unit="m",
        license_name="工程夹具内部授权",
        provenance={"source": "独立工程扫描夹具", "preparation": "单对象裁剪", "scan_time": None},
        supersedes_version_id=supersedes_version_id,
        principal=EXPERT,
        operation_id=f"import-{model_id}-{version_id}",
        request_id=f"request-import-{model_id}-{version_id}",
        idempotency_key=f"idem-import-{model_id}-{version_id}",
    )
    review_reference_version(
        root,
        model_id=model_id,
        version_id=version_id,
        decision="approved",
        reason="工程夹具已确认单对象、来源许可和覆盖限制",
        acknowledgements=["single_object", "metadata_and_rights", "coverage_limitations"],
        principal=EXPERT,
        operation_id=f"review-{model_id}-{version_id}",
        request_id=f"request-review-{model_id}-{version_id}",
        idempotency_key=f"idem-review-{model_id}-{version_id}",
    )
    return imported


def _publish_index(root, *, index_id, release_id, expected_current_release_id):
    index = build_model_feature_index(
        root,
        index_id=index_id,
        index_mode="production",
        config_id=FEATURE_V11["config_id"],
        historical_releases=None,
        principal=EXPERT,
        operation_id=f"build-{index_id}",
        request_id=f"request-build-{index_id}",
        idempotency_key=f"idem-build-{index_id}",
    )
    release_model_feature_index(
        root,
        index_id=index_id,
        release_id=release_id,
        action="activate",
        expected_current_release_id=expected_current_release_id,
        rollback_of_release_id=None,
        reason="发布工程扫描模板生产索引",
        principal=EXPERT,
        operation_id=f"publish-{release_id}",
        request_id=f"request-publish-{release_id}",
        idempotency_key=f"idem-publish-{release_id}",
    )
    return index


def _publish_scene_release(root):
    points = _scene_points_from_an_independent_scan()
    source = root / "independent-scene.points.json"
    source.write_text(json.dumps(points), encoding="utf-8")
    run_segmentation(
        root,
        asset_id="independent-scene",
        asset_version="v1",
        source_uri=str(source),
        points=points,
        config={"engine": "builtin_geometric", "distance_threshold": 3.0, "min_points": 1},
        run_id="scene-run-1",
    )
    session = create_correction_session(
        root,
        asset_id="independent-scene",
        run_id="scene-run-1",
        session_id="scene-session-1",
        sample_id="scene-sample-1",
        actor="alice",
    )
    relabeled = apply_correction_event(
        root,
        asset_id="independent-scene",
        session_id="scene-session-1",
        actor="alice",
        expected_revision=session["revision"],
        client_request_id="scene-relabel-1",
        operation={"type": "relabel", "instance_ids": ["obj-001"], "class_id": "centrifugal-pump"},
    )
    confirmed = apply_correction_event(
        root,
        asset_id="independent-scene",
        session_id="scene-session-1",
        actor="alice",
        expected_revision=relabeled["revision"],
        client_request_id="scene-confirm-1",
        operation={"type": "confirm", "instance_ids": ["obj-001"]},
    )
    reviewed = transition_correction_session(
        root,
        asset_id="independent-scene",
        session_id="scene-session-1",
        action="submit",
        actor="alice",
        expected_revision=confirmed["revision"],
    )
    publish_correction_release(
        root,
        asset_id="independent-scene",
        session_id="scene-session-1",
        release_id="scene-release-1",
        reviewer="reviewer",
        expected_revision=reviewed["revision"],
        benchmark_split="development",
        license_name="工程夹具",
    )


def _retrieve(root, *, retrieval_run_id, sequence):
    return retrieve_model_candidates(
        root,
        retrieval_run_id=retrieval_run_id,
        source_kind="correction_release",
        asset_id="independent-scene",
        source_id="scene-release-1",
        instance_id="obj-001",
        index_release_id=None,
        index_id=None,
        top_k=2,
        keywords=[],
        tags=[],
        manufacturer=None,
        model_number=None,
        hint_source=None,
        principal=EXPERT,
        operation_id=f"retrieve-{sequence}",
        request_id=f"request-retrieve-{sequence}",
        idempotency_key=f"idem-retrieve-{sequence}",
    )


def test_scanned_reference_engineering_fixture_covers_import_to_immutable_binding_and_history(tmp_path):
    """错误地复制模板、跳过核验/发布、错排 Top-K 或漂移绑定都会使该全链失败。"""
    for model_id, display_name, model_number in (
        ("scan-positive", "工程扫描正例泵", "ENG-POS"),
        ("scan-negative", "工程扫描相似负例", "ENG-NEG"),
    ):
        create_model_asset(
            tmp_path,
            model_id=model_id,
            display_name=display_name,
            category_id="pump",
            manufacturer="工程夹具",
            model_number=model_number,
            keywords=["centrifugal"],
            tags=["engineering"],
            source_family="scanned_reference",
            principal=EXPERT,
            operation_id=f"asset-{model_id}",
            request_id=f"request-asset-{model_id}",
            idempotency_key=f"idem-asset-{model_id}",
        )

    _create_and_release_scan(
        tmp_path, model_id="scan-positive", version_id="v1",
        points=_reference_points(variant="positive"), sequence="v1",
    )
    _create_and_release_scan(
        tmp_path, model_id="scan-negative", version_id="v1",
        points=_reference_points(variant="negative"), sequence="v1",
    )
    for model_id in ("scan-positive", "scan-negative"):
        release_model_version(
            tmp_path,
            model_id=model_id,
            version_id="v1",
            release_id=f"release-{model_id}-v1",
            action="activate",
            expected_current_release_id=None,
            rollback_of_release_id=None,
            reason="发布已核验工程扫描模板",
            principal=EXPERT,
            operation_id=f"release-{model_id}-v1",
            request_id=f"request-release-{model_id}-v1",
            idempotency_key=f"idem-release-{model_id}-v1",
        )

    publish_retrieval_config(
        tmp_path,
        config_id=FEATURE_V11["config_id"],
        feature=FEATURE_V11,
        scoring={**SCORING_V1, "config_id": FEATURE_V11["config_id"]},
        category_mapping={**MAPPING_V1, "config_id": FEATURE_V11["config_id"]},
        principal=EXPERT,
        operation_id="publish-integration-retrieval-config",
        request_id="request-publish-integration-retrieval-config",
        idempotency_key="idem-publish-integration-retrieval-config",
    )
    _publish_scene_release(tmp_path)
    index_v1 = _publish_index(
        tmp_path, index_id="integration-index-v1", release_id="integration-index-release-v1",
        expected_current_release_id=None,
    )
    entries = list(read_index_entries(tmp_path, index_v1["index_id"]))
    assert [(entry["model_id"], entry["representation_type"]) for entry in entries] == [
        ("scan-negative", "scanned_reference"), ("scan-positive", "scanned_reference"),
    ]

    retrieval = _retrieve(tmp_path, retrieval_run_id="integration-retrieval-v1", sequence="v1")
    assert [candidate["model_id"] for candidate in retrieval["candidates"]] == [
        "scan-positive", "scan-negative",
    ]
    assert all(candidate["representation_type"] == "scanned_reference" for candidate in retrieval["candidates"])
    assert load_model_retrieval(
        tmp_path, asset_id="independent-scene", source_id="scene-release-1", instance_id="obj-001",
        retrieval_run_id="integration-retrieval-v1",
    ) == retrieval

    registration_config = publish_registration_config(
        tmp_path,
        config_id="integration-registration-v1",
        config=copy.deepcopy(REGISTRATION_V1),
        principal=EXPERT,
        operation_id="publish-integration-registration-config",
        request_id="request-publish-integration-registration-config",
        idempotency_key="idem-publish-integration-registration-config",
    )
    frozen = load_registration_input(
        tmp_path,
        asset_id="independent-scene",
        source_id="scene-release-1",
        instance_id="obj-001",
        retrieval_run_id="integration-retrieval-v1",
        candidate_rank=1,
        principal=EXPERT,
    )
    registration = register_model_candidate(
        tmp_path,
        registration_id="integration-registration-v1",
        asset_id="independent-scene",
        source_id="scene-release-1",
        instance_id="obj-001",
        retrieval_run_id="integration-retrieval-v1",
        candidate_rank=1,
        config_id=registration_config["config_id"],
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        principal=EXPERT,
        operation_id="register-integration-v1",
        request_id="request-register-integration-v1",
        idempotency_key="idem-register-integration-v1",
    )
    identity = {
        "asset_id": "independent-scene", "source_id": "scene-release-1",
        "instance_id": "obj-001", "retrieval_run_id": "integration-retrieval-v1",
    }
    context = load_decision_context(tmp_path, **identity)
    decided = decide_model_match(
        tmp_path,
        decision_id="integration-decision-v1",
        case_id=context["case_id"],
        decision="confirmed",
        decision_reason="工程夹具人工确认扫描参考候选",
        verification_scope="operational_pose",
        registration_id=registration["registration_id"],
        candidate_rank=1,
        expected_case_revision=context["case_revision"],
        binding_id="integration-binding-v1",
        principal=OPERATOR,
        operation_id="decide-integration-v1",
        request_id="request-decide-integration-v1",
        idempotency_key="idem-decide-integration-v1",
    )
    assert frozen["candidate_evidence"]["representation_type"] == "scanned_reference"
    assert registration["gate_status"] == "passed"
    assert registration["residual_metrics"]["observed_to_model_coverage"] == 1.0
    assert registration["residual_metrics"]["model_to_observed_coverage"] == 1.0
    frozen_binding = decided["binding"]

    immutable_roots = [
        "models/scan-positive/versions/v1",
        "models/scan-positive/reviews/v1",
        "models/scan-positive/releases/release-scan-positive-v1",
        "models/scan-positive/representations/v1",
        "models/scan-positive/features/v1",
        "models/scan-negative/versions/v1",
        "models/scan-negative/reviews/v1",
        "models/scan-negative/releases/release-scan-negative-v1",
        "models/scan-negative/representations/v1",
        "models/scan-negative/features/v1",
    ]
    before_history = _sha256_tree(tmp_path, immutable_roots)
    _create_and_release_scan(
        tmp_path, model_id="scan-positive", version_id="v2",
        points=_reference_points(variant="positive", offset=0.23), sequence="v2", supersedes_version_id="v1",
    )
    release_model_version(
        tmp_path,
        model_id="scan-positive", version_id="v2", release_id="release-scan-positive-v2",
        action="activate", expected_current_release_id="release-scan-positive-v1", rollback_of_release_id=None,
        reason="追加独立扫描版本", principal=EXPERT, operation_id="release-scan-positive-v2",
        request_id="request-release-scan-positive-v2", idempotency_key="idem-release-scan-positive-v2",
    )
    _publish_index(
        tmp_path, index_id="integration-index-v2", release_id="integration-index-release-v2",
        expected_current_release_id="integration-index-release-v1",
    )
    release_model_version(
        tmp_path,
        model_id="scan-positive", version_id="v1", release_id="release-scan-positive-v3",
        action="rollback", expected_current_release_id="release-scan-positive-v2",
        rollback_of_release_id="release-scan-positive-v1", reason="恢复已绑定模板版本",
        principal=EXPERT, operation_id="rollback-scan-positive-v1", request_id="request-rollback-scan-positive-v1",
        idempotency_key="idem-rollback-scan-positive-v1",
    )
    _publish_index(
        tmp_path, index_id="integration-index-v3", release_id="integration-index-release-v3",
        expected_current_release_id="integration-index-release-v2",
    )

    assert load_current_model_release(tmp_path, "scan-positive")["version_id"] == "v1"
    assert load_decision_context(tmp_path, **identity)["current_binding"] == frozen_binding
    assert load_registration_input(tmp_path, candidate_rank=1, principal=EXPERT, **identity) == frozen
    assert _retrieve(tmp_path, retrieval_run_id="integration-retrieval-v3", sequence="v3")["candidates"][0]["model_id"] == "scan-positive"
    assert _sha256_tree(tmp_path, immutable_roots) == before_history
    assert not list(tmp_path.rglob("*.obj"))
    assert not list(tmp_path.rglob("*.stl"))
    assert all("cad_mesh" not in path.parts for path in tmp_path.rglob("*"))
