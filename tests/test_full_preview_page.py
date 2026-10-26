"""完整 LAS 页面必须是可加载点云页，而不是只展示 JSON 链接。"""

from html.parser import HTMLParser
from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class _Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def test_page_has_real_potree_loader_and_distinct_counts():
    html = (FRONTEND / "full-las-preview.html").read_text(encoding="utf-8")
    script = (FRONTEND / "full-las-preview.js").read_text(encoding="utf-8")
    elements = _Elements()
    elements.feed(html)
    ids = {attrs.get("id") for _tag, attrs in elements.items}
    assert {"potree_render_area", "source-points", "visible-points", "point-budget", "load-status"} <= ids
    assert "/vendor/potree/build/potree/potree.js" in html
    assert "Potree.loadPointCloud(metadataUrl" in script
    assert "pointcloud.numVisiblePoints" in script
    assert "viewer.fitToScreen()" in script
    assert "material.activeAttributeName = \"rgba\"" in script


def test_page_freezes_immutable_run_url_and_exposes_failures():
    script = (FRONTEND / "full-las-preview.js").read_text(encoding="utf-8")
    assert 'fetch("/api/full-previews"' in script
    assert "const metadataUrl = selected.metadata_url" in script
    assert "selected.run_id" in script
    assert "window.setTimeout" in script
    assert "加载失败" in script
    assert "viewer.setPointBudget" in script


def test_showcase_entry_starts_hidden_and_appears_from_published_catalog():
    html = (FRONTEND / "viewer.html").read_text(encoding="utf-8")
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")
    elements = _Elements()
    elements.feed(html)
    nav = [attrs for tag, attrs in elements.items if tag == "a" and attrs.get("id") == "full-las-nav"]
    assert nav and "hidden" in nav[0]
    assert 'fetch("/api/full-previews"' in script
    assert "full-las-preview.html?asset_id=" in script
