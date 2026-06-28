from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_workbench_prefers_api_before_static_registry():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "API_BASE_URL" in script
    assert "fetchApiProjectData" in script
    assert "/assets" in script
    assert script.index("fetchApiProjectData") < script.index("normalizeRegistryProject")


def test_showcase_viewer_embeds_real_viewer_targets():
    html = (FRONTEND / "viewer.html").read_text(encoding="utf-8")
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")

    assert "viewer-frame" in html
    assert "renderEmbeddedViewer" in script
    assert "iframe" in script
    assert "Potree" in script
    assert "Gaussian Splatting" in script
