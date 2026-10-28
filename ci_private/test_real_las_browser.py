"""只由受控 CI 显式运行的私有真实 LAS 浏览器验收。"""

import os
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

from pc_system.full_preview_publication import get_active_preview
from pc_system.full_preview_server import create_preview_server


SOURCE_POINTS = 8_394_852
SOURCE_SHA256 = "ee1ac0dc4f49f9634d803d4a62313fda903d4d07d08dc86c5bd4afd040bffb69"
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _private_preview():
    values = {
        "project_root": os.environ.get("PRIVATE_REAL_LAS_PROJECT_ROOT"),
        "viewer_root": os.environ.get("PRIVATE_REAL_LAS_VIEWER_ROOT"),
        "asset_id": os.environ.get("PRIVATE_REAL_LAS_ASSET_ID"),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        pytest.fail(f"私有样本验收环境缺失：{', '.join(missing)}")
    project_root = Path(values["project_root"])
    viewer_root = Path(values["viewer_root"])
    published = get_active_preview(project_root, values["asset_id"])
    if published is None:
        pytest.fail("私有样本没有已发布的完整预览版本。")
    return project_root, viewer_root, published


def test_private_preview_publication_matches_complete_source():
    _project_root, _viewer_root, published = _private_preview()
    manifest = published.manifest
    assert manifest["source_point_count"] == SOURCE_POINTS
    assert manifest["source_sha256"] == SOURCE_SHA256
    assert manifest["source_unit"] == "m"
    assert manifest["rgb_available"] is True
    assert manifest["viewer_format"] == "potree2"


def test_private_preview_browser_loads_rgb_and_range_without_las(page):
    project_root, viewer_root, published = _private_preview()
    server = create_preview_server(project_root, FRONTEND, viewer_root=viewer_root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    seen = []
    page.on("response", lambda response: seen.append((response.url, response.status)))
    try:
        page.goto(
            f"http://127.0.0.1:{server.server_port}/frontend/full-las-preview.html"
            f"?asset_id={published.asset_id}"
        )
        expect(page.locator("#run-id")).to_have_text(published.run_id)
        expect(page.locator("#source-points")).to_have_text("8,394,852")
        expect(page.locator("#rgb-state")).to_have_text("已记录")
        expect(page.locator("#load-status")).to_have_attribute("data-state", "ready", timeout=120000)
        assert int(page.locator("#visible-points").inner_text().replace(",", "")) > 0
        assert any("hierarchy.bin" in url and status == 206 for url, status in seen)
        assert any("octree.bin" in url and status == 206 for url, status in seen)
        assert not any(url.endswith(".las") for url, _status in seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
