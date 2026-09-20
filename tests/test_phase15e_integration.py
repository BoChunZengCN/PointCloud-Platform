"""Phase 15E 扫描参考模板的工程全链验收。"""

import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from phase15b2_support import EXPERT, FEATURE_V1, MAPPING_V1, SCORING_V1
from phase15c_support import REGISTRATION_V1
from phase15d_support import OPERATOR

from pc_system.model_feature_index import build_model_feature_index, read_index_entries
from pc_system.model_import import import_model_version, load_model_version
from pc_system.model_index_release import release_model_feature_index
from pc_system.model_library import create_model_asset
from pc_system.model_match_decision import (
    decide_model_match,
    load_decision_bundle,
    load_decision_context,
)
from pc_system.model_registration import load_model_registration, register_model_candidate
from pc_system.model_registration_config import publish_registration_config
from pc_system.model_registration_engine import EngineDescription
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
        "point_count": 64,
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
        for path in (
            [root / immutable_root]
            if (root / immutable_root).is_file()
            else (root / immutable_root).rglob("*")
        )
        if path.is_file()
    }


def _snapshot_immutable_groups(root, groups):
    snapshot = {name: _sha256_tree(root, roots) for name, roots in groups.items()}
    assert all(snapshot.values()), "每类历史工件都必须在冻结时已有真实字节"
    return snapshot


def _nearest_neighbor_distances(source_points, target_points):
    """用输入坐标计算最近邻距离；不得接受配准引擎伪造的残差数组。"""
    source = np.asarray(source_points, dtype=np.float64)
    target = np.asarray(target_points, dtype=np.float64)
    return np.sqrt(((source[:, None, :] - target[None, :, :]) ** 2).sum(axis=2)).min(axis=1)


def _transform_points(points, matrix):
    values = np.asarray(points, dtype=np.float64)
    homogeneous = np.column_stack((values, np.ones(len(values))))
    return (np.asarray(matrix, dtype=np.float64) @ homogeneous.T).T[:, :3]


class GeometryEvidenceRegistrationEngine:
    """仅供工程夹具使用：从冻结的真实点坐标计算平移和双向最近邻证据。"""

    def describe(self):
        return EngineDescription("deterministic-test", "geometry-evidence-v1", False)

    def preprocess(self, model_points, object_points, _config):
        return {
            "model_points": np.asarray(model_points, dtype=np.float64),
            "object_points": np.asarray(object_points, dtype=np.float64),
        }

    @staticmethod
    def _matrix(prepared):
        translation = (
            prepared["object_points"].mean(axis=0)
            - prepared["model_points"].mean(axis=0)
        )
        return [
            [1.0, 0.0, 0.0, float(translation[0])],
            [0.0, 1.0, 0.0, float(translation[1])],
            [0.0, 0.0, 1.0, float(translation[2])],
            [0.0, 0.0, 0.0, 1.0],
        ]

    @staticmethod
    def _metrics(prepared, matrix):
        transformed = _transform_points(prepared["model_points"], matrix)
        observed = _nearest_neighbor_distances(prepared["object_points"], transformed)
        model = _nearest_neighbor_distances(transformed, prepared["object_points"])
        return observed, model

    def coarse_register(self, prepared, hypotheses, _config):
        matrix = self._matrix(prepared)
        observed, model = self._metrics(prepared, matrix)
        rmse = float(np.sqrt(np.mean(np.square(np.concatenate((observed, model))))))
        return [{
            "hypothesis_id": hypotheses[0]["hypothesis_id"],
            "source": hypotheses[0]["source"],
            "matrix": matrix,
            "score": 1.0 / (1.0 + rmse),
            "coarse_metrics": {"rmse_m": rmse, "fitness": float(np.mean(observed <= 0.03))},
        }]

    def fine_register(self, prepared, coarse_results, _config):
        coarse = coarse_results[0]
        observed, model = self._metrics(prepared, coarse["matrix"])
        rmse = float(np.sqrt(np.mean(np.square(np.concatenate((observed, model))))))
        return [{
            **coarse,
            "score": 1.0 / (1.0 + rmse),
            "fine_metrics": {"rmse_m": rmse, "fitness": float(np.mean(observed <= 0.03))},
            "symmetry_equivalent": False,
        }]

    def nearest_neighbor_evidence(self, prepared, transform, _config):
        observed, model = self._metrics(prepared, transform)
        return {
            "observed_to_model_distances_m": observed.tolist(),
            "model_to_observed_distances_m": model.tolist(),
            "normal_cosines": None,
        }


def _legacy_mesh_reader(_path):
    return {
        "vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
        "faces": [[0, 1, 2]],
    }


def _prepare_legacy_cad_fixture(root):
    """独立旧 CAD 工件；不可与扫描夹具根目录混用。"""
    create_model_asset(
        root,
        model_id="legacy-cad-pump",
        display_name="旧 CAD 泵",
        category_id="pump",
        manufacturer="工程夹具",
        model_number="LEGACY-CAD",
        keywords=[],
        tags=[],
        principal=EXPERT,
        operation_id="legacy-cad-asset",
        request_id="legacy-cad-asset-request",
        idempotency_key="legacy-cad-asset-idem",
    )
    version = import_model_version(
        root,
        model_id="legacy-cad-pump",
        version_id="v1",
        source_path=Path(__file__).parent / "fixtures" / "models" / "minimal.obj",
        declared_unit="mm",
        license_name="工程夹具旧 CAD 授权",
        provenance={},
        principal=EXPERT,
        operation_id="legacy-cad-import",
        request_id="legacy-cad-import-request",
        idempotency_key="legacy-cad-import-idem",
        mesh_reader=_legacy_mesh_reader,
    )
    roots = ["models/legacy-cad-pump/versions/v1"]
    snapshot = _sha256_tree(root, roots)
    assert snapshot
    return version, roots, snapshot


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
    legacy_root = tmp_path / "independent-legacy-cad"
    legacy_root.mkdir()
    legacy_version, legacy_roots, legacy_before = _prepare_legacy_cad_fixture(legacy_root)
    history_groups = {
        "扫描版本": ["models/scan-positive/versions/v1", "models/scan-negative/versions/v1"],
        "扫描核验": ["models/scan-positive/reviews/v1", "models/scan-negative/reviews/v1"],
        "扫描发布": [
            "models/scan-positive/releases/release-scan-positive-v1",
            "models/scan-negative/releases/release-scan-negative-v1",
        ],
        "扫描表达": ["models/scan-positive/representations/v1", "models/scan-negative/representations/v1"],
        "扫描特征": ["models/scan-positive/features/v1", "models/scan-negative/features/v1"],
        "索引": ["models/feature_indexes/integration-index-v1"],
        "索引发布": ["models/feature_index_releases/integration-index-release-v1"],
        "候选": ["reports/model_retrieval/independent-scene/scene-release-1/obj-001/integration-retrieval-v1"],
        "配准": ["reports/model_registrations/independent-scene/scene-release-1/obj-001/integration-registration-v1"],
        "人工决定": ["reports/model_match_decisions/independent-scene/scene-release-1/obj-001/integration-decision-v1/decision.json"],
        "绑定": ["reports/model_match_decisions/independent-scene/scene-release-1/obj-001/integration-decision-v1/binding.json"],
    }
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
    released_history = _snapshot_immutable_groups(
        tmp_path, {name: history_groups[name] for name in ("扫描版本", "扫描核验", "扫描发布")}
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
    indexed_history = _snapshot_immutable_groups(
        tmp_path, {name: history_groups[name] for name in ("扫描表达", "扫描特征", "索引", "索引发布")}
    )

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
        engine_resolver=lambda _name: GeometryEvidenceRegistrationEngine(),
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
    transformed_model = _transform_points(
        frozen["model_points"], registration["rigid_transform_4x4"]
    )
    observed_points = np.asarray(frozen["object_points"], dtype=np.float64)
    observed_to_model = _nearest_neighbor_distances(observed_points, transformed_model)
    model_to_observed = _nearest_neighbor_distances(transformed_model, observed_points)
    np.testing.assert_allclose(
        registration["rigid_transform_4x4"],
        [[1.0, 0.0, 0.0, 1.9], [0.0, 1.0, 0.0, 2.525],
         [0.0, 0.0, 1.0, 3.27], [0.0, 0.0, 0.0, 1.0]],
        atol=0.001,
    )
    assert (model_to_observed <= 0.03).mean() == 1.0
    assert (observed_to_model <= 0.03).mean() == 1.0
    metrics = registration["residual_metrics"]
    assert metrics["observed_to_model_coverage"] == float((observed_to_model <= 0.03).mean())
    assert metrics["model_to_observed_coverage"] == float((model_to_observed <= 0.03).mean())
    assert np.isclose(
        metrics["inlier_rmse_m"],
        np.sqrt(np.mean(np.square(np.concatenate((observed_to_model, model_to_observed))))),
    )
    assert np.isclose(
        metrics["chamfer_distance_m"],
        (observed_to_model.mean() + model_to_observed.mean()) / 2.0,
    )
    assert decided["decision"]["decision"] == "confirmed"
    assert decided["binding"]["binding_id"] == "integration-binding-v1"
    assert decided["binding"]["model_id"] == "scan-positive"
    assert decided["binding"]["registration_id"] == registration["registration_id"]
    frozen_binding = decided["binding"]

    workflow_history = _snapshot_immutable_groups(
        tmp_path, {name: history_groups[name] for name in ("候选", "配准", "人工决定", "绑定")}
    )
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
    assert load_model_registration(
        tmp_path,
        asset_id="independent-scene",
        source_id="scene-release-1",
        instance_id="obj-001",
        registration_id="integration-registration-v1",
    )["report_fingerprint"] == registration["report_fingerprint"]
    assert load_decision_bundle(
        tmp_path,
        asset_id="independent-scene",
        source_id="scene-release-1",
        instance_id="obj-001",
        decision_id="integration-decision-v1",
    )["binding"] == frozen_binding
    assert _retrieve(tmp_path, retrieval_run_id="integration-retrieval-v3", sequence="v3")["candidates"][0]["model_id"] == "scan-positive"
    for history in (released_history, indexed_history, workflow_history):
        for name, before in history.items():
            assert _sha256_tree(tmp_path, history_groups[name]) == before
    assert load_model_version(legacy_root, "legacy-cad-pump", "v1") == legacy_version
    assert _sha256_tree(legacy_root, legacy_roots) == legacy_before
    assert not list((tmp_path / "models").rglob("*.obj"))
    assert not list((tmp_path / "models").rglob("*.stl"))
    assert all("cad_mesh" not in path.parts for path in (tmp_path / "models").rglob("*"))
