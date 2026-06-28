# Dashboard And Viewer Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the formal frontend toward the selected UX direction: Version B for the project dashboard and Version C for per-project showcase views.

**Architecture:** Keep the static frontend lightweight and API-first. The main `frontend/index.html` and `frontend/app.js` become the project dashboard, while `frontend/viewer.html` remains the showcase entry and is later reshaped toward the viewer-first layout. Tests assert the intended DOM contracts and data-flow functions before implementation changes.

**Tech Stack:** Static HTML, CSS, vanilla JavaScript, Python pytest content tests, existing FastAPI backend endpoints.

---

### Task 1: Project Dashboard Shell

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.css`
- Modify: `frontend/app.js`
- Test: `tests/test_frontend_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_formal_home_uses_project_dashboard_contract():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "data-view=\"project-dashboard\"" in html
    assert "项目健康度" in html
    assert "dashboard-main" in html
    assert "asset-selector" in html
    assert "data-source-status" in html
    assert "renderDashboard" in script
    assert "renderAssetSelector" in script
    assert ".dashboard-main" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests\test_frontend_dashboard.py::test_formal_home_uses_project_dashboard_contract -q -p no:cacheprovider`

Expected: FAIL because `tests/test_frontend_dashboard.py` or the dashboard contract does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Update `index.html` so the main page contains:

```html
<main class="project-dashboard-shell" data-view="project-dashboard" aria-label="点云项目驾驶舱">
  <section class="dashboard-hero" aria-labelledby="project-title">
    <div>
      <p class="eyebrow">Point Cloud Dashboard</p>
      <h1 id="project-title">点云项目驾驶舱</h1>
      <p id="project-summary" class="summary-line">加载项目状态...</p>
    </div>
    <div id="project-health" class="health-card" aria-label="项目健康度"></div>
  </section>
  <section id="data-source-status" class="data-source-status" aria-label="数据来源状态"></section>
  <section class="dashboard-main">
    <section id="asset-selector" class="asset-selector" aria-label="资产选择"></section>
    <section class="project-insight-panel" aria-label="项目指标"></section>
    <aside class="decision-panel" aria-label="下一步决策"></aside>
  </section>
</main>
```

Add `renderDashboard(project)`, `renderAssetSelector(project)`, and related CSS using the Version B visual direction.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests\test_frontend_dashboard.py -q -p no:cacheprovider`

Expected: PASS.

### Task 2: Data Source Status

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`
- Test: `tests/test_frontend_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dashboard_tracks_api_workspace_sample_data_source():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "sourceLabel" in script
    assert "api" in script
    assert "workspace" in script
    assert "sample" in script
    assert "default" in script
    assert "renderDataSourceStatus" in script
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests\test_frontend_dashboard.py::test_dashboard_tracks_api_workspace_sample_data_source -q -p no:cacheprovider`

Expected: FAIL because data-source status metadata is not yet returned consistently.

- [ ] **Step 3: Write minimal implementation**

Make `fetchProjectData()` return an object with:

```javascript
return { ...projectData, sourceType: "api", sourceLabel: "API 在线" };
```

Use `"workspace"`, `"sample"`, and `"default"` for fallback branches. Render the result into `#data-source-status`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests\test_frontend_dashboard.py -q -p no:cacheprovider`

Expected: PASS.

### Task 3: Asset Selector

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`
- Test: `tests/test_frontend_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dashboard_can_select_assets_from_registry():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "selectedAssetId" in script
    assert "selectAssetById" in script
    assert "project.assets" in script
    assert "data-asset-id" in script
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests\test_frontend_dashboard.py::test_dashboard_can_select_assets_from_registry -q -p no:cacheprovider`

Expected: FAIL because the formal dashboard still renders one default asset.

- [ ] **Step 3: Write minimal implementation**

Normalize registry data into:

```javascript
{
  assets: [{ id, name, format, source, point_count, colorized, bounds, reports }],
  selectedAssetId: firstAsset.id
}
```

Add `selectAssetById(project, assetId)` and re-render dashboard sections on click.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests\test_frontend_dashboard.py -q -p no:cacheprovider`

Expected: PASS.

### Task 4: Verification

**Files:**
- Test all changed frontend and Python code.

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests -q -p no:cacheprovider`

Expected: all tests pass.

- [ ] **Step 2: Run Python compile check**

Run: `python -m compileall -q src tests scripts`

Expected: no output and exit code 0.

- [ ] **Step 3: Run frontend JavaScript syntax checks**

Run:

```bash
node --check frontend\app.js
node --check frontend\viewer.js
```

Expected: both commands exit 0 with no syntax errors.

### Self-Review

- Spec coverage: covers the first three approved steps. The Version C project showcase is intentionally left for the next implementation batch.
- Placeholder scan: no TBD/TODO placeholders are present.
- Type consistency: project fields use `assets`, `asset`, `selectedAssetId`, `sourceType`, and `sourceLabel` consistently.
