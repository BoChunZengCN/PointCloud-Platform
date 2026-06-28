from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_formal_home_uses_project_dashboard_contract():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert 'data-view="project-dashboard"' in html
    assert "项目健康度" in html
    assert "dashboard-main" in html
    assert "asset-selector" in html
    assert "data-source-status" in html
    assert "renderDashboard" in script
    assert "renderAssetSelector" in script
    assert ".dashboard-main" in css


def test_dashboard_tracks_api_workspace_sample_data_source():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "sourceLabel" in script
    assert '"api"' in script
    assert '"workspace"' in script
    assert '"sample"' in script
    assert '"default"' in script
    assert "renderDataSourceStatus" in script


def test_dashboard_can_select_assets_from_registry():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "selectedAssetId" in script
    assert "selectAssetById" in script
    assert "project.assets" in script
    assert "data-asset-id" in script


def test_project_showcase_uses_viewer_first_contract():
    html = (FRONTEND / "viewer.html").read_text(encoding="utf-8")
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")
    css = (FRONTEND / "viewer.css").read_text(encoding="utf-8")

    assert 'data-view="viewer-first"' in html
    assert "immersive-viewer" in html
    assert "viewer-control-panel" in html
    assert "layer-controls" in html
    assert "viewer-task-list" in html
    assert "renderLayerControls" in script
    assert "renderViewerTasks" in script
    assert ".viewer-shell" in css
