import copy

import pytest

from pc_system import mesh_reconstruction, mesh_release
from pc_system.mesh_reconstruction_config import build_mesh_quality_policy
from pc_system.mesh_release import (
    decide_mesh_run,
    list_mesh_releases,
    load_current_mesh_release,
    load_mesh_decision,
    mesh_object_key,
    release_mesh_run,
)
from pc_system.model_matching_audit import load_operation, read_operation_events
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal
from pc_system.scan_to_mesh_errors import ScanToMeshError

from test_mesh_reconstruction import (
    EXPERT,
    FakeWorker,
    _config,
    _create,
    _metrics,
    _policy,
    _source_snapshot,
)


SYSTEM = Principal("mesh-auto", frozenset(), "system")


def _install_dependencies(monkeypatch, *, auto_publish=False):
    source = _source_snapshot()
    config = _config()
    policy = _policy()
    if auto_publish:
        raw = {
            key: copy.deepcopy(policy[key])
            for key in (
                "schema_version",
                "policy_kind",
                "production_context",
                "requires_watertight",
                "hard_limits",
                "review_limits",
                "decisions",
            )
        }
        raw["decisions"]["auto_publish_passed"] = True
        policy = build_mesh_quality_policy("mesh-quality-v1", raw)
    monkeypatch.setattr(
        mesh_reconstruction,
        "freeze_mesh_source",
        lambda *args, **kwargs: copy.deepcopy(source),
    )
    monkeypatch.setattr(
        mesh_reconstruction,
        "load_mesh_config",
        lambda *args, **kwargs: copy.deepcopy(config),
    )
    monkeypatch.setattr(
        mesh_reconstruction,
        "load_mesh_quality_policy",
        lambda *args, **kwargs: copy.deepcopy(policy),
    )
    return policy


def _run(tmp_path, monkeypatch, *, run_id="mesh-run-1", coverage=1.0, auto=False):
    _install_dependencies(monkeypatch, auto_publish=auto)
    return _create(
        tmp_path,
        FakeWorker(metrics=_metrics(mesh_to_point_coverage=coverage)),
        run_id=run_id,
        operation_id=f"create-{run_id}",
    )


def _decide(
    root,
    *,
    run_id="mesh-run-1",
    decision="approve",
    acknowledgements=None,
    principal=EXPERT,
    sequence="1",
):
    return decide_mesh_run(
        root,
        run_id=run_id,
        decision=decision,
        reason="质量证据已核验",
        acknowledgements=[] if acknowledgements is None else acknowledgements,
        principal=principal,
        operation_id=f"mesh-decision-{sequence}",
        request_id=f"request-mesh-decision-{sequence}",
        idempotency_key=f"idem-mesh-decision-{sequence}",
    )


def _release(
    root,
    *,
    object_key,
    run_id="mesh-run-1",
    release_id="mesh-release-1",
    action="activate",
    expected=None,
    sequence="1",
):
    return release_mesh_run(
        root,
        object_key=object_key,
        run_id=run_id,
        release_id=release_id,
        action=action,
        reason="发布经核验的网格版本",
        expected_current_release_id=expected,
        principal=EXPERT,
        operation_id=f"mesh-release-op-{sequence}",
        request_id=f"request-mesh-release-{sequence}",
        idempotency_key=f"idem-mesh-release-{sequence}",
    )


def test_passed_run_requires_expert_when_auto_publish_is_disabled(
    tmp_path, monkeypatch
):
    _run(tmp_path, monkeypatch)

    with pytest.raises(ScanToMeshError) as caught:
        _decide(tmp_path, principal=SYSTEM)
    assert caught.value.code == "mesh_decision_not_allowed"

    decision = _decide(tmp_path, sequence="2")

    assert decision["decision"] == "approve"
    assert decision["automated"] is False
    assert load_mesh_decision(tmp_path, "mesh-run-1") == decision


def test_passed_run_can_receive_system_approval_only_when_policy_allows_it(
    tmp_path, monkeypatch
):
    _run(tmp_path, monkeypatch, auto=True)

    decision = _decide(tmp_path, principal=SYSTEM)

    assert decision["decision"] == "approve"
    assert decision["automated"] is True


def test_review_required_approval_must_acknowledge_every_risk(
    tmp_path, monkeypatch
):
    run = _run(tmp_path, monkeypatch, coverage=0.85)
    risks = run["status"]
    assert risks == "review_required"

    with pytest.raises(ScanToMeshError) as caught:
        _decide(tmp_path, acknowledgements=[])
    assert caught.value.code == "mesh_decision_acknowledgement_required"

    decision = _decide(
        tmp_path,
        acknowledgements=["point_coverage_review"],
        sequence="2",
    )
    assert decision["acknowledgements"] == ["point_coverage_review"]


def test_rejected_run_cannot_be_approved_and_has_one_final_rejection(
    tmp_path, monkeypatch
):
    _run(tmp_path, monkeypatch, coverage=0.70)

    with pytest.raises(ScanToMeshError) as caught:
        _decide(tmp_path)
    assert caught.value.code == "mesh_decision_not_allowed"

    rejected = _decide(tmp_path, decision="reject", sequence="2")
    with pytest.raises(ScanToMeshError) as conflict:
        _decide(tmp_path, decision="reject", sequence="3")

    assert conflict.value.code == "mesh_decision_conflict"
    assert load_mesh_decision(tmp_path, "mesh-run-1") == rejected


def test_activate_replace_and_rollback_preserve_history_and_use_cas(
    tmp_path, monkeypatch
):
    first_run = _run(tmp_path, monkeypatch, run_id="mesh-run-1")
    _decide(tmp_path, run_id="mesh-run-1", sequence="1")
    object_key = mesh_object_key(first_run)
    first = _release(tmp_path, object_key=object_key)
    assert [
        event["event_type"]
        for event in read_operation_events(tmp_path, "mesh-release-op-1")
    ] == [
        "operation.started",
        "mesh.release_created",
        "mesh.release_head_changed",
        "operation.completed",
    ]

    _run(tmp_path, monkeypatch, run_id="mesh-run-2")
    _decide(tmp_path, run_id="mesh-run-2", sequence="2")
    second = _release(
        tmp_path,
        object_key=object_key,
        run_id="mesh-run-2",
        release_id="mesh-release-2",
        action="replace",
        expected="mesh-release-1",
        sequence="2",
    )

    with pytest.raises(ScanToMeshError) as stale:
        _release(
            tmp_path,
            object_key=object_key,
            run_id="mesh-run-1",
            release_id="mesh-release-stale",
            action="replace",
            expected="mesh-release-1",
            sequence="stale",
        )
    assert stale.value.code == "mesh_release_conflict"
    assert load_current_mesh_release(tmp_path, object_key) == second

    rollback = _release(
        tmp_path,
        object_key=object_key,
        run_id="mesh-run-1",
        release_id="mesh-release-3",
        action="rollback",
        expected="mesh-release-2",
        sequence="3",
    )

    assert rollback["rollback_of_release_id"] == first["release_id"]
    assert rollback["previous_release_id"] == second["release_id"]
    assert load_current_mesh_release(tmp_path, object_key) == rollback
    assert [item["release_id"] for item in list_mesh_releases(tmp_path, object_key)] == [
        "mesh-release-1",
        "mesh-release-2",
        "mesh-release-3",
    ]


def test_rejected_decision_cannot_be_released(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    _decide(tmp_path, decision="reject")

    with pytest.raises(ScanToMeshError) as caught:
        _release(tmp_path, object_key=mesh_object_key(run))

    assert caught.value.code == "mesh_release_not_allowed"
    assert load_current_mesh_release(tmp_path, mesh_object_key(run)) is None


def test_release_recovers_projection_after_completed_response_loss(
    tmp_path, monkeypatch
):
    run = _run(tmp_path, monkeypatch)
    _decide(tmp_path)
    object_key = mesh_object_key(run)
    real_complete = mesh_release.complete_operation
    failed = False

    def complete_then_raise(root, operation_id, result):
        nonlocal failed
        completed = real_complete(root, operation_id, result)
        if not failed:
            failed = True
            raise ModelMatchingError("audit_persistence_error", "模拟完成响应丢失")
        return completed

    monkeypatch.setattr(mesh_release, "complete_operation", complete_then_raise)
    with pytest.raises(ScanToMeshError) as caught:
        _release(tmp_path, object_key=object_key)
    assert caught.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "mesh-release-op-1")["status"] == "completed"
    monkeypatch.setattr(mesh_release, "complete_operation", real_complete)

    release = _release(tmp_path, object_key=object_key)

    assert load_current_mesh_release(tmp_path, object_key) == release
    assert len(list_mesh_releases(tmp_path, object_key)) == 1


def test_release_recovers_running_candidate_after_business_event_failure(
    tmp_path, monkeypatch
):
    run = _run(tmp_path, monkeypatch)
    _decide(tmp_path)
    object_key = mesh_object_key(run)
    real_ensure = mesh_release.ensure_operation_event
    failed = False

    def fail_once(root, operation_id, event_type, details):
        nonlocal failed
        if event_type == "mesh.release_created" and not failed:
            failed = True
            raise ModelMatchingError("audit_persistence_error", "模拟业务事件中断")
        return real_ensure(root, operation_id, event_type, details)

    monkeypatch.setattr(mesh_release, "ensure_operation_event", fail_once)
    with pytest.raises(ScanToMeshError) as caught:
        _release(tmp_path, object_key=object_key)
    assert caught.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "mesh-release-op-1")["status"] == "running"
    monkeypatch.setattr(mesh_release, "ensure_operation_event", real_ensure)

    release = _release(tmp_path, object_key=object_key)

    assert load_current_mesh_release(tmp_path, object_key) == release
    assert len(list_mesh_releases(tmp_path, object_key)) == 1


def test_completed_unprojected_successor_blocks_stale_current_head(
    tmp_path, monkeypatch
):
    first_run = _run(tmp_path, monkeypatch, run_id="mesh-run-1")
    _decide(tmp_path, run_id="mesh-run-1", sequence="1")
    object_key = mesh_object_key(first_run)
    _release(tmp_path, object_key=object_key)
    _run(tmp_path, monkeypatch, run_id="mesh-run-2")
    _decide(tmp_path, run_id="mesh-run-2", sequence="2")
    real_complete = mesh_release.complete_operation
    failed = False

    def complete_then_raise(root, operation_id, result):
        nonlocal failed
        completed = real_complete(root, operation_id, result)
        if operation_id == "mesh-release-op-2" and not failed:
            failed = True
            raise ModelMatchingError("audit_persistence_error", "模拟新头投影前中断")
        return completed

    monkeypatch.setattr(mesh_release, "complete_operation", complete_then_raise)
    with pytest.raises(ScanToMeshError):
        _release(
            tmp_path,
            object_key=object_key,
            run_id="mesh-run-2",
            release_id="mesh-release-2",
            action="replace",
            expected="mesh-release-1",
            sequence="2",
        )

    with pytest.raises(ScanToMeshError) as caught:
        load_current_mesh_release(tmp_path, object_key)

    assert caught.value.code == "mesh_release_integrity_error"
