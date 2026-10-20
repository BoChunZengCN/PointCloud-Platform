import copy
import json

from fastapi.testclient import TestClient

from pc_system import mesh_reconstruction
from pc_system.api import create_app
from pc_system.cli import main
from pc_system.mesh_release import mesh_object_key
from pc_system.mesh_reconstruction import load_mesh_run

from test_mesh_reconstruction import FakeWorker, _source_snapshot
from test_mesh_reconstruction_config import POISSON_CONFIG, QUALITY_POLICY


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _identity(sequence):
    return [
        "--operation-id", f"operation-{sequence}",
        "--request-id", f"request-{sequence}",
        "--idempotency-key", f"idem-{sequence}",
    ]


def _prepare(root, monkeypatch, capsys):
    monkeypatch.setattr(
        mesh_reconstruction,
        "freeze_mesh_source",
        lambda *args, **kwargs: copy.deepcopy(_source_snapshot()),
    )
    config = _write_json(root / "config.json", POISSON_CONFIG)
    policy = _write_json(root / "policy.json", QUALITY_POLICY)
    assert main([
        "mesh-config-publish", "--project-root", str(root), "--config-id", "mesh-poisson-v1",
        "--config", str(config), "--actor", "mesh-expert", *_identity("config"),
    ]) == 0
    capsys.readouterr()
    assert main([
        "mesh-policy-publish", "--project-root", str(root), "--policy-id", "mesh-quality-v1",
        "--policy", str(policy), "--actor", "mesh-expert", *_identity("policy"),
    ]) == 0
    capsys.readouterr()


def test_cli_runs_decides_releases_and_reuses_catalog_projection(tmp_path, monkeypatch, capsys):
    """CLI 若复制状态机或绕过角色裁剪，该完整流程将与目录结果不一致。"""
    _prepare(tmp_path, monkeypatch, capsys)
    worker = FakeWorker()
    run_args = [
        "scan-to-mesh-run", "--project-root", str(tmp_path), "--run-id", "mesh-run-1",
        "--source-kind", "phase14_correction_release", "--asset-id", "scan",
        "--source-release-id", "release-1", "--instance-id", "pipe-1",
        "--config-id", "mesh-poisson-v1", "--policy-id", "mesh-quality-v1",
        "--actor", "mesh-expert", *_identity("run"),
    ]
    assert main(run_args, mesh_worker=worker, scan_to_mesh_cursor_secret=b"cli-secret") == 0
    created = json.loads(capsys.readouterr().out)

    assert main([
        "scan-to-mesh-list", "--project-root", str(tmp_path), "--actor", "mesh-operator",
        "--role", "operator",
    ], scan_to_mesh_cursor_secret=b"cli-secret") == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert main([
        "scan-to-mesh-show", "--project-root", str(tmp_path), "--run-id", "mesh-run-1",
        "--actor", "mesh-auditor", "--role", "auditor",
    ], scan_to_mesh_cursor_secret=b"cli-secret") == 0
    audited = json.loads(capsys.readouterr().out)
    assert audited["audit"]["operation_id"] == "operation-run"

    assert main([
        "scan-to-mesh-decide", "--project-root", str(tmp_path), "--run-id", "mesh-run-1",
        "--decision", "approve", "--reason", "质量证据已核验", "--actor", "mesh-expert",
        *_identity("decision"),
    ], scan_to_mesh_cursor_secret=b"cli-secret") == 0
    decided = json.loads(capsys.readouterr().out)
    object_key = mesh_object_key(load_mesh_run(tmp_path, "mesh-run-1"))
    assert decided["object_key"] == object_key
    assert main([
        "mesh-release", "--project-root", str(tmp_path), "--object-key", object_key,
        "--run-id", "mesh-run-1", "--release-id", "mesh-release-1", "--action", "activate",
        "--reason", "发布经核验网格", "--actor", "mesh-expert", *_identity("release"),
    ], scan_to_mesh_cursor_secret=b"cli-secret") == 0
    released = json.loads(capsys.readouterr().out)
    assert released["release_id"] == "mesh-release-1"

    assert main([
        "scan-to-mesh-list", "--project-root", str(tmp_path), "--actor", "mesh-operator",
        "--role", "operator",
    ], scan_to_mesh_cursor_secret=b"cli-secret") == 0
    operator = json.loads(capsys.readouterr().out)
    assert operator["items"][0]["current_release_id"] == "mesh-release-1"
    assert "artifacts" not in operator["items"][0]
    assert created["run_id"] == operator["items"][0]["run_id"]
    api_operator = TestClient(
        create_app(
            tmp_path,
            run_mode="development",
            scan_to_mesh_cursor_secret=b"cli-secret",
        )
    ).get(
        "/scan-to-mesh/runs",
        headers={"X-Actor-ID": "mesh-operator", "X-Actor-Roles": "operator"},
    )
    assert api_operator.status_code == 200
    assert api_operator.json() == operator


def test_cli_reports_same_stable_catalog_error_and_rejects_unsafe_json(tmp_path, monkeypatch, capsys):
    """CLI 必须保留领域错误码，且配置 JSON 的重复字段不能被解析器吞掉。"""
    _prepare(tmp_path, monkeypatch, capsys)
    bad = tmp_path / "bad-config.json"
    bad.write_text('{"schema_version":"1.0","schema_version":"2.0"}', encoding="utf-8")

    assert main([
        "mesh-config-publish", "--project-root", str(tmp_path), "--config-id", "bad-config",
        "--config", str(bad), "--actor", "mesh-expert", *_identity("bad-config"),
    ]) == 2
    assert capsys.readouterr().err.startswith("mesh_config_invalid:")

    assert main([
        "scan-to-mesh-list", "--project-root", str(tmp_path), "--actor", "mesh-expert",
        "--role", "expert", "--cursor", "forged-cursor",
    ], scan_to_mesh_cursor_secret=b"cli-secret") == 2
    assert capsys.readouterr().err.startswith("scan_to_mesh_catalog_invalid:")

    assert main([
        "scan-to-mesh-list", "--project-root", str(tmp_path), "--actor", "mesh-expert",
        "--role", "expert", "--status", "unknown-status",
    ], scan_to_mesh_cursor_secret=b"cli-secret") == 2
    assert capsys.readouterr().err.startswith("scan_to_mesh_catalog_invalid:")

    assert main([
        "scan-to-mesh-run", "--project-root", str(tmp_path), "--run-id", "bad-source-run",
        "--source-kind", "local_file", "--config-id", "mesh-poisson-v1",
        "--policy-id", "mesh-quality-v1", "--actor", "mesh-expert", *_identity("bad-source"),
    ], mesh_worker=FakeWorker()) == 2
    assert capsys.readouterr().err.startswith("mesh_run_invalid:")
