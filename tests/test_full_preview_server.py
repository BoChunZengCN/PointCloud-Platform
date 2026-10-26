"""完整 LAS 只读服务：精确 Range、版本 URL 与路径隔离。"""

import http.client
import json
import threading
import pytest


@pytest.fixture
def preview_server(tmp_path):
    from pc_system.full_preview_server import create_preview_server

    project = tmp_path / "project"
    project.mkdir()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "full-las-preview.html").write_text("<h1>preview</h1>", encoding="utf-8")
    root = project / "workspace/previews/machine-zoom"
    run = root / "runs/run-one"
    run.mkdir(parents=True)
    manifest = {
        "schema": 1, "status": "ready", "asset_id": "machine-zoom", "run_id": "run-one",
        "created_at": "2026-10-26T00:00:00+00:00",
        "metadata_url": "/previews/machine-zoom/runs/run-one/metadata.json",
        "source_point_count": 42, "source_format": "las", "representation_kind": "point_cloud",
        "viewer_format": "potree2", "source_unit": "m", "rgb_available": True,
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "metadata.json").write_text('{"points":42}', encoding="utf-8")
    (run / "hierarchy.bin").write_bytes(b"abcdefghijklmnopqrstuv")
    (run / "octree.bin").write_bytes(bytes(range(100)))
    (root / "active.json").write_text(
        json.dumps({"schema": 1, "asset_id": "machine-zoom", "run_id": "run-one"}), encoding="utf-8"
    )
    server = create_preview_server(project, frontend)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server, run
    server.shutdown()
    server.server_close()
    worker.join(timeout=3)


def _get(server, path, *, range_header=None, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    headers = {}
    if range_header is not None:
        headers["Range"] = range_header
    if host is not None:
        headers["Host"] = host
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_exact_single_range_and_catalog(preview_server):
    server, _run = preview_server
    path = "/previews/machine-zoom/runs/run-one/octree.bin"
    status, headers, data = _get(server, path, range_header="bytes=10-19")
    assert status == 206
    assert headers["Content-Range"] == "bytes 10-19/100"
    assert headers["Content-Length"] == "10"
    assert data == bytes(range(10, 20))
    status, _headers, data = _get(server, "/api/full-previews")
    assert status == 200
    assert json.loads(data)["previews"][0]["source_point_count"] == 42
    assert b"source_path" not in data


@pytest.mark.parametrize("range_header", [None, "bytes=0-9,20-29", "bytes=100-101", "bytes=-10", "bytes=0-999"])
def test_binary_rejects_missing_multirange_or_out_of_bounds(preview_server, range_header):
    server, _run = preview_server
    status, _headers, data = _get(
        server, "/previews/machine-zoom/runs/run-one/octree.bin", range_header=range_header
    )
    assert status == 416
    assert data != bytes(range(100))


def test_unpublished_run_and_path_traversal_are_not_served(preview_server):
    server, _run = preview_server
    for path in (
        "/previews/machine-zoom/runs/not-ready/octree.bin",
        "/previews/machine-zoom/runs/run-one/%2e%2e/metadata.json",
        "/frontend/%2e%2e/secret.txt",
        "/previews/machine-zoom/runs/run-one/source.las",
    ):
        status, _headers, _data = _get(server, path, range_header="bytes=0-9")
        assert status in (400, 404)


def test_foreign_host_rejected(preview_server):
    server, _run = preview_server
    status, _headers, _data = _get(server, "/api/full-previews", host="evil.example")
    assert status == 403


def test_static_frontend_and_metadata_are_small_allowed_resources(preview_server):
    server, _run = preview_server
    assert _get(server, "/frontend/full-las-preview.html")[2] == b"<h1>preview</h1>"
    assert _get(server, "/previews/machine-zoom/runs/run-one/metadata.json")[2] == b'{"points":42}'


def test_reparse_artifact_is_not_served(preview_server, tmp_path):
    server, run = preview_server
    (run / "octree.bin").unlink()
    target = tmp_path / "secret.bin"
    target.write_bytes(b"secret")
    try:
        (run / "octree.bin").symlink_to(target)
    except OSError:
        pytest.skip("当前 Windows 环境未允许创建测试符号链接")
    status, _headers, data = _get(
        server, "/previews/machine-zoom/runs/run-one/octree.bin", range_header="bytes=0-5"
    )
    assert status == 404
    assert data != b"secret"


def test_reparse_static_root_is_rejected_at_startup(tmp_path):
    from pc_system.full_preview_server import create_preview_server

    project = tmp_path / "project"
    project.mkdir()
    real = tmp_path / "real-frontend"
    real.mkdir()
    linked = tmp_path / "linked-frontend"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 环境未允许创建测试符号链接")
    with pytest.raises(ValueError, match="重解析|符号"):
        create_preview_server(project, linked)


def test_client_disconnect_during_range_stream_is_normal(preview_server):
    server, run = preview_server
    handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
    handler.headers = {"Range": "bytes=0-99"}
    handler.send_response = lambda *args: None
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None

    class Disconnected:
        def write(self, _data):
            raise ConnectionAbortedError("browser closed the request")

    handler.wfile = Disconnected()
    handler._serve_range(run / "octree.bin")


def test_unverified_viewer_bundle_is_rejected_at_startup(tmp_path):
    from pc_system.full_preview_server import create_preview_server

    project = tmp_path / "project"
    frontend = tmp_path / "frontend"
    viewer = tmp_path / "viewer"
    project.mkdir()
    frontend.mkdir()
    (viewer / "build/potree").mkdir(parents=True)
    (viewer / "build/potree/potree.js").write_bytes(b"wrong viewer")
    with pytest.raises(ValueError, match="查看器.*指纹|查看器.*版本"):
        create_preview_server(project, frontend, viewer_root=viewer)
