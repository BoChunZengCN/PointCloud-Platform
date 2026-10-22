"""两类真实上游工件到网格发布历史的工程夹具验收。"""

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phase15e_support import prepared_scan, review_request
from pc_system import mesh_worker_deployment
from pc_system.api import create_app
from pc_system.cli import main
from pc_system.mesh_reconstruction import create_mesh_run, load_mesh_run
from pc_system.mesh_reconstruction_config import (
    publish_mesh_config,
    publish_mesh_quality_policy,
)
from pc_system.mesh_release import (
    decide_mesh_run,
    list_mesh_releases,
    load_current_mesh_release,
    mesh_object_key,
    release_mesh_run,
)
from pc_system.model_matching_identity import Principal
from pc_system.reference_review import review_reference_version
from pc_system.scan_to_mesh_errors import ScanToMeshError

from test_mesh_reconstruction import FakeWorker, _metrics
from test_mesh_reconstruction_config import (
    BALL_PIVOTING_CONFIG,
    POISSON_CONFIG,
    QUALITY_POLICY,
)
from test_scan_to_mesh_source import _phase14_release


EXPERT = Principal("mesh-integration-expert", frozenset({"expert"}), "configured_token")


def _hash_files(directory):
    return {
        path.relative_to(directory): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*") if path.is_file()
    }


def _source_and_upstream(root, source_kind):
    if source_kind == "phase14_correction_release":
        directory = _phase14_release(root)
        return (
            {"asset_id": "plant-scan", "release_id": "release-1", "instance_id": "pipe-1"},
            list(directory.glob("*.json")),
        )
    prepared_scan(root)
    review_reference_version(root, **review_request())
    source_files = [root / "v1.ply"]
    source_files.extend(path for path in (root / "models" / "scan-pump").rglob("*") if path.is_file())
    return {"model_id": "scan-pump", "version_id": "v1"}, source_files


def _publish_configuration(root):
    for config_id, config in (
        ("poisson-v1", POISSON_CONFIG),
        ("bpa-v1", BALL_PIVOTING_CONFIG),
    ):
        publish_mesh_config(
            root,
            config_id=config_id,
            config=copy.deepcopy(config),
            principal=EXPERT,
            operation_id=f"publish-{config_id}",
            request_id=f"request-{config_id}",
            idempotency_key=f"idem-{config_id}",
        )
    publish_mesh_quality_policy(
        root,
        policy_id="quality-v1",
        policy=copy.deepcopy(QUALITY_POLICY),
        principal=EXPERT,
        operation_id="publish-quality-v1",
        request_id="request-quality-v1",
        idempotency_key="idem-quality-v1",
    )


def _decision(root, run_id, sequence, *, acknowledgements=()):
    return decide_mesh_run(
        root,
        run_id=run_id,
        decision="approve",
        reason="已核对来源、网格和质量证据",
        acknowledgements=list(acknowledgements),
        principal=EXPERT,
        operation_id=f"decision-{sequence}",
        request_id=f"request-decision-{sequence}",
        idempotency_key=f"idem-decision-{sequence}",
    )


def _release(root, object_key, run_id, sequence, action, expected):
    return release_mesh_run(
        root,
        object_key=object_key,
        run_id=run_id,
        release_id=f"release-mesh-{sequence}",
        action=action,
        reason="已核验网格发布历史",
        expected_current_release_id=expected,
        principal=EXPERT,
        operation_id=f"release-operation-{sequence}",
        request_id=f"request-release-{sequence}",
        idempotency_key=f"idem-release-{sequence}",
    )


@pytest.mark.parametrize(
    "source_kind", ["phase14_correction_release", "phase15e_scanned_reference"]
)
def test_reviewed_sources_flow_through_both_algorithms_quality_and_immutable_history(
    tmp_path, source_kind
):
    """来源误选、算法切换、门禁绕过、历史覆写或上游改写都会失败。"""
    source_ref, upstream_files = _source_and_upstream(tmp_path, source_kind)
    before = {path.relative_to(tmp_path): hashlib.sha256(path.read_bytes()).hexdigest() for path in upstream_files}
    _publish_configuration(tmp_path)

    runs = []
    for sequence, config_id, coverage, status in (
        ("passed", "poisson-v1", 1.0, "passed"),
        ("review", "bpa-v1", 0.85, "review_required"),
        ("rejected", "poisson-v1", 0.70, "rejected"),
    ):
        worker = FakeWorker(metrics=_metrics(mesh_to_point_coverage=coverage))
        run = create_mesh_run(
            tmp_path,
            run_id=f"run-{sequence}",
            source_kind=source_kind,
            source_ref=source_ref,
            config_id=config_id,
            policy_id="quality-v1",
            principal=EXPERT,
            operation_id=f"run-operation-{sequence}",
            request_id=f"request-run-{sequence}",
            idempotency_key=f"idem-run-{sequence}",
            worker=worker,
        )
        request = json.loads(
            (tmp_path / "scan_to_cad" / "mesh_runs" / run["run_id"] / "worker_request.json").read_text(encoding="utf-8")
        )
        assert run["status"] == status
        assert run["source_kind"] == source_kind
        assert run["source_snapshot_fingerprint"] == request["source_snapshot_fingerprint"]
        assert request["algorithm"] == ("ball_pivoting" if config_id == "bpa-v1" else "poisson")
        assert load_mesh_run(tmp_path, run["run_id"]) == run
        assert worker.calls == 1
        runs.append(run)

    object_key = mesh_object_key(runs[0])
    assert all(mesh_object_key(run) == object_key for run in runs)
    _decision(tmp_path, "run-passed", "passed")
    _decision(tmp_path, "run-review", "review", acknowledgements=["point_coverage_review"])

    with pytest.raises(ScanToMeshError) as denied:
        _decision(tmp_path, "run-rejected", "rejected")
    assert denied.value.code == "mesh_decision_not_allowed"

    first = _release(tmp_path, object_key, "run-passed", "one", "activate", None)
    first_release_dir = tmp_path / "scan_to_cad" / "mesh_objects" / object_key / "releases" / first["release_id"]
    first_release_hashes = _hash_files(first_release_dir)
    first_run_dir = tmp_path / "scan_to_cad" / "mesh_runs" / "run-passed"
    first_run_hashes = _hash_files(first_run_dir)
    assert first_release_hashes and first_run_hashes
    second = _release(tmp_path, object_key, "run-review", "two", "replace", first["release_id"])
    assert _hash_files(first_release_dir) == first_release_hashes
    assert _hash_files(first_run_dir) == first_run_hashes
    second_release_dir = tmp_path / "scan_to_cad" / "mesh_objects" / object_key / "releases" / second["release_id"]
    second_release_hashes = _hash_files(second_release_dir)
    third = _release(tmp_path, object_key, "run-passed", "three", "rollback", second["release_id"])
    assert _hash_files(first_release_dir) == first_release_hashes
    assert _hash_files(second_release_dir) == second_release_hashes
    assert _hash_files(first_run_dir) == first_run_hashes

    assert [item["release_id"] for item in list_mesh_releases(tmp_path, object_key)] == [
        first["release_id"], second["release_id"], third["release_id"]
    ]
    assert third["rollback_of_release_id"] == first["release_id"]
    assert load_current_mesh_release(tmp_path, object_key) == third
    assert {path.relative_to(tmp_path): hashlib.sha256(path.read_bytes()).hexdigest() for path in upstream_files} == before


def test_admin_worker_configuration_reaches_default_api_and_cli_entrypoints(
    tmp_path, monkeypatch, capsys
):
    """常规部署入口若忽略管理员配置，真实用户无法启动网格运行。"""
    _phase14_release(tmp_path)
    _publish_configuration(tmp_path)
    script = tmp_path / "worker.py"
    script.write_text("# 测试固定路径；执行由确定性假工作器取代\n", encoding="utf-8")
    monkeypatch.setenv("PC_SYSTEM_MESH_WORKER_PYTHON", str(Path(sys.executable).resolve()))
    monkeypatch.setenv("PC_SYSTEM_MESH_WORKER_SCRIPT", str(script))
    worker = FakeWorker()
    monkeypatch.setattr(mesh_worker_deployment, "Open3DSubprocessWorker", lambda command: worker)

    client = TestClient(create_app(tmp_path, run_mode="development"))
    response = client.post(
        "/scan-to-mesh/runs",
        headers={"X-Actor-ID": "mesh-integration-expert", "X-Actor-Roles": "expert"},
        json={
            "run_id": "run-api",
            "source_kind": "phase14_correction_release",
            "source_ref": {"asset_id": "plant-scan", "release_id": "release-1", "instance_id": "pipe-1"},
            "config_id": "poisson-v1",
            "policy_id": "quality-v1",
            "operation_id": "operation-api",
            "request_id": "request-api",
            "idempotency_key": "idem-api",
        },
    )
    assert response.status_code == 200
    assert response.json()["run_id"] == "run-api"

    code = main([
        "scan-to-mesh-run", "--project-root", str(tmp_path), "--run-id", "run-cli",
        "--source-kind", "phase14_correction_release", "--asset-id", "plant-scan",
        "--source-release-id", "release-1", "--instance-id", "pipe-1",
        "--config-id", "bpa-v1", "--policy-id", "quality-v1",
        "--actor", "mesh-integration-expert", "--operation-id", "operation-cli",
        "--request-id", "request-cli", "--idempotency-key", "idem-cli",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "run-cli"
    assert worker.calls == 2


def test_default_api_worker_executes_only_admin_script_in_real_subprocess(
    tmp_path, monkeypatch
):
    """普通入口必须经固定子进程读取受控请求，不能接受业务指定命令。"""
    _phase14_release(tmp_path)
    _publish_configuration(tmp_path)
    script = Path(__file__).parent / "fixtures" / "mesh_worker_subprocess_stub.py"
    trace = tmp_path / "subprocess-trace.json"
    monkeypatch.setenv("PC_SYSTEM_MESH_WORKER_PYTHON", str(Path(sys.executable).resolve()))
    monkeypatch.setenv("PC_SYSTEM_MESH_WORKER_SCRIPT", str(script.resolve()))
    monkeypatch.setenv("PC_SYSTEM_MESH_STUB_TRACE", str(trace))
    source_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join((str(source_root / "src"), str(source_root / "tests"))),
    )
    client = TestClient(create_app(tmp_path, run_mode="development"))
    request = {
        "run_id": "run-subprocess",
        "source_kind": "phase14_correction_release",
        "source_ref": {"asset_id": "plant-scan", "release_id": "release-1", "instance_id": "pipe-1"},
        "config_id": "poisson-v1",
        "policy_id": "quality-v1",
        "operation_id": "operation-subprocess",
        "request_id": "request-subprocess",
        "idempotency_key": "idem-subprocess",
    }
    headers = {"X-Actor-ID": "mesh-integration-expert", "X-Actor-Roles": "expert"}
    rejected = client.post("/scan-to-mesh/runs", headers=headers, json={**request, "worker_command": "other.py"})
    assert rejected.status_code == 400
    assert not trace.exists()

    response = client.post("/scan-to-mesh/runs", headers=headers, json=request)

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-subprocess"
    invocation = json.loads(trace.read_text(encoding="utf-8"))
    assert invocation["argv"] == ["--request", "worker_request.json"]
    assert invocation["algorithm"] == "poisson"
    assert invocation["script"] == str(script.resolve())
    assert Path(invocation["cwd"]) == tmp_path / "scan_to_cad" / "mesh_runs" / "run-subprocess"
