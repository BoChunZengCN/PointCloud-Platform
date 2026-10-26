"""完整 LAS 预览本机命令的参数与分发。"""

import json
from pc_system.cli import main


def test_prepare_command_dispatches_explicit_source_and_converter(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_prepare(project_root, source_path, asset_id, converter_path):
        seen.update(project_root=project_root, source_path=source_path, asset_id=asset_id, converter_path=converter_path)
        return type("Published", (), {"asset_id": asset_id, "run_id": "run1", "metadata_url": "/metadata.json"})()

    monkeypatch.setattr("pc_system.commands.full_preview.prepare_full_preview", fake_prepare)
    assert main([
        "prepare-full-preview", "--project-root", str(tmp_path),
        "--source-las", str(tmp_path / "machine zoom.las"),
        "--asset-id", "machine-zoom", "--converter-path", str(tmp_path / "converter.exe"),
    ]) == 0
    assert seen["source_path"] == tmp_path / "machine zoom.las"
    assert seen["converter_path"] == tmp_path / "converter.exe"
    assert json.loads(capsys.readouterr().out)["run_id"] == "run1"


def test_list_and_activate_commands_are_exposed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("pc_system.commands.full_preview.list_preview_versions", lambda root, asset: [])
    monkeypatch.setattr(
        "pc_system.commands.full_preview.activate_preview_version",
        lambda root, asset, run: type("Published", (), {"asset_id": asset, "run_id": run, "metadata_url": "/old/metadata.json"})(),
    )
    assert main(["list-full-preview-versions", "--project-root", str(tmp_path), "--asset-id", "machine-zoom"]) == 0
    assert json.loads(capsys.readouterr().out) == {"asset_id": "machine-zoom", "versions": []}
    assert main([
        "activate-full-preview-version", "--project-root", str(tmp_path),
        "--asset-id", "machine-zoom", "--run-id", "old-run",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "old-run"


def test_serve_command_binds_local_server_with_explicit_viewer_root(monkeypatch, tmp_path, capsys):
    seen = {}

    class FakeServer:
        server_port = 8123

        def serve_forever(self):
            seen["served"] = True

        def server_close(self):
            seen["closed"] = True

    def fake_create(project_root, frontend_root, host, port, *, viewer_root):
        seen.update(project_root=project_root, frontend_root=frontend_root, host=host, port=port, viewer_root=viewer_root)
        return FakeServer()

    monkeypatch.setattr("pc_system.commands.full_preview.create_preview_server", fake_create)
    assert main([
        "serve-full-preview", "--project-root", str(tmp_path),
        "--frontend-root", str(tmp_path / "frontend"),
        "--viewer-root", str(tmp_path / "potree"), "--port", "8123",
    ]) == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["served"] and seen["closed"]
    assert "http://127.0.0.1:8123/" in capsys.readouterr().out
