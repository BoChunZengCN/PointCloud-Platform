const API_BASE_URL = window.PC_SYSTEM_API_BASE_URL || "http://127.0.0.1:8000";
const REGISTRY_URL = "../workspace/data/assets/asset_index.json";

const FALLBACK_ASSET = {
  asset_id: "site-a-las",
  file_name: "sample.las",
  point_count: 12840000,
  has_rgb: true,
  preview_paths: {
    potree_manifest: "previews/site-a-las/potree_manifest.json",
    phase2_viewer: "previews/site-a-las/phase2_viewer_manifest.json",
  },
  viewer_paths: {
    viewer_url: "previews/site-a-las/phase2_viewer.html",
    viewer_html_path: "previews/site-a-las/phase2_viewer.html",
    manifest_path: "previews/site-a-las/phase2_viewer_manifest.json",
    potree_manifest_path: "previews/site-a-las/potree_manifest.json",
    report_path: "reports/production_runs/site-a-las/production_run_report.md",
  },
  report_paths: {
    quality_report: "reports/site-a-las/quality_report.html",
    production_report: "reports/production_runs/site-a-las/production_run_report.md",
  },
};

let activeRegistry = null;
let activeDeliveryId = "";

async function loadRegistry() {
  try {
    const response = await fetch(REGISTRY_URL, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("asset_index.json unavailable");
    }
    return await response.json();
  } catch (error) {
    return { asset_count: 1, assets: [FALLBACK_ASSET] };
  }
}

async function fetchDeliveryStatus(assetId) {
  try {
    const response = await fetch(`${API_BASE_URL}/delivery/${encodeURIComponent(assetId)}/status`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("delivery status unavailable");
    }
    return await response.json();
  } catch (error) {
    return null;
  }
}

function selectedAssetIdFromUrl() {
  return new URLSearchParams(window.location.search).get("asset_id") || "";
}

function viewerUrlForAssetId(assetId) {
  return `viewer.html?asset_id=${encodeURIComponent(assetId)}`;
}

function selectShowcaseAsset(registry, selectedAssetId) {
  return registry.assets?.find((asset) => asset.asset_id === selectedAssetId) || registry.assets?.[0] || FALLBACK_ASSET;
}

function switchShowcaseAsset(assetId) {
  const nextUrl = viewerUrlForAssetId(assetId);
  window.history.replaceState({}, "", nextUrl);
  activeDeliveryId = "";
  void renderShowcase(activeRegistry, assetId);
}

function workspaceHref(path) {
  return path ? `../workspace/${path}` : "";
}

function deliveryLinks(asset) {
  const viewerPaths = asset.viewer_paths || {};
  const previewPaths = asset.preview_paths || {};
  const reportPaths = asset.report_paths || {};
  return [
    {
      id: "potree",
      outputKey: "potree_manifest_path",
      kind: "potree",
      title: "Potree",
      label: "点云查看器",
      href: workspaceHref(viewerPaths.potree_viewer_url || viewerPaths.potree_html_path || previewPaths.potree_viewer || previewPaths.potree_html || previewPaths.potree_manifest || viewerPaths.potree_manifest_path),
    },
    {
      id: "splat",
      outputKey: "viewer_url",
      kind: "splat",
      title: "Gaussian Splatting",
      label: "Splat 视觉成果",
      href: workspaceHref(viewerPaths.viewer_url || viewerPaths.viewer_html_path || previewPaths.splat_viewer || previewPaths.phase2_viewer || viewerPaths.manifest_path),
    },
    {
      id: "quality-report",
      outputKey: "quality_report",
      kind: "report",
      title: "质量报告",
      label: "QA / 质检结果",
      href: workspaceHref(reportPaths.quality_report),
    },
    {
      id: "production-report",
      outputKey: "report_path",
      kind: "report",
      title: "生产运行报告",
      label: "Phase 3 交付记录",
      href: workspaceHref(reportPaths.production_report || viewerPaths.report_path),
    },
  ];
}

function statusByPath(deliveryStatus) {
  const outputs = deliveryStatus?.outputs || {};
  const byPath = new Map();
  Object.entries(outputs).forEach(([key, value]) => {
    if (value?.path) {
      byPath.set(`../workspace/${value.path}`, { ...value, outputKey: key });
    }
  });
  return byPath;
}

function applyDeliveryStatus(items, deliveryStatus) {
  const outputs = deliveryStatus?.outputs || {};
  const byPath = statusByPath(deliveryStatus);
  return items.map((item) => ({
    ...item,
    deliveryStatus: outputs[item.outputKey] || byPath.get(item.href) || null,
  }));
}

function isEmbeddableViewer(href) {
  return /\.html?(#.*|\?.*)?$/i.test(href || "");
}

function isManifestOutput(href) {
  return /manifest\.json(#.*|\?.*)?$/i.test(href || "") || /\.json(#.*|\?.*)?$/i.test(href || "");
}

function deliveryItemStatus(item) {
  if (item.deliveryStatus) {
    if (!item.deliveryStatus.exists) {
      return { status: "missing", label: "缺失", note: "API 已确认该交付物文件不存在。" };
    }
    if (item.deliveryStatus.kind === "html") {
      return { status: "embeddable", label: "可嵌入", note: "API 已确认这是可嵌入 HTML 查看器。" };
    }
    if (item.deliveryStatus.kind === "manifest") {
      return { status: "manifest", label: "Manifest", note: "API 已确认这是清单 JSON，不是可嵌入 HTML。" };
    }
    if (item.deliveryStatus.kind === "report") {
      return { status: "openable", label: "可打开", note: "API 已确认这是报告交付物。" };
    }
  }
  if (!item.href) {
    return { status: "missing", label: "缺失", note: "该交付物尚未登记输出路径。" };
  }
  if (item.kind === "report") {
    return { status: "openable", label: "可打开", note: "报告会在新页面打开。" };
  }
  if (isEmbeddableViewer(item.href)) {
    return { status: "embeddable", label: "可嵌入", note: "可直接加载到左侧查看器。" };
  }
  if (isManifestOutput(item.href)) {
    return { status: "manifest", label: "Manifest", note: "这是清单 JSON，不是可嵌入 HTML 查看器。" };
  }
  return { status: "openable", label: "可打开", note: "该链接会在新页面打开。" };
}

function layerItems() {
  return [
    { id: "rgb", label: "RGB 点云", enabled: true },
    { id: "segment", label: "分割类别", enabled: true },
    { id: "splat", label: "高斯泼溅预览", enabled: false },
    { id: "qa", label: "QA 异常标记", enabled: false },
  ];
}

function viewerTasks(asset) {
  return [
    { status: "done", label: "QA 完成", note: asset.report_paths?.quality_report ? "质量报告可打开" : "等待质量报告" },
    { status: "active", label: "Viewer 发布", note: asset.viewer_paths?.viewer_url || asset.preview_paths?.phase2_viewer ? "展示入口可用" : "等待 Phase 2 Viewer" },
    { status: "planned", label: "生产报告", note: asset.report_paths?.production_report ? "报告已登记" : "等待生产运行报告" },
  ];
}

function renderViewerStatus(status, title, detail) {
  const bar = document.getElementById("viewer-status-bar");
  if (!bar) {
    return;
  }
  bar.setAttribute("data-viewer-status", status);
  bar.querySelector("strong").textContent = title;
  document.getElementById("viewer-status-title").textContent = "当前查看器";
  document.getElementById("viewer-status-detail").textContent = detail;
}

function setActiveDeliveryItem(deliveryId) {
  activeDeliveryId = deliveryId || "";
  document.querySelectorAll(".showcase-card").forEach((card) => {
    const isActive = card.getAttribute("data-delivery-id") === activeDeliveryId;
    card.className = isActive ? "showcase-card active" : "showcase-card";
  });
}

// iframe 只嵌入真实 HTML 查看器，manifest JSON 与缺失项会保留为明确状态提示。
function renderEmbeddedViewer(item) {
  const frame = document.getElementById("viewer-frame");
  frame.src = item.href;
  frame.dataset.kind = item.kind;
  setActiveDeliveryItem(item.id);
  hideViewerEmptyState();
  renderViewerStatus("embeddable", item.title, `正在显示：${item.label}`);
}

function clearEmbeddedViewer(reason = "未自动加载：没有找到可嵌入 HTML 查看器。") {
  const frame = document.getElementById("viewer-frame");
  frame.removeAttribute("src");
  frame.dataset.kind = "empty";
  setActiveDeliveryItem("");
  renderViewerStatus("empty", "未自动加载", reason);
  renderViewerEmptyState(reason);
}

function renderViewerEmptyState(reason) {
  const empty = document.getElementById("viewer-empty-state");
  if (!empty) {
    return;
  }
  empty.hidden = false;
  empty.replaceChildren(
    textElement("strong", "暂无可嵌入查看器"),
    textElement("span", reason),
    textElement("code", "pc-system publish-phase2-viewer"),
    textElement("code", "pc-system plan-production-run"),
  );
}

function hideViewerEmptyState() {
  const empty = document.getElementById("viewer-empty-state");
  if (empty) {
    empty.hidden = true;
  }
}

function renderShowcaseAssetSwitcher(registry, selectedAsset) {
  const list = document.getElementById("showcase-asset-switcher");
  const assets = registry.assets?.length ? registry.assets : [FALLBACK_ASSET];
  const buttons = assets.map((asset) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = asset.asset_id === selectedAsset.asset_id ? "showcase-asset-button active" : "showcase-asset-button";
    button.dataset.assetId = asset.asset_id;
    button.addEventListener("click", () => switchShowcaseAsset(asset.asset_id));
    button.append(
      textElement("span", asset.asset_id),
      textElement("strong", asset.file_name || asset.asset_id),
    );
    return button;
  });
  list.replaceChildren(...buttons);
}

function renderLayerControls() {
  const list = document.getElementById("layer-controls");
  const controls = layerItems().map((item) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = item.enabled;
    input.dataset.layer = item.id;
    label.append(input, document.createTextNode(item.label));
    return label;
  });
  list.replaceChildren(...controls);
}

function renderAssetFacts(asset) {
  const facts = document.getElementById("asset-facts");
  facts.replaceChildren(
    fact("资产", asset.asset_id),
    fact("文件", asset.file_name || asset.asset_id),
    fact("点数", new Intl.NumberFormat("zh-CN").format(asset.point_count || 0)),
    fact("颜色", asset.has_rgb ? "RGB 已保留" : "未记录"),
  );
}

function fact(term, value) {
  const fragment = document.createDocumentFragment();
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = term;
  dd.textContent = value;
  fragment.append(dt, dd);
  return fragment;
}

function renderViewerTasks(asset) {
  const list = document.getElementById("viewer-task-list");
  const nodes = viewerTasks(asset).map((task) => {
    const item = document.createElement("article");
    item.className = `mini-status ${task.status}`;
    item.append(textElement("strong", task.label), textElement("span", task.note));
    return item;
  });
  list.replaceChildren(...nodes);
}

function renderShowcaseLinks(asset, deliveryStatus = null) {
  const deliveryItems = applyDeliveryStatus(deliveryLinks(asset), deliveryStatus);
  const links = deliveryItems.map((item) => {
    const state = deliveryItemStatus(item);
    const link = document.createElement("a");
    link.className = item.id === activeDeliveryId ? "showcase-card active" : "showcase-card";
    link.dataset.kind = item.kind;
    link.setAttribute("data-status", state.status);
    link.setAttribute("data-delivery-id", item.id);
    link.href = item.href || "#";
    link.target = state.status === "embeddable" || state.status === "missing" ? "" : "_blank";
    link.rel = link.target ? "noreferrer" : "";
    link.addEventListener("click", (event) => {
      if (state.status === "missing") {
        event.preventDefault();
        setActiveDeliveryItem(item.id);
        renderViewerStatus("missing", item.title, state.note);
        renderViewerEmptyState(state.note);
        return;
      }
      if (state.status === "embeddable") {
        event.preventDefault();
        renderEmbeddedViewer(item);
        return;
      }
      setActiveDeliveryItem(item.id);
      renderViewerStatus(state.status, item.title, state.note);
    });
    link.append(
      textElement("span", item.label),
      textElement("strong", item.title),
      statusElement(state),
    );
    return link;
  });
  document.getElementById("showcase-links").replaceChildren(...links);
}

function statusElement(state) {
  const node = document.createElement("small");
  node.className = "showcase-status";
  node.textContent = `${state.label} · ${state.note}`;
  return node;
}

function renderInitialEmbeddedViewer(asset, deliveryStatus = null) {
  const initial = applyDeliveryStatus(deliveryLinks(asset), deliveryStatus).find((item) => deliveryItemStatus(item).status === "embeddable");
  if (initial) {
    renderEmbeddedViewer(initial);
  } else {
    clearEmbeddedViewer();
  }
}

async function renderShowcase(registry, selectedAssetId = selectedAssetIdFromUrl()) {
  activeRegistry = registry;
  const asset = selectShowcaseAsset(registry, selectedAssetId);
  const deliveryStatus = await fetchDeliveryStatus(asset.asset_id);
  document.getElementById("viewer-title").textContent = `${asset.asset_id} 三维成果查看器`;
  document.getElementById("showcase-summary").textContent = `${asset.asset_id} · ${asset.file_name || asset.asset_id} · ${registry.asset_count} 个资产可展示`;
  renderShowcaseAssetSwitcher(registry, asset);
  renderLayerControls();
  renderAssetFacts(asset);
  renderViewerTasks(asset);
  renderShowcaseLinks(asset, deliveryStatus);
  renderInitialEmbeddedViewer(asset, deliveryStatus);
}

function textElement(tagName, text) {
  const node = document.createElement(tagName);
  node.textContent = text;
  return node;
}

loadRegistry().then((registry) => { void renderShowcase(registry); });
