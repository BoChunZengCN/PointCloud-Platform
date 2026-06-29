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


def test_dashboard_links_selected_assets_to_viewer_route():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "viewerUrlForAsset" in script
    assert "viewer.html?asset_id=" in script
    assert "encodeURIComponent(asset.id)" in script
    assert "打开展示页" in script


def test_viewer_selects_asset_from_url_parameter():
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")

    assert "selectedAssetIdFromUrl" in script
    assert "new URLSearchParams(window.location.search)" in script
    assert "selectShowcaseAsset" in script
    assert "asset.asset_id === selectedAssetId" in script


def test_viewer_has_asset_switcher_inside_showcase():
    html = (FRONTEND / "viewer.html").read_text(encoding="utf-8")
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")
    css = (FRONTEND / "viewer.css").read_text(encoding="utf-8")

    assert "showcase-asset-switcher" in html
    assert "renderShowcaseAssetSwitcher" in script
    assert "switchShowcaseAsset" in script
    assert "viewer.html?asset_id=" in script
    assert "asset-switcher-list" in css
    assert "showcase-asset-button" in css


def test_viewer_delivery_items_explain_embed_status_and_missing_outputs():
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")
    css = (FRONTEND / "viewer.css").read_text(encoding="utf-8")

    assert "deliveryItemStatus" in script
    assert "isEmbeddableViewer" in script
    assert "manifest" in script
    assert "missing" in script
    assert "data-status" in script
    assert "可嵌入" in script
    assert "Manifest" in script
    assert "缺失" in script
    assert "showcase-status" in css
    assert "data-status=\"missing\"" in css


def test_viewer_status_bar_reports_current_viewer_state():
    html = (FRONTEND / "viewer.html").read_text(encoding="utf-8")
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")
    css = (FRONTEND / "viewer.css").read_text(encoding="utf-8")

    assert "viewer-status-bar" in html
    assert "viewer-status-title" in html
    assert "viewer-status-detail" in html
    assert "renderViewerStatus" in script
    assert "当前查看器" in script
    assert "未自动加载" in script
    assert "data-viewer-status" in script
    assert ".viewer-status-bar" in css
    assert "viewer-status-detail" in css


def test_viewer_marks_active_delivery_item():
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")
    css = (FRONTEND / "viewer.css").read_text(encoding="utf-8")

    assert "setActiveDeliveryItem" in script
    assert "data-delivery-id" in script
    assert "activeDeliveryId" in script
    assert "showcase-card active" in script
    assert ".showcase-card.active" in css


def test_viewer_empty_state_explains_next_cli_actions():
    html = (FRONTEND / "viewer.html").read_text(encoding="utf-8")
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")
    css = (FRONTEND / "viewer.css").read_text(encoding="utf-8")

    assert "viewer-empty-state" in html
    assert "renderViewerEmptyState" in script
    assert "pc-system publish-phase2-viewer" in script
    assert "pc-system plan-production-run" in script
    assert "viewer-empty-state" in css


def test_viewer_status_bar_mobile_rules_override_base_styles():
    css = (FRONTEND / "viewer.css").read_text(encoding="utf-8")

    base_index = css.index(".viewer-status-bar {")
    mobile_index = css.rindex("@media (max-width: 560px)")
    assert mobile_index > base_index


def test_viewer_prefers_api_delivery_status_over_extension_guessing():
    script = (FRONTEND / "viewer.js").read_text(encoding="utf-8")

    assert "API_BASE_URL" in script
    assert "/delivery/" in script
    assert "fetchDeliveryStatus" in script
    assert "applyDeliveryStatus" in script
    assert "statusByPath" in script

def test_dashboard_fetches_and_renders_phase4_job_summary():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "job-status-panel" in html
    assert "fetchJobSummary" in script
    assert "/runs/" in script
    assert "renderJobSummary" in script
    assert "latest_job" in script
    assert "生产任务" in script
    assert ".job-status-panel" in css

def test_phase4_docs_describe_controlled_job_write_api():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    phase4 = (ROOT / "docs" / "phase4-development-plan.md").read_text(encoding="utf-8")

    assert "P4-M3" in readme
    assert "POST /runs/<asset_id>/jobs" in readme
    assert "PATCH /runs/<asset_id>/jobs/<job_id>/steps/<step_id>" in readme
    assert "P4-M3 Controlled Job Write API" in phase4
    assert "controlled write" in phase4

def test_dashboard_ex1_has_job_operation_panel_contract():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "job-action-panel" in html
    assert "job-create-button" in html
    assert "job-step-status-select" in html
    assert "createProductionJob" in script
    assert "updateProductionJobStep" in script
    assert "renderJobActions" in script
    assert "refreshJobSummary" in script
    assert "POST" in script
    assert "PATCH" in script
    assert ".job-action-panel" in css
    assert ".job-action-row" in css

