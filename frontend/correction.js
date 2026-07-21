(function initializeCorrectionWorkbench() {
  const api = window.segmentationCorrection;
  const params = new URLSearchParams(window.location.search);
  const assetId = params.get("asset_id");
  const sessionId = params.get("session_id");
  const requestedActor = params.get("actor");
  const defaultApiBase = "http://127.0.0.1:8000";
  const requestedApiUrl = new URL(params.get("api") || defaultApiBase, window.location.href);
  const defaultApiOrigin = new URL(defaultApiBase).origin;
  const trustedApiOrigin =
    requestedApiUrl.origin === window.location.origin ||
    requestedApiUrl.origin === defaultApiOrigin;
  const apiBase = (trustedApiOrigin ? requestedApiUrl.origin : defaultApiBase).replace(/\/$/, "");
  const state = {
    session: null,
    points: [],
    objects: [],
    queue: [],
    selectedIndices: new Set(),
    selectedInstances: new Set(),
    camera: { view: "top", zoom: 30, panX: 0, panY: 0 },
    tool: "object",
    gesture: null,
    showBaseline: false,
    readOnly: false,
  };
  const canvas = document.getElementById("correction-canvas");
  const context = canvas.getContext("2d");

  function currentActor() {
    return (
      requestedActor ||
      localStorage.getItem("pc-system-actor") ||
      state.session?.active_editor ||
      "browser-user"
    );
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function endpoint(suffix = "") {
    return `${apiBase}/segmentation-corrections/${encodeURIComponent(assetId)}/${encodeURIComponent(sessionId)}${suffix}`;
  }

  async function request(url, options = {}) {
    const key = localStorage.getItem("pc-system-api-key");
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (key && trustedApiOrigin) headers["X-API-Key"] = key;
    const response = await fetch(url, { ...options, headers });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(payload?.detail?.message || payload?.detail || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function resizeCanvas() {
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * ratio));
    canvas.height = Math.max(1, Math.round(rect.height * ratio));
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    draw();
  }

  function projectedPoints() {
    const rect = canvas.getBoundingClientRect();
    return state.points.map((point) => {
      const visiblePoint = state.showBaseline
        ? { ...point, draft: point.baseline }
        : point;
      return api.projectPoint(visiblePoint, state.camera, { width: rect.width, height: rect.height });
    });
  }

  function colorFor(point) {
    if (point.is_noise) return "#697887";
    if (state.selectedIndices.has(point.source_point_index)) return "#ffb866";
    if (state.selectedInstances.has(point.instance_id)) return "#58d6b1";
    let hash = 0;
    for (const character of String(point.instance_id)) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
    return `hsl(${hash % 360} 62% 61%)`;
  }

  function draw() {
    const rect = canvas.getBoundingClientRect();
    context.clearRect(0, 0, rect.width, rect.height);
    context.fillStyle = "#050a0f";
    context.fillRect(0, 0, rect.width, rect.height);
    for (const point of projectedPoints()) {
      context.fillStyle = colorFor(point);
      context.beginPath();
      context.arc(point.screenX, point.screenY, 3, 0, Math.PI * 2);
      context.fill();
    }
  }

  function render() {
    const model = api.buildCorrectionViewModel(
      state.session,
      { items: state.queue },
      { objects: state.objects },
    );
    document.getElementById("session-status").textContent =
      `${model.status} · rev ${model.revision}`;
    document.getElementById("queue-count").textContent = model.suggestions.length;
    document.getElementById("object-count").textContent = model.objects.length;
    document.getElementById("review-queue").innerHTML = model.suggestions
      .map((item) => `<button class="review-item ${item.confirmed ? "confirmed" : ""}" data-instance="${escapeHtml(item.instance_id)}">
        <strong>${escapeHtml(item.instance_id)}</strong><small>${escapeHtml(item.reason_code)} · 建议 ${escapeHtml(item.suggested_action)}</small>
      </button>`)
      .join("");
    document.getElementById("selected-objects").innerHTML = model.objects
      .map((item) => `<button class="object-item ${state.selectedInstances.has(item.instance_id) ? "selected" : ""}" data-instance="${escapeHtml(item.instance_id)}">
        <strong>${escapeHtml(item.instance_id)}</strong><small>${escapeHtml(item.class_id)} · ${Number(item.point_count) || 0} 点 · ${escapeHtml(item.review_state)}</small>
      </button>`)
      .join("");
    const diff = state.session?.correction_diff || {};
    document.getElementById("diff-summary").innerHTML = [
      ["变化点", diff.changed_point_count || 0],
      ["新增对象", diff.created_instance_count || 0],
      ["移除对象", diff.removed_instance_count || 0],
      ["改类对象", diff.class_change_count || 0],
    ].map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
    document.getElementById("selection-summary").textContent =
      `已选 ${state.selectedInstances.size} 个对象、${state.selectedIndices.size} 个点`;
    document.getElementById("canvas-empty").hidden = state.points.length > 0;
    draw();
  }

  async function load() {
    if (!assetId || !sessionId) return;
    try {
      const [session, points, objects, queue] = await Promise.all([
        request(endpoint()),
        request(endpoint("/points?offset=0&limit=50000")),
        request(endpoint("/objects")),
        request(endpoint("/queue")),
      ]);
      state.session = session;
      state.points = points.points;
      state.objects = objects.objects;
      state.queue = queue.items;
      if (points.total > points.points.length) {
        const banner = document.getElementById("conflict-banner");
        banner.hidden = false;
        banner.textContent = `当前仅显示前 ${points.points.length} / ${points.total} 个点；请缩小样本后再做精确点纠正。`;
      }
      render();
    } catch (error) {
      showError(error);
    }
  }

  function showError(error) {
    const banner = document.getElementById("conflict-banner");
    banner.hidden = false;
    banner.textContent =
      error.status === 409
        ? `修订冲突：${error.message}。已重新加载最新状态。`
        : error.status === 423
          ? `当前只读：${error.message}`
          : error.message;
    if (error.status === 409) load();
    if (error.status === 423) {
      state.readOnly = true;
      document.querySelectorAll("[data-action], #submit-review, #return-draft, #publish-release")
        .forEach((button) => { button.disabled = true; });
    }
  }

  async function applyAction(action) {
    if (state.readOnly) return;
    const instanceIds = [...state.selectedInstances];
    const operation = api.buildOperation(action, [...state.selectedIndices], {
      instanceIds,
      instanceId: instanceIds[0],
      targetInstanceId: instanceIds[0],
      classId: document.getElementById("class-id").value.trim(),
    });
    try {
      await request(endpoint("/events"), {
        method: "POST",
        body: JSON.stringify({
          actor: currentActor(),
          expected_revision: state.session.revision,
          client_request_id: `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          operation,
        }),
      });
      state.selectedIndices.clear();
      await load();
    } catch (error) {
      if (error.status === 401) {
        const key = window.prompt("请输入 API Key");
        if (key) localStorage.setItem("pc-system-api-key", key);
      }
      showError(error);
    }
  }

  document.addEventListener("click", (event) => {
    const instanceButton = event.target.closest("[data-instance]");
    if (instanceButton) {
      const id = instanceButton.dataset.instance;
      state.selectedInstances.has(id)
        ? state.selectedInstances.delete(id)
        : state.selectedInstances.add(id);
      render();
      return;
    }
    const actionButton = event.target.closest("[data-action]");
    if (actionButton) applyAction(actionButton.dataset.action);
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) {
      state.camera.view = viewButton.dataset.view;
      document.querySelectorAll("[data-view]").forEach((item) => item.classList.toggle("active", item === viewButton));
      draw();
    }
    const toolButton = event.target.closest("[data-tool]");
    if (toolButton) {
      state.tool = toolButton.dataset.tool;
      document.querySelectorAll("[data-tool]").forEach((item) => item.classList.toggle("active", item === toolButton));
    }
  });
  canvas.addEventListener("click", (event) => {
    if (state.tool !== "object") return;
    const rect = canvas.getBoundingClientRect();
    const nearest = projectedPoints()
      .map((point) => ({ point, distance: Math.hypot(point.screenX - (event.clientX - rect.left), point.screenY - (event.clientY - rect.top)) }))
      .sort((a, b) => a.distance - b.distance)[0];
    if (nearest && nearest.distance <= 12) {
      const index = nearest.point.source_point_index;
      state.selectedIndices.has(index) ? state.selectedIndices.delete(index) : state.selectedIndices.add(index);
      render();
    }
  });
  function canvasPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  }
  canvas.addEventListener("pointerdown", (event) => {
    if (state.tool === "object") return;
    const start = canvasPoint(event);
    state.gesture = { start, points: [start] };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!state.gesture) return;
    const point = canvasPoint(event);
    state.gesture.points.push(point);
    if (state.tool === "brush") {
      for (const projected of projectedPoints()) {
        if (Math.hypot(projected.screenX - point[0], projected.screenY - point[1]) <= 14) {
          state.selectedIndices.add(projected.source_point_index);
        }
      }
      render();
    }
  });
  canvas.addEventListener("pointerup", (event) => {
    if (!state.gesture) return;
    const end = canvasPoint(event);
    let polygon = state.gesture.points;
    if (state.tool === "box") {
      const [x0, y0] = state.gesture.start;
      polygon = [[x0, y0], [end[0], y0], end, [x0, end[1]]];
    }
    if (state.tool !== "brush") {
      api.pickIndices(projectedPoints(), polygon).forEach((index) => state.selectedIndices.add(index));
    }
    state.gesture = null;
    render();
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    state.camera.zoom = Math.max(1, Math.min(500, state.camera.zoom * (event.deltaY > 0 ? 0.9 : 1.1)));
    draw();
  }, { passive: false });
  document.getElementById("fit-view").addEventListener("click", () => {
    if (!state.points.length) return;
    const axes = state.camera.view === "front" ? ["x", "z"] : state.camera.view === "side" ? ["y", "z"] : ["x", "y"];
    const values = axes.map((axis) => state.points.map((point) => Number(point[axis])));
    const ranges = values.map((items) => [Math.min(...items), Math.max(...items)]);
    const rect = canvas.getBoundingClientRect();
    state.camera.panX = -(ranges[0][0] + ranges[0][1]) / 2;
    state.camera.panY = -(ranges[1][0] + ranges[1][1]) / 2;
    state.camera.zoom = Math.max(1, Math.min(rect.width / Math.max(ranges[0][1] - ranges[0][0], 1), rect.height / Math.max(ranges[1][1] - ranges[1][0], 1)) * 0.8);
    draw();
  });
  document.getElementById("show-baseline").addEventListener("change", (event) => {
    state.showBaseline = event.target.checked;
    draw();
  });
  document.getElementById("submit-review").addEventListener("click", async () => {
    try {
      await request(endpoint("/submit"), {
        method: "POST",
        body: JSON.stringify({
          actor: currentActor(),
          expected_revision: state.session.revision,
        }),
      });
      await load();
    } catch (error) {
      showError(error);
    }
  });
  document.getElementById("return-draft").addEventListener("click", async () => {
    if (state.readOnly) return;
    try {
      await request(endpoint("/return"), {
        method: "POST",
        body: JSON.stringify({ actor: currentActor(), expected_revision: state.session.revision }),
      });
      await load();
    } catch (error) { showError(error); }
  });
  document.getElementById("publish-release").addEventListener("click", async () => {
    if (state.readOnly) return;
    const releaseId = window.prompt("发布版本 ID");
    if (!releaseId) return;
    try {
      await request(endpoint("/publish"), {
        method: "POST",
        body: JSON.stringify({
          release_id: releaseId,
          reviewer: currentActor(),
          expected_revision: state.session.revision,
          benchmark_split: "development",
          license: "internal",
        }),
      });
      await load();
    } catch (error) { showError(error); }
  });
  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();
  load();
})();
