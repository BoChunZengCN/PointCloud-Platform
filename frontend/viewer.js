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
  report_paths: {
    quality_report: "reports/site-a-las/quality_report.html",
    production_report: "reports/production_runs/site-a-las/production_run_report.md",
  },
};

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

function deliveryLinks(asset) {
  return [
    {
      kind: "potree",
      title: "Potree",
      label: "点云查看器",
      href: `../workspace/${asset.preview_paths.potree_manifest}`,
    },
    {
      kind: "splat",
      title: "Gaussian Splatting",
      label: "Splat 视觉成果",
      href: `../workspace/${asset.preview_paths.phase2_viewer}`,
    },
    {
      kind: "report",
      title: "质量报告",
      label: "QA / 质检结果",
      href: `../workspace/${asset.report_paths.quality_report}`,
    },
    {
      kind: "report",
      title: "生产运行报告",
      label: "Phase 3 交付记录",
      href: `../workspace/${asset.report_paths.production_report}`,
    },
  ];
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
    { status: "done", label: "QA 完成", note: asset.report_paths.quality_report ? "质量报告可打开" : "等待质量报告" },
    { status: "active", label: "Viewer 发布", note: asset.preview_paths.phase2_viewer ? "展示入口可用" : "等待 Phase 2 Viewer" },
    { status: "planned", label: "生产报告", note: asset.report_paths.production_report ? "报告已登记" : "等待生产运行报告" },
  ];
}

// iframe 嵌入真实 Potree / Gaussian Splatting / 报告入口。
function renderEmbeddedViewer(item) {
  const frame = document.getElementById("viewer-frame");
  frame.src = item.href;
  frame.dataset.kind = item.kind;
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

function renderShowcaseLinks(asset) {
  const links = deliveryLinks(asset).map((item) => {
    const link = document.createElement("a");
    link.className = "showcase-card";
    link.dataset.kind = item.kind;
    link.href = item.href;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      renderEmbeddedViewer(item);
    });
    link.append(textElement("span", item.label), textElement("strong", item.title));
    return link;
  });
  document.getElementById("showcase-links").replaceChildren(...links);
}

function renderShowcase(registry) {
  const asset = registry.assets?.[0] || FALLBACK_ASSET;
  document.getElementById("viewer-title").textContent = `${asset.asset_id} 三维成果查看器`;
  document.getElementById("showcase-summary").textContent = `${asset.asset_id} · ${asset.file_name || asset.asset_id} · ${registry.asset_count} 个资产可展示`;
  renderLayerControls();
  renderAssetFacts(asset);
  renderViewerTasks(asset);
  renderShowcaseLinks(asset);
  renderEmbeddedViewer(deliveryLinks(asset)[1] || deliveryLinks(asset)[0]);
}

function textElement(tagName, text) {
  const node = document.createElement(tagName);
  node.textContent = text;
  return node;
}

loadRegistry().then(renderShowcase);
