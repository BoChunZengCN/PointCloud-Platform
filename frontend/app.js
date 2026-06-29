const API_BASE_URL = window.PC_SYSTEM_API_BASE_URL || "http://127.0.0.1:8000";
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

async function loadJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${url}`);
  }
  return await response.json();
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
  return {
    id: item.asset_id,
    name: item.file_name || item.asset_id,
    format: "LAS/LAZ",
    source: item.source_path || "workspace_registry",
    point_count: item.point_count || 0,
    colorized: Boolean(item.has_rgb),
    coordinate_system: "来自 asset_index.json",
    bounds: JSON.stringify(item.bounds || {}),
    reports: [
      { name: "质量报告", kind: "QA", href: `../workspace/${reportPaths.quality_report || ""}`, status: reportPaths.quality_report ? "ready" : "planned" },
      { name: "生产运行计划", kind: "Production", href: `../workspace/${reportPaths.production_plan || ""}`, status: reportPaths.production_plan ? "ready" : "planned" },
      { name: "生产运行报告", kind: "Production", href: `../workspace/${reportPaths.production_report || ""}`, status: reportPaths.production_report ? "ready" : "planned" },
      { name: "Phase 2 Viewer", kind: "Viewer", href: `../workspace/${previewPaths.phase2_viewer || ""}`, status: previewPaths.phase2_viewer ? "ready" : "planned" },
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
  renderAssetSelector(project);
  renderAssetInsight(project, asset);
  renderDecisions(project);
  renderReports(asset);
  renderJobSummary(asset, null);
  if (project.sourceType === "api" && asset) {
    fetchJobSummary(asset.id)
      .then((summary) => renderJobSummary(asset, summary))
      .catch(() => renderJobSummary(asset, { job_count: 0, latest_job: null, status_summary: {} }));
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
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
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






