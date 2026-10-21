"use strict";
(function () {
  const $ = id => document.getElementById(id);
  const state = {
    token:"", role:"unverified", filter:"all", selected:null,
    writeBusy:false, readBusy:0, pending:null, preview:null,
  };
  const uuid = () => crypto.randomUUID();
  const writeControls = () => document.querySelectorAll("[data-write]");

  function message(text, kind="") {
    $("message").textContent = text;
    $("message").dataset.kind = kind;
  }

  function node(text, tag="span") {
    const element = document.createElement(tag);
    element.textContent = text;
    return element;
  }

  async function api(path, body) {
    const headers = {};
    if (state.token) headers.Authorization = "Bearer " + state.token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method:body === undefined ? "GET" : "POST",
      credentials:"same-origin",
      headers,
      body:body === undefined ? undefined : JSON.stringify(body),
    });
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error("服务未返回 JSON 响应"); }
    if (!response.ok) {
      const detail = data && typeof data.detail === "object" && data.detail ? data.detail : {};
      const error = new Error(detail.message || "请求失败");
      error.status = response.status;
      error.code = typeof detail.code === "string" ? detail.code : null;
      throw error;
    }
    return data;
  }

  function operationIds(prefix) {
    const value = uuid();
    return {
      operation_id:`${prefix}-op-${value}`,
      request_id:`${prefix}-req-${value}`,
      idempotency_key:`${prefix}-idem-${value}`,
    };
  }

  function freezeEnvelope(path, body, target) {
    const frozenBody = Object.freeze(JSON.parse(JSON.stringify(body)));
    return Object.freeze({path, body:frozenBody, target:Object.freeze({...target})});
  }

  function setRole(role) {
    state.role = role || "unverified";
    $("role-note").textContent = state.role === "expert"
      ? "专家：可创建运行、作出决定并发布。"
      : state.role === "auditor"
        ? "审计员：可查看专业证据，所有写操作已禁用。"
        : state.role === "operator"
          ? "业务角色：仅显示当前已发布网格摘要。"
          : "尚未连接；所有写操作已禁用。";
    renderControls();
  }

  function currentReasons() {
    return state.selected && state.selected.quality && Array.isArray(state.selected.quality.reasons)
      ? state.selected.quality.reasons : [];
  }

  function allRisksChecked() {
    const controls = [...$("risk-acknowledgements").querySelectorAll("input[data-risk-code]")];
    return controls.every(control => control.checked);
  }

  function renderControls() {
    const blocked = state.writeBusy || state.pending !== null;
    const expert = state.role === "expert";
    writeControls().forEach(control => { control.disabled = blocked || !expert; });
    $("connect").disabled = blocked || state.readBusy > 0;
    $("retry").hidden = state.pending === null;
    $("retry").disabled = state.writeBusy || state.pending === null;
    document.querySelectorAll("[data-status], #run-list button").forEach(control => {
      control.disabled = blocked || state.readBusy > 0;
    });
    const row = state.selected;
    const decided = Boolean(row && row.decision);
    const rejected = Boolean(row && row.status === "rejected");
    const decisionReason = $("decision-reason").value.trim();
    $("approve").hidden = !row || rejected || decided || !expert;
    $("reject").hidden = !row || decided || !expert;
    $("approve").disabled = blocked || !expert || rejected || decided || !decisionReason || !allRisksChecked();
    $("reject").disabled = blocked || !expert || decided || !decisionReason;
    const approved = Boolean(row && row.decision && row.decision.decision === "approve");
    $("release").disabled = blocked || !expert || !approved || !$("release-id").value.trim() || !$("release-reason").value.trim();
    $("create-run").disabled = blocked || !expert || !$("config-id").value || !$("policy-id").value;
  }

  function showSourceFields() {
    const phase14 = $("source-kind").value === "phase14_correction_release";
    $("phase14-source").hidden = !phase14;
    $("phase15e-source").hidden = phase14;
  }

  function fillSelect(id, items, field, label) {
    const options = items.map(item => {
      const option = document.createElement("option");
      option.value = item[field];
      option.textContent = `${item[field]} · ${label(item)}`;
      return option;
    });
    $(id).replaceChildren(...options);
  }

  async function loadBootstrap() {
    state.readBusy += 1;
    renderControls();
    try {
      const [session, configs, policies] = await Promise.all([
        api("/scan-to-mesh/session"),
        api("/scan-to-mesh/configs"),
        api("/scan-to-mesh/quality-policies"),
      ]);
      setRole(session.viewer_role);
      fillSelect("config-id", configs.items, "config_id", item => item.algorithm || "已发布");
      fillSelect("policy-id", policies.items, "policy_id", item => item.policy_kind || "已发布");
      await loadRuns("all", {duringRead:true});
      message("连接成功。", "ok");
    } catch (error) {
      setRole("unverified");
      message(`连接失败：${error.message}`, "error");
    } finally {
      state.readBusy -= 1;
      renderControls();
    }
  }

  function sourceLabel(row) {
    const identity = row.source_identity || {};
    if (row.source_kind === "phase14_correction_release") {
      return `Phase 14 · ${identity.asset_id || "-"} / ${identity.instance_id || "-"}`;
    }
    return `Phase 15E · ${identity.model_id || "-"} / ${identity.version_id || "-"}`;
  }

  function renderList(items) {
    const buttons = items.map(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.append(node(item.run_id, "strong"), node(`${item.status || item.quality_status} · ${item.publication_status}`, "small"));
      button.addEventListener("click", () => loadDetail(item.run_id));
      return button;
    });
    $("run-list").replaceChildren(...buttons);
    if (!items.length) $("run-list").replaceChildren(node("暂无符合条件的网格运行。", "p"));
  }

  async function loadRuns(status=state.filter, {duringRead=false}={}) {
    if (!state.token || (!duringRead && (state.writeBusy || state.pending))) return;
    state.readBusy += 1;
    renderControls();
    try {
      const data = await api(`/scan-to-mesh/runs?status=${encodeURIComponent(status)}`);
      setRole(data.viewer_role);
      state.filter = status;
      document.querySelectorAll("[data-status]").forEach(button => {
        button.dataset.active = String(button.dataset.status === status);
      });
      renderList(data.items);
    } catch (error) {
      message(`读取运行目录失败：${error.message}`, "error");
    } finally {
      state.readBusy -= 1;
      renderControls();
    }
  }

  function renderQuality(row) {
    const quality = row.quality;
    $("quality-summary").textContent = `${quality.summary_zh} 风险：${quality.reasons.join("、") || "无"}`;
    const labels = {
      mesh_to_point_coverage:"点云覆盖率",
      point_to_mesh_p95_m:"点到网格 P95（m）",
      component_count:"连通组件",
      raw_triangle_count:"原始三角面",
      repaired_triangle_count:"修复三角面",
      repair_max_displacement_m:"修复最大位移（m）",
    };
    $("quality-metrics").replaceChildren(...Object.entries(labels).map(([field, label]) => {
      const line = document.createElement("p");
      line.textContent = `${label}：${quality.metrics[field]}`;
      return line;
    }));
  }

  function renderRisks(row) {
    const risks = row.quality.reasons || [];
    $("risk-acknowledgements").replaceChildren(...risks.map(code => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.riskCode = code;
      input.addEventListener("input", renderControls);
      label.append(input, document.createTextNode(`确认风险：${code}`));
      return label;
    }));
  }

  function renderReleaseHistory(row) {
    const lines = (row.release_history || []).map(release => {
      const line = document.createElement("p");
      line.textContent = `${release.release_id} · ${release.action} · ${release.created_at}`;
      return line;
    });
    $("release-history").replaceChildren(...lines);
    if (!lines.length) $("release-history").replaceChildren(node("尚无发布历史。", "p"));
  }

  function renderProfessional(row) {
    $("business-summary").textContent = `专业运行：${sourceLabel(row)}`;
    $("professional-detail").hidden = false;
    $("source-summary").textContent = sourceLabel(row);
    $("configuration-summary").textContent = `配置：${row.config_id}；策略：${row.policy_id}`;
    renderQuality(row);
    $("publication-summary").textContent = row.publication_status === "current"
      ? `当前发布：${row.current_release_id}`
      : row.publication_status === "historical" ? "历史发布版本" : "尚未发布";
    const decisionLabel = row.decision
      ? row.decision.decision === "approve"
        ? (row.decision.automated ? "系统自动批准" : "专家已批准")
        : "专家已驳回"
      : null;
    $("decision-summary").textContent = row.decision
      ? `${decisionLabel}：${row.decision.reason}`
      : "尚无最终决定";
    $("audit-summary").textContent = row.audit
      ? `操作：${row.audit.operation_id}；主体：${row.audit.actor_id}；策略：${row.policy_id}`
      : `策略：${row.policy_id}；运行指纹：${row.manifest_fingerprint}`;
    renderRisks(row);
    renderReleaseHistory(row);
    $("decision-reason").value = "";
    $("release-reason").value = "";
    $("release-id").value = "";
    $("release-action").value = row.current_release_id ? "replace" : "activate";
  }

  function renderBusiness(row) {
    $("business-summary").textContent = `${sourceLabel(row)}；质量：${row.quality_status}；已发布：${row.current_release_id}`;
    $("professional-detail").hidden = true;
  }

  function coordinates(preview) {
    const values = [...preview.source.points];
    for (const key of ["raw_mesh", "repaired_mesh"]) {
      for (const triangle of preview[key].triangles) values.push(...triangle);
    }
    return values;
  }

  function drawPreview() {
    const canvas = $("mesh-preview");
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    const preview = state.preview;
    if (!preview) { delete canvas.dataset.rendered; return; }
    const project = point => [point[0] - point[1] * .55, point[2] + point[1] * .32];
    const projected = coordinates(preview).map(project);
    const xs = projected.map(value => value[0]);
    const ys = projected.map(value => value[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const scale = Math.min((canvas.width - 70) / (maxX - minX || 1), (canvas.height - 70) / (maxY - minY || 1));
    const screen = point => {
      const [x, y] = project(point);
      return [35 + (x - minX) * scale, canvas.height - 35 - (y - minY) * scale];
    };
    const drawMesh = (triangles, color, width) => {
      context.strokeStyle = color; context.lineWidth = width;
      for (const triangle of triangles) {
        context.beginPath();
        triangle.forEach((point, index) => {
          const [x, y] = screen(point);
          if (!index) context.moveTo(x, y); else context.lineTo(x, y);
        });
        context.closePath(); context.stroke();
      }
    };
    if ($("show-raw").checked) drawMesh(preview.raw_mesh.triangles, "rgba(255,166,86,.58)", 1);
    if ($("show-repaired").checked) drawMesh(preview.repaired_mesh.triangles, "rgba(104,225,173,.82)", 1.6);
    if ($("show-points").checked) {
      context.fillStyle = "#8fdcff";
      for (const point of preview.source.points) {
        const [x, y] = screen(point); context.fillRect(x - 1.5, y - 1.5, 3, 3);
      }
    }
    canvas.dataset.rendered = "true";
  }

  async function loadPreview(runId) {
    if (state.role === "operator") return;
    try {
      state.preview = await api(`/scan-to-mesh/runs/${encodeURIComponent(runId)}/preview`);
      drawPreview();
    } catch (error) {
      state.preview = null;
      drawPreview();
      message(`预览读取失败：${error.message}`, "error");
    }
  }

  async function loadDetail(runId, {duringWrite=false}={}) {
    if (!duringWrite && (state.writeBusy || state.pending)) return;
    state.readBusy += 1;
    renderControls();
    try {
      const row = await api(`/scan-to-mesh/runs/${encodeURIComponent(runId)}`);
      setRole(row.viewer_role);
      state.selected = row;
      $("detail").hidden = false;
      $("detail-title").textContent = row.run_id;
      const status = row.status || row.quality_status;
      $("status-chip").textContent = status;
      if (row.viewer_role === "operator") renderBusiness(row); else renderProfessional(row);
      await loadPreview(runId);
    } catch (error) {
      message(`详情读取失败：${error.message}`, "error");
    } finally {
      state.readBusy -= 1;
      renderControls();
    }
  }

  async function runPending() {
    if (state.writeBusy || !state.pending) return;
    state.writeBusy = true;
    renderControls();
    message("正在提交，请勿重复操作…");
    try {
      const pending = state.pending;
      const result = await api(pending.path, pending.body);
      state.pending = null;
      message("操作已保存。", "ok");
      await loadRuns(state.filter, {duringRead:true});
      await loadDetail(pending.target.runId || result.run_id, {duringWrite:true});
    } catch (error) {
      const recoverable = !error.status || error.status >= 500 || ["operation_busy", "publication_recovery_required", "operation_persistence_failed"].includes(error.code);
      if (!recoverable) state.pending = null;
      message(recoverable ? "请求结果未确认；请重试原操作。" : `操作失败：${error.message}`, "error");
    } finally {
      state.writeBusy = false;
      renderControls();
    }
  }

  function startWrite(envelope, confirmation) {
    if (state.writeBusy || state.pending) return;
    if (!window.confirm(confirmation)) return;
    state.pending = envelope;
    renderControls();
    runPending();
  }

  function createBody() {
    const sourceKind = $("source-kind").value;
    const sourceRef = sourceKind === "phase14_correction_release"
      ? {asset_id:$("asset-id").value.trim(), release_id:$("source-release-id").value.trim(), instance_id:$("instance-id").value.trim()}
      : {model_id:$("model-id").value.trim(), version_id:$("version-id").value.trim()};
    return {
      run_id:$("run-id").value.trim(), source_kind:sourceKind, source_ref:sourceRef,
      config_id:$("config-id").value, policy_id:$("policy-id").value,
      ...operationIds("mesh-create"),
    };
  }

  $("connect").addEventListener("click", () => {
    state.token = $("token").value;
    $("token").value = "";
    state.selected = null;
    $("detail").hidden = true;
    loadBootstrap();
  });
  $("source-kind").addEventListener("change", showSourceFields);
  $("create-run").addEventListener("click", () => {
    const body = createBody();
    startWrite(freezeEnvelope("/scan-to-mesh/runs", body, {runId:body.run_id}), "确认使用所选可信来源创建网格运行？");
  });
  document.querySelectorAll("[data-status]").forEach(button => button.addEventListener("click", () => loadRuns(button.dataset.status)));
  $("decision-reason").addEventListener("input", renderControls);
  $("release-id").addEventListener("input", renderControls);
  $("release-reason").addEventListener("input", renderControls);
  for (const id of ["show-points", "show-raw", "show-repaired"]) $(id).addEventListener("input", drawPreview);
  $("approve").addEventListener("click", () => {
    const row = state.selected;
    const acknowledgements = [...$("risk-acknowledgements").querySelectorAll("input[data-risk-code]:checked")].map(input => input.dataset.riskCode).sort();
    const body = {decision:"approve", reason:$("decision-reason").value.trim(), acknowledgements, ...operationIds("mesh-approve")};
    startWrite(freezeEnvelope(`/scan-to-mesh/runs/${encodeURIComponent(row.run_id)}/decision`, body, {runId:row.run_id}), "确认提交一次性批准决定？该决定不能修改。 ");
  });
  $("reject").addEventListener("click", () => {
    const row = state.selected;
    const body = {decision:"reject", reason:$("decision-reason").value.trim(), acknowledgements:[], ...operationIds("mesh-reject")};
    startWrite(freezeEnvelope(`/scan-to-mesh/runs/${encodeURIComponent(row.run_id)}/decision`, body, {runId:row.run_id}), "确认驳回此网格运行？该决定不能修改。 ");
  });
  $("release").addEventListener("click", () => {
    const row = state.selected;
    const body = {
      run_id:row.run_id, release_id:$("release-id").value.trim(), action:$("release-action").value,
      reason:$("release-reason").value.trim(), expected_current_release_id:row.current_release_id,
      ...operationIds("mesh-release"),
    };
    startWrite(freezeEnvelope(`/scan-to-mesh/objects/${encodeURIComponent(row.object_key)}/releases`, body, {runId:row.run_id}), "确认改变此对象的当前网格发布版本？");
  });
  $("retry").addEventListener("click", runPending);
  showSourceFields();
  renderControls();
})();
