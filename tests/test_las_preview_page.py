"""平台展示入口到本地 LAS 预览页的最小集成契约。"""

from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "frontend"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def elements(name):
    parser = Elements()
    parser.feed((ROOT / name).read_text(encoding="utf-8"))
    return parser.items


def test_existing_showcase_links_to_real_las_preview_page():
    links = [attrs for tag, attrs in elements("viewer.html") if tag == "a"]
    assert any(link.get("href") == "las-preview.html" for link in links)


def test_las_preview_page_has_file_picker_canvas_and_local_scripts():
    items = elements("las-preview.html")
    inputs = [attrs for tag, attrs in items if tag == "input"]
    assert any(item.get("type") == "file" and "multiple" in item and item.get("accept") == ".las" for item in inputs)
    assert any(tag == "canvas" and attrs.get("id") == "las-canvas" for tag, attrs in items)
    assert any(attrs.get("id") == "preview-status" for _, attrs in items)
    scripts = [attrs.get("src") for tag, attrs in items if tag == "script"]
    assert scripts == ["las-preview-core.js", "las-preview.js"]
