"""本地 LAS 在平台页面中的真实 Chromium 预览。"""

import struct
from pathlib import Path

from playwright.sync_api import expect


ROOT = Path(__file__).resolve().parents[2]


def _write_small_las(path: Path) -> None:
    count = 100
    data = bytearray(227 + count * 26)
    data[:4] = b"LASF"
    data[24:26] = bytes((1, 2))
    struct.pack_into("<H", data, 94, 227)
    struct.pack_into("<I", data, 96, 227)
    data[104] = 2
    struct.pack_into("<H", data, 105, 26)
    struct.pack_into("<I", data, 107, count)
    for offset in (131, 139, 147):
        struct.pack_into("<d", data, offset, 0.01)
    for index in range(count):
        base = 227 + index * 26
        struct.pack_into("<iii", data, base, (index % 10) * 10, (index // 10) * 10, index % 5)
        struct.pack_into("<HHH", data, base + 20, 65535, 0, 0)
    path.write_bytes(data)


def test_local_las_is_loaded_as_real_points_and_views_switch(page, tmp_path):
    source = tmp_path / "small.las"
    _write_small_las(source)
    page.goto((ROOT / "frontend" / "las-preview.html").as_uri())
    expect(page.locator("#preview-status")).to_have_text("请选择 LAS 文件")
    page.locator("#las-files").set_input_files(str(source))
    expect(page.locator("#preview-status")).to_have_text("全部原始点已显示")
    expect(page.locator("#point-counter")).to_have_text("100 点")
    expect(page.locator("#cloud-facts")).to_contain_text("原始 RGB")
    page.locator('[data-view="front"]').click()
    expect(page.locator("#view-name")).to_have_text("正视 XZ")
    page.locator('[data-view="side"]').click()
    expect(page.locator("#view-name")).to_have_text("侧视 YZ")
