const API_BASE_URL = window.PC_SYSTEM_API_BASE_URL || "http://127.0.0.1:8000";
const API_KEY = window.PC_SYSTEM_API_KEY || "";
const WORKSPACE_REGISTRY_URL = "../workspace/data/assets/asset_index.json";
const DATA_URL = "data/sample-project.json";

const DEFAULT_WORKFLOW = [
  {
    phase: "Phase 1",
    name: "LAS 资产处理",
    status: "completed",
    command: "pc-system ingest / demo-phase1",
    output: "asset.json, quality_report.html, preview_manifest.json",
  },
  {
    phase: "Phase 1",
    name: "切片与规则分割",
    status: "completed",
    command: "pc-system plan-slice / execute-rule-segment",
    output: "slice_plan.json, segmentation_summary.html",
  },
  {
    phase: "Phase 2",
    name: "Potree 与 Splat 入口",
    status: "completed",
    command: "pc-system publish-phase2-viewer",
    output: "phase2_viewer_manifest.json",
  },
  {
    phase: "Phase 3",
    name: "生产运行计划",
    status: "planned",
    command: "pc-system plan-production-run",
    output: "production_run_plan.json",
  },
];

const DEFAULT_PROJECT_DATA = {
  project_name: "脚架式点云示例项目",
  summary: "已处理 LAS/LAZ 资产进入生产工作流，Phase 1 与 Phase 2 已具备可审计输出，Phase 3 正在补齐生产化计划。",
  sourceType: "default",
  sourceLabel: "内置默认数据",
  selectedAssetId: "site-a-las",
  assets: [
    {
      id: "site-a-las",
      name: "三维扫描大厅样例",
      format: "LAS/LAZ",
      source: "processed_las",
      point_count: 12840000,
      colorized: true,
      coordinate_system: "统一工程坐标",
      bounds: "X 0-38m, Y 0-22m, Z 0-7m",
      reports: [
        { name: "质量报告", kind: "QA", href: "../workspace/reports/site-a-las/quality_report.html", status: "ready" },
        { name: "分割汇总", kind: "Segmentation", href: "../workspace/reports/site-a-las/segments/room-a/baseline/segmentation_summary.html", status: "ready" },
        { name: "Phase 2 状态", kind: "Status", href: "../workspace/reports/phase2_status.md", status: "ready" },
        { name: "Phase 3 工具检查", kind: "Production", href: "../workspace/reports/phase3_tool_check.md", status: "ready" },
      ],
    },
  ],
  workflow: DEFAULT_WORKFLOW,
};

let activeProject = null;

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function statusText(status) {
  const names = {
    completed: "已完成",
    planned: "计划中",
    blocked: "阻塞",
    running: "运行中",
    failed: "失败",
    ready: "可用",
  };
  return names[status] || status;
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) {
    node.textContent = value;
  }
}

function viewerUrlForAsset(asset) {
  return `viewer.html?asset_id=${encodeURIComponent(asset.id)}`;
}

function selectedAsset(project) {
  return project.assets.find((asset) => asset.id === project.selectedAssetId) || project.assets[0];
}

function assetCount(project) {
  return project.assets.length;
}

function totalPointCount(project) {
  return project.assets.reduce((total, asset) => total + Number(asset.point_count || 0), 0);
}

function riskCount(project) {
  return project.workflow.filter((step) => step.status === "blocked" || step.status === "planned").length;
}

function createFact(term, value) {
  const fragment = document.createDocumentFragment();
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = term;
  dd.textContent = value;
  fragment.append(dt, dd);
  return fragment;
}

function apiHeaders() {
  return API_KEY ? { "x-api-key": API_KEY } : {};
}

async function loadJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${url}`);
  }
  return await response.json();
}

async function fetchApiHealth() {
  return await loadJson(`${API_BASE_URL}/health`);
}

async function fetchApiProjectData() {
  const registry = await loadJson(`${API_BASE_URL}/assets`);
  return {
    ...normalizeRegistryProject(registry),
    sourceType: "api",
    sourceLabel: "API 在线",
  };
}

function normalizeRegistryProject(registry) {
  const registryAssets = registry.assets || [];
  if (!registryAssets.length) {
    return {
      ...DEFAULT_PROJECT_DATA,
      project_name: "未发现真实资产索引",
      summary: "请先运行 pc-system index-assets 生成 workspace/data/assets/asset_index.json，然后刷新工作台。",
      assets: [],
      selectedAssetId: "",
    };
  }

  const assets = registryAssets.map((item) => normalizeRegistryAsset(item));
  return {
    project_name: "真实点云项目驾驶舱",
    summary: `已从 workspace 读取 ${registry.asset_count || assets.length} 个资产，可从驾驶舱选择项目并进入展示页。`,
    assets,
    selectedAssetId: assets[0].id,
    workflow: DEFAULT_WORKFLOW,
  };
}

function normalizeRegistryAsset(item) {
  const reportPaths = item.report_paths || {};
  const previewPaths = item.preview_paths || {};
  const artifactStatus = item.artifact_status || {};
  const reportStatus = artifactStatus.reports || {};
  const previewStatus = artifactStatus.preview || {};
  return {
    id: item.asset_id,
    name: item.file_name || item.asset_id,
    format: "LAS/LAZ",
    source: item.source_path || "workspace_registry",
    point_count: item.point_count || 0,
    colorized: Boolean(item.has_rgb),
    coordinate_system: "来自 asset_index.json",
    bounds: JSON.stringify(item.bounds || {}),
    analysis_status: item.analysis_status || "missing",
    reports: [
      { name: "质量报告", kind: "QA", href: `../workspace/${reportPaths.quality_report || ""}`, status: reportStatus.quality_report ? "ready" : "planned" },
      { name: "生产运行计划", kind: "Production", href: `../workspace/${reportPaths.production_plan || ""}`, status: reportStatus.production_plan ? "ready" : "planned" },
      { name: "生产运行报告", kind: "Production", href: `../workspace/${reportPaths.production_report || ""}`, status: reportStatus.production_report ? "ready" : "planned" },
      { name: "Phase 2 Viewer", kind: "Viewer", href: `../workspace/${previewPaths.phase2_viewer || ""}`, status: previewStatus.phase2_viewer ? "ready" : "planned" },
    ],
  };
}

function normalizeSampleProject(project) {
  if (project.assets) {
    return {
      ...project,
      selectedAssetId: project.selectedAssetId || project.assets[0]?.id || "",
    };
  }

  // 兼容旧 sample-project.json 的单资产结构，避免演示数据格式变化导致页面空白。
  return {
    ...project,
    assets: project.asset ? [project.asset] : DEFAULT_PROJECT_DATA.assets,
    selectedAssetId: project.asset?.id || DEFAULT_PROJECT_DATA.selectedAssetId,
    workflow: project.workflow || DEFAULT_WORKFLOW,
  };
}

async function fetchProjectData() {
  try {
    return await fetchApiProjectData();
  } catch (apiError) {
    try {
      return {
        ...normalizeRegistryProject(await loadJson(WORKSPACE_REGISTRY_URL)),
        sourceType: "workspace",
        sourceLabel: "workspace 静态索引",
      };
    } catch (workspaceError) {
      try {
        return {
          ...normalizeSampleProject(await loadJson(DATA_URL)),
          sourceType: "sample",
          sourceLabel: "前端样例数据",
        };
      } catch (sampleError) {
        // 直接双击 file:// 打开时，部分浏览器会拦截本地 JSON fetch；此时使用内置样例数据。
        return DEFAULT_PROJECT_DATA;
      }
    }
  }
}


function renderApiHealthStatus(health) {
  const node = document.getElementById("api-health-status");
  if (!node) {
    return;
  }
  if (!health) {
    node.dataset.status = "offline";
    node.replaceChildren(textElement("span", "API"), textElement("strong", "离线"), textElement("small", "未连接 FastAPI 服务"));
    return;
  }
  node.dataset.status = "online";
  node.replaceChildren(
    textElement("span", health.run_mode || "API"),
    textElement("strong", health.status === "ok" ? "在线" : "异常"),
    textElement("small", `写入保护：${health.write_protection || "unknown"}`),
  );
}
function renderDataSourceStatus(project) {
  const node = document.getElementById("data-source-status");
  if (!node) {
    return;
  }
  const sourceDescriptions = {
    api: "正在读取 FastAPI 服务，适合联调和真实工作流。",
    workspace: "正在读取 workspace/data/assets/asset_index.json。",
    sample: "当前显示前端样例数据，不代表真实 workspace。",
    default: "当前显示内置默认数据，请启动服务或生成资产索引。",
  };
  node.dataset.source = project.sourceType || "default";
  node.replaceChildren(
    textElement("span", "数据来源"),
    textElement("strong", project.sourceLabel || "未知来源"),
    textElement("small", sourceDescriptions[project.sourceType] || sourceDescriptions.default),
  );
}

function renderDashboard(project) {
  const asset = selectedAsset(project);
  setText("project-title", project.project_name);
  setText("project-summary", project.summary);
  setText("metric-assets", String(assetCount(project)));
  setText("metric-points", formatNumber(totalPointCount(project)));
  setText("metric-phases", new Set(project.workflow.map((step) => step.phase)).size.toString());
  setText("metric-risks", String(riskCount(project)));
  renderHealth(project);
  renderDataSourceStatus(project);
  fetchApiHealth().then(renderApiHealthStatus).catch(() => renderApiHealthStatus(null));
  renderAssetSelector(project);
  renderAssetInsight(project, asset);
  renderDecisions(project);
  renderReports(asset);
  renderJobSummary(asset, null);
  renderQualityInsights(asset, null);
  renderAnalysisOverview(project, null);
  renderObjectSegmentation(asset, null);
  renderSegmentationRun(asset, null);
  renderGoldenEvaluation(asset, null, null, null);
  renderQualityGateStatus(asset, null);
  renderDeliveryGateNotice(asset, null);
  renderProjectGateStatus(null);
  renderReportCenter(null);
  if (project.sourceType === "api" && asset) {
    fetchJobSummary(asset.id)
      .then((summary) => renderJobSummary(asset, summary))
      .catch(() => renderJobSummary(asset, { job_count: 0, latest_job: null, status_summary: {} }));
    fetchPointCloudAnalysis(asset.id)
      .then((analysis) => renderQualityInsights(asset, analysis))
      .catch(() => renderQualityInsights(asset, { point_count: 0, rgb_coverage: 0, grid: { cell_count: 0 }, findings: [] }));
    fetchAnalysisOverview()
      .then((overview) => renderAnalysisOverview(project, overview))
      .catch(() => renderAnalysisOverview(project, null));
    fetchObjectSegmentation(asset.id)
      .then((segments) => renderObjectSegmentation(asset, segments))
      .catch(() => renderObjectSegmentation(asset, { object_count: 0, noise_point_count: 0, objects: [] }));
    fetchSegmentationRuns(asset.id)
      .then((runs) => renderSegmentationRun(asset, runs))
      .catch(() => renderSegmentationRun(asset, { run_count: 0, runs: [] }));
    Promise.all([
      fetchGoldenEvaluations(asset.id).catch(() => ({ evaluation_count: 0, evaluations: [] })),
      fetchSegmentationSearches(asset.id).catch(() => ({ search_count: 0, searches: [] })),
    ]).then(async ([evaluations, searches]) => {
      const searchItems = searches.searches || [];
      const latestSearch = searchItems.length
        ? searchItems[searchItems.length - 1]
        : null;
      const comparisonId = latestSearch?.recommendation?.comparison_id;
      let comparison = null;
      if (comparisonId) {
        comparison = await fetchSegmentationComparison(asset.id, comparisonId)
          .catch(() => null);
      }
      renderGoldenEvaluation(asset, evaluations, searches, comparison);
    });
    fetchQualityGate(asset.id)
      .then((gate) => renderQualityGateStatus(asset, gate))
      .catch(() => renderQualityGateStatus(asset, { status: "review_required", severity: "warning", finding_count: 0 }));
  }
}

function renderHealth(project) {
  const node = document.getElementById("project-health");
  const total = Math.max(project.workflow.length, 1);
  const completed = project.workflow.filter((step) => step.status === "completed").length;
  const score = Math.round((completed / total) * 100);
  node.replaceChildren(
    textElement("span", "项目健康度"),
    textElement("strong", `${score}%`),
    textElement("small", riskCount(project) ? `${riskCount(project)} 项需要推进` : "全部关键流程已完成"),
  );
}

function renderAssetSelector(project) {
  const list = document.getElementById("asset-selector");
  const title = document.createElement("div");
  title.className = "selector-title";
  title.innerHTML = `<span>资产列表</span><strong>${assetCount(project)}</strong>`;

  const rows = project.assets.map((asset) => {
    const row = document.createElement("article");
    row.className = asset.id === project.selectedAssetId ? "asset-row-shell active" : "asset-row-shell";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "asset-row";
    button.setAttribute("data-asset-id", asset.id);
    button.addEventListener("click", () => selectAssetById(project, asset.id));
    button.append(
      textElement("span", asset.id),
      textElement("strong", asset.name),
      textElement("small", `${formatNumber(asset.point_count)} pts · ${asset.colorized ? "RGB" : "No RGB"}`),
    );

    const viewerLink = document.createElement("a");
    viewerLink.className = "asset-viewer-link";
    viewerLink.href = viewerUrlForAsset(asset);
    viewerLink.textContent = "打开展示页";
    row.append(button, viewerLink);
    return row;
  });

  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-note";
    empty.textContent = "暂无资产。请先运行 pc-system index-assets 生成资产索引。";
    rows.push(empty);
  }

  list.replaceChildren(title, ...rows);
}

function selectAssetById(project, assetId) {
  project.selectedAssetId = assetId;
  activeProject = project;
  renderDashboard(project);
}

function renderAssetInsight(project, asset) {
  if (!asset) {
    return;
  }
  setText("asset-format", asset.format);
  drawPointCloudPreview(asset);

  const detail = document.getElementById("asset-detail");
  detail.replaceChildren(
    assetPill("资产 ID", asset.id),
    assetPill("坐标", asset.coordinate_system),
    assetPill("范围", asset.bounds),
    assetPill("来源", asset.source),
  );
}

function renderDecisions(project) {
  const list = document.getElementById("decision-list");
  const actions = [
    {
      index: "01",
      title: riskCount(project) ? "推进计划中生产任务" : "复核最终交付包",
      note: riskCount(project) ? "优先处理 planned / blocked 流程" : "可进入展示页确认成果",
    },
    {
      index: "02",
      title: "打开选中资产展示页",
      note: "展示页采用查看器优先布局",
    },
    {
      index: "03",
      title: "检查报告入口",
      note: "确认 QA、分割、生产报告是否齐备",
    },
  ];
  list.replaceChildren(...actions.map(decisionItem));
}

function decisionItem(action) {
  const node = document.createElement("article");
  node.className = "decision-item";
  node.append(
    textElement("span", action.index),
    textElement("strong", action.title),
    textElement("small", action.note),
  );
  return node;
}

function renderReports(asset) {
  const list = document.getElementById("report-list");
  const reports = asset?.reports || [];
  const links = reports.map((report) => {
    const link = document.createElement("a");
    link.className = "report-link";
    link.href = report.href || "#";
    link.innerHTML = `
      <span>
        <span class="report-name"></span>
        <span class="report-kind"></span>
      </span>
      <span class="report-status"></span>
    `;
    link.querySelector(".report-name").textContent = report.name;
    link.querySelector(".report-kind").textContent = report.kind;
    link.querySelector(".report-status").textContent = statusText(report.status);
    return link;
  });
  list.replaceChildren(...links);
}



async function sendJson(url, options) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...apiHeaders(), ...(options?.headers || {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Failed to call ${url}`);
  }
  return await response.json();
}

async function createProductionJob(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  const jobId = `job-${assetId}-dashboard`;
  return await sendJson(`${API_BASE_URL}/runs/${encodedAssetId}/jobs`, {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
}

async function updateProductionJobStep(assetId, jobId, stepId, status, message) {
  const encodedAssetId = encodeURIComponent(assetId);
  const encodedJobId = encodeURIComponent(jobId);
  const encodedStepId = encodeURIComponent(stepId);
  return await sendJson(`${API_BASE_URL}/runs/${encodedAssetId}/jobs/${encodedJobId}/steps/${encodedStepId}`, {
    method: "PATCH",
    body: JSON.stringify({ status, message }),
  });
}

async function refreshJobSummary(asset) {
  const summary = await fetchJobSummary(asset.id);
  renderJobSummary(asset, summary);
  return summary;
}

function setJobActionFeedback(message) {
  const feedback = document.getElementById("job-action-feedback");
  if (feedback) {
    feedback.textContent = message;
  }
}

function renderJobActions(project, asset) {
  const panel = document.getElementById("job-action-panel");
  const createButton = document.getElementById("job-create-button");
  const updateButton = document.getElementById("job-step-update-button");
  if (!panel || !createButton || !updateButton) {
    return;
  }

  const apiEnabled = Boolean(asset && project.sourceType === "api");
  panel.dataset.enabled = apiEnabled ? "true" : "false";
  createButton.disabled = !apiEnabled;
  updateButton.disabled = !apiEnabled;
  setJobActionFeedback(apiEnabled ? "可通过 API 操作生产任务" : "启动 API 后可操作生产任务");

  createButton.onclick = async () => {
    try {
      setJobActionFeedback("正在创建 job...");
      await createProductionJob(asset.id);
      await refreshJobSummary(asset);
      setJobActionFeedback("job 已创建");
    } catch (error) {
      setJobActionFeedback(`创建失败：${error.message}`);
    }
  };

  updateButton.onclick = async () => {
    try {
      setJobActionFeedback("正在更新 step...");
      const summary = await fetchJobSummary(asset.id);
      const latestJob = summary.latest_job;
      if (!latestJob) {
        setJobActionFeedback("请先创建 job");
        return;
      }
      const stepId = document.getElementById("job-step-id-input").value || "ingest";
      const status = document.getElementById("job-step-status-select").value;
      const message = document.getElementById("job-step-message-input").value;
      await updateProductionJobStep(asset.id, latestJob.job_id, stepId, status, message);
      await refreshJobSummary(asset);
      setJobActionFeedback("step 已更新");
    } catch (error) {
      setJobActionFeedback(`更新失败：${error.message}`);
    }
  };
}

async function fetchJobSummary(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  return await loadJson(`${API_BASE_URL}/runs/${encodedAssetId}/jobs`);
}


async function fetchProjectGate() {
  return await loadJson(`${API_BASE_URL}/project-gate`);
}

function renderProjectGateStatus(gate) {
  const node = document.getElementById("project-gate-status");
  if (!node) {
    return;
  }
  if (!gate) {
    node.dataset.status = "review_required";
    node.replaceChildren(textElement("span", "项目门禁"), textElement("strong", "读取中"), textElement("small", "正在读取项目级门禁"));
    return;
  }
  const labels = { passed: "可交付", review_required: "需复核", blocked: "阻塞", missing: "缺失" };
  node.dataset.status = gate.status || "review_required";
  node.replaceChildren(
    textElement("span", "项目门禁"),
    textElement("strong", labels[gate.status] || "需复核"),
    textElement("small", `${gate.asset_count || 0} 个资产 · 项目级汇总`),
  );
}

async function fetchReportCenter() {
  return await loadJson(`${API_BASE_URL}/reports/center`);
}

function renderReportCenter(center) {
  const node = document.getElementById("report-center-summary");
  if (!node) {
    return;
  }
  if (!center) {
    node.replaceChildren(textElement("span", "报告中心"), textElement("strong", "读取中"), textElement("small", "正在扫描 reports 与 delivery"));
    return;
  }
  const firstReport = (center.reports || [])[0];
  node.replaceChildren(
    textElement("span", "报告中心"),
    textElement("strong", `${formatNumber(center.report_count || 0)} reports`),
    textElement("small", firstReport ? firstReport.path : "等待生成报告"),
  );
}
async function fetchQualityGate(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  return await loadJson(`${API_BASE_URL}/quality-gates/${encodedAssetId}`);
}

function renderQualityGateStatus(asset, gate) {
  const node = document.getElementById("quality-gate-status-bar");
  if (!node) {
    return;
  }
  if (!asset) {
    node.dataset.status = "review_required";
    node.replaceChildren(textElement("span", "质量门禁"), textElement("strong", "暂无资产"), textElement("small", "请先生成资产索引"));
    return;
  }
  if (!gate) {
    node.dataset.status = "review_required";
    node.replaceChildren(textElement("span", "质量门禁"), textElement("strong", "读取中"), textElement("small", `正在检查 ${asset.id}`));
    return;
  }
  const statusLabels = {
    passed: "可交付",
    review_required: "需复核",
    blocked: "阻塞",
  };
  node.dataset.status = gate.status || "review_required";
  node.replaceChildren(
    textElement("span", "质量门禁"),
    textElement("strong", statusLabels[gate.status] || "需复核"),
    textElement("small", `${asset.id} · ${gate.finding_count || 0} 项质量提示`),
  );
}
function renderDeliveryGateNotice(asset, gate) {
  const node = document.getElementById("delivery-gate-summary");
  if (!node) {
    return;
  }
  if (!asset || !gate) {
    node.replaceChildren(textElement("span", "交付放行"), textElement("strong", "等待门禁"), textElement("small", "生成 quality gate 后判断是否可导出"));
    return;
  }
  const deliveryLabels = {
    passed: ["可导出", "export-delivery-package 可直接执行"],
    review_required: ["需复核", "使用 --allow-review-required 后可导出"],
    blocked: ["不可导出", "blocked 门禁会阻止交付包导出"],
  };
  const [label, note] = deliveryLabels[gate.status] || deliveryLabels.review_required;
  node.replaceChildren(
    textElement("span", "交付放行"),
    textElement("strong", label),
    textElement("small", `${asset.id} · ${note}`),
  );
}
async function fetchAnalysisOverview() {
  return await loadJson(`${API_BASE_URL}/analysis`);
}

function renderAnalysisOverview(project, overview) {
  const node = document.getElementById("analysis-overview-summary");
  if (!node) {
    return;
  }
  const readyFromRegistry = project.assets.filter((asset) => asset.analysis_status === "ready").length;
  if (!overview) {
    node.replaceChildren(
      textElement("span", "分析状态"),
      textElement("strong", `${readyFromRegistry}/${project.assets.length}`),
      textElement("small", readyFromRegistry ? "来自资产索引" : "等待 Phase 7 分析报告"),
    );
    return;
  }
  node.replaceChildren(
    textElement("span", "已生成分析报告"),
    textElement("strong", `${overview.asset_count || 0}`),
    textElement("small", `${project.assets.length} 个资产 · API 汇总在线`),
  );
}
async function fetchPointCloudAnalysis(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  return await loadJson(`${API_BASE_URL}/analysis/${encodedAssetId}`);
}

async function fetchObjectSegmentation(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  return await loadJson(`${API_BASE_URL}/segments/${encodedAssetId}/objects`);
}

async function fetchSegmentationRuns(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  return await loadJson(`${API_BASE_URL}/segmentation-runs/${encodedAssetId}`);
}

async function fetchGoldenEvaluations(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  return await loadJson(`${API_BASE_URL}/segmentation-evaluations/${encodedAssetId}`);
}

async function fetchSegmentationSearches(assetId) {
  const encodedAssetId = encodeURIComponent(assetId);
  return await loadJson(`${API_BASE_URL}/segmentation-searches/${encodedAssetId}`);
}

async function fetchSegmentationComparison(assetId, comparisonId) {
  const encodedAssetId = encodeURIComponent(assetId);
  const encodedComparisonId = encodeURIComponent(comparisonId);
  return await loadJson(
    `${API_BASE_URL}/segmentation-comparisons/${encodedAssetId}/${encodedComparisonId}`,
  );
}

function metricPercent(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`;
}

function renderGoldenEvaluation(
  asset,
  evaluationPayload,
  searchPayload,
  comparisonPayload,
) {
  const node = document.getElementById("golden-evaluation-summary");
  if (!node) {
    return;
  }
  if (!asset) {
    node.replaceChildren(textElement("span", "暂无资产"));
    return;
  }
  if (!evaluationPayload) {
    node.replaceChildren(
      textElement("span", "黄金标注准确率"),
      textElement("strong", "读取中"),
      textElement("small", "正在读取 Phase 13B 评估与参数搜索"),
    );
    return;
  }
  const view = buildGoldenEvaluationViewModel(
    evaluationPayload,
    searchPayload,
    comparisonPayload,
  );
  if (view.state !== "ready") {
    node.replaceChildren(
      textElement("span", "黄金标注准确率"),
      textElement("strong", "暂无评估"),
      textElement("small", "请先执行 evaluate-segmentation-run"),
    );
    return;
  }
  const recommendationText = view.recommendationConfig
    ? JSON.stringify(view.recommendationConfig)
    : "暂无推荐参数";
  const scoreText = view.recommendationScore === null
    ? "暂无评分"
    : view.recommendationScore.toFixed(4);
  node.replaceChildren(
    textElement("span", `黄金标注准确率 · ${view.evaluationId}`),
    textElement("strong", `实例 F1 ${metricPercent(view.instanceF1)}`),
    textElement(
      "small",
      `点 mIoU ${metricPercent(view.pointMiou)} · 包围盒 IoU ${metricPercent(view.meanBoxIou)} · 标注覆盖率 ${metricPercent(view.matchedLabelRatio)}`,
    ),
    textElement(
      "small",
      `回归门禁: ${view.gateStatus} · 最佳综合分: ${scoreText} · 推荐参数: ${recommendationText}`,
    ),
  );
}

function renderSegmentationRun(asset, payload) {
  const node = document.getElementById("segmentation-run-summary");
  if (!node) {
    return;
  }
  if (!asset) {
    node.replaceChildren(textElement("span", "暂无资产"));
    return;
  }
  if (!payload) {
    node.replaceChildren(
      textElement("span", "运行质量代理指标"),
      textElement("strong", "读取中"),
      textElement("small", "正在读取 Phase 13A 分割运行"),
    );
    return;
  }
  const runs = payload.runs || [];
  const latest = runs.length ? runs[runs.length - 1] : null;
  if (!latest) {
    node.replaceChildren(
      textElement("span", "运行质量代理指标"),
      textElement("strong", "暂无运行"),
      textElement("small", "请先执行 run-segmentation"),
    );
    return;
  }
  const findingCodes = (latest.quality?.findings || [])
    .slice(0, 3)
    .map((finding) => finding.code)
    .join(" · ");
  node.replaceChildren(
    textElement("span", `${latest.run_id} · ${latest.executed_engine || latest.requested_engine}`),
    textElement("strong", latest.quality?.status || latest.status),
    textElement("small", findingCodes || "运行质量代理指标未发现风险"),
  );
}

function renderObjectSegmentation(asset, segments) {
  const node = document.getElementById("object-segmentation-summary");
  if (!node) {
    return;
  }
  if (!asset) {
    node.replaceChildren(textElement("span", "暂无资产"));
    return;
  }
  if (!segments) {
    node.replaceChildren(
      textElement("span", "物体分割"),
      textElement("strong", "读取中"),
      textElement("small", "正在读取 Phase 10 分割报告"),
    );
    return;
  }

  const firstObject = (segments.objects || [])[0];
  const detail = firstObject ? `${firstObject.object_id} · ${formatNumber(firstObject.point_count)} points` : "等待生成 object_segments.json";
  node.replaceChildren(
    textElement("span", `${asset.id} · ${segments.method || "geometric_cluster"}`),
    textElement("strong", `${formatNumber(segments.object_count || 0)} objects`),
    textElement("small", `${formatNumber(segments.noise_point_count || 0)} noise points · ${detail}`),
  );
}

function renderQualityInsights(asset, analysis) {
  const node = document.getElementById("quality-insight-summary");
  if (!node) {
    return;
  }
  if (!asset) {
    node.replaceChildren(textElement("span", "暂无资产"));
    return;
  }
  if (!analysis) {
    node.replaceChildren(
      textElement("span", "质量洞察"),
      textElement("strong", "读取中"),
      textElement("small", "正在读取 Phase 6 分析报告"),
    );
    return;
  }

  const findingCount = (analysis.findings || []).length;
  const gridCount = analysis.grid?.cell_count || 0;
  const rgbPercent = Math.round(Number(analysis.rgb_coverage || 0) * 100);
  node.replaceChildren(
    textElement("span", `${asset.id} · ${formatNumber(analysis.point_count || 0)} points`),
    textElement("strong", `${rgbPercent}% RGB`),
    textElement("small", `${gridCount} 个网格 · ${findingCount} 项质量提示`),
  );
}

function renderJobSummary(asset, summary) {
  const node = document.getElementById("job-status-summary");
  if (!node) {
    return;
  }
  if (!asset) {
    node.replaceChildren(textElement("span", "暂无资产"));
    return;
  }
  if (!summary) {
    node.replaceChildren(
      textElement("span", "生产任务"),
      textElement("strong", "读取中"),
      textElement("small", "正在检查 Phase 4 job 状态"),
    );
    return;
  }

  const latestJob = summary.latest_job;
  const statusSummary = summary.status_summary || {};
  const detail = Object.entries(statusSummary)
    .map(([status, count]) => `${statusText(status)} ${count}`)
    .join(" · ") || "暂无 job 状态";
  node.replaceChildren(
    textElement("span", `${asset.id} · ${summary.job_count || 0} 个 job`),
    textElement("strong", latestJob ? statusText(latestJob.status) : "暂无任务"),
    textElement("small", latestJob ? `${latestJob.job_id} · ${detail}` : "请先创建 production job"),
  );
}

function drawPointCloudPreview(asset) {
  const stage = document.getElementById("project-preview");
  stage.replaceChildren();

  // 当前首页只做驾驶舱缩略预览，真实三维成果会在 C 风格展示页中承载。
  for (let index = 0; index < 7; index += 1) {
    const dot = document.createElement("span");
    dot.className = `cloud-cluster cluster-${index + 1}`;
    stage.append(dot);
  }

  const caption = document.createElement("div");
  caption.className = "preview-caption";
  caption.textContent = `${asset.name} · ${asset.format} · ${formatNumber(asset.point_count)} points`;
  stage.append(caption);
}

function assetPill(label, value) {
  const node = document.createElement("div");
  node.className = "asset-pill";
  node.append(textElement("span", label), textElement("strong", value));
  return node;
}

function textElement(tagName, text) {
  const node = document.createElement(tagName);
  node.textContent = text;
  return node;
}

async function initWorkbench() {
  activeProject = await fetchProjectData();
  renderDashboard(activeProject);
}

initWorkbench();

// 兼容 FE-M1 旧测试命名：正式首页现在由 renderDashboard 承载流程摘要。
function renderWorkflow(project) {
  renderDecisions(project);
}







