"""有本机官方资源和真实小样本时验证 Potree 页面与 Range 请求。"""

import threading
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import expect

from pc_system.full_preview_server import create_preview_server


ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(
    subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True,
    ).strip()
).parent
VIEWER = PROJECT / "workspace/potree-viewer-1.8.2/Potree_1.8.2"
RUN_ID = "8ad306aee5a34274a4733a0b02e86991"
ASSET_ID = "machine-zoom-s50"
FULL_RUN_ID = "c2f11101312e404d986dcad02e69c05e"


def test_real_preview_assets_use_shared_repository_root():
    common_dir = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True,
    ).strip()
    assert PROJECT == Path(common_dir).parent


def test_real_small_preview_renders_rgb_with_range_requests(page):
    run = PROJECT / "workspace/previews" / ASSET_ID / "runs" / RUN_ID
    if not VIEWER.is_dir() or not run.is_dir():
        pytest.skip("本机尚无固定版本 Potree 查看器或真实小样本转换产物")
    server = create_preview_server(PROJECT, ROOT / "frontend", viewer_root=VIEWER)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    seen = []
    page.on("response", lambda response: seen.append((response.url, response.status)))
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/frontend/full-las-preview.html?asset_id={ASSET_ID}")
        expect(page.locator("#source-points")).to_have_text("957,996")
        expect(page.locator("#rgb-state")).to_have_text("已记录")
        expect(page.locator("#load-status")).to_have_attribute("data-state", "ready", timeout=60000)
        assert int(page.locator("#visible-points").inner_text().replace(",", "")) > 0
        assert any("hierarchy.bin" in url and status == 206 for url, status in seen)
        assert any("octree.bin" in url and status == 206 for url, status in seen)
        assert not any(url.endswith(".las") for url, _status in seen)
        page.locator("#point-budget").fill("2000000")
        expect(page.locator("#budget-value")).to_have_text("2,000,000")
        page.locator("#top-view").click()
        page.locator("#front-view").click()
        page.screenshot(path=str(PROJECT / "workspace/full-preview-probe/small-browser-preview.png"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_full_scan_is_browsable_without_requesting_original_las(page):
    run = PROJECT / "workspace/previews/machine-zoom-full/runs" / FULL_RUN_ID
    if not VIEWER.is_dir() or not run.is_dir():
        pytest.skip("本机尚无固定版本 Potree 查看器或完整原始 LAS 转换产物")
    server = create_preview_server(PROJECT, ROOT / "frontend", viewer_root=VIEWER)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    seen = []
    page.on("response", lambda response: seen.append((response.url, response.status)))
    try:
        page.goto(
            f"http://127.0.0.1:{server.server_port}/frontend/viewer.html"
            "?asset_id=machine-zoom-full"
        )
        expect(page.locator("#full-las-nav")).to_be_visible(timeout=10000)
        expect(page.locator("#asset-facts")).to_contain_text("machine-zoom-full")
        expect(page.locator("#asset-facts")).to_contain_text("47,899,752")
        expect(page.locator("#asset-facts")).not_to_contain_text("site-a-las")
        page.locator("#full-las-nav").click()
        expect(page).to_have_url(
            f"http://127.0.0.1:{server.server_port}/frontend/full-las-preview.html"
            "?asset_id=machine-zoom-full"
        )
        expect(page.locator("#run-id")).to_have_text(FULL_RUN_ID)
        expect(page.locator("#source-points")).to_have_text("47,899,752")
        expect(page.locator("#rgb-state")).to_have_text("已记录")
        expect(page.locator("#load-status")).to_have_attribute("data-state", "ready", timeout=120000)
        assert int(page.locator("#visible-points").inner_text().replace(",", "")) > 0
        assert any("hierarchy.bin" in url and status == 206 for url, status in seen)
        assert any("octree.bin" in url and status == 206 for url, status in seen)
        assert not any(url.endswith(".las") for url, _status in seen)
        page.locator("#fit-view").click()
        page.mouse.move(900, 500)
        page.mouse.down()
        page.mouse.move(1030, 540, steps=8)
        page.mouse.up()
        page.mouse.wheel(0, -600)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(PROJECT / "workspace/full-preview-probe/full-browser-preview.png"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
