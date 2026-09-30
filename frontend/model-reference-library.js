"use strict";
(function () {
  const $ = id => document.getElementById(id);
  const BASE_ACKNOWLEDGEMENTS = ["single_object", "metadata_and_rights", "coverage_limitations"];
  const state = {
    modelId:null, versionId:null, row:null, role:"unverified", token:"",
    readBusy:0, catalogSequence:0, detailSequence:0, writeBusy:false,
    pending:null, warningCodes:[], detailTarget:null,
    catalogStatus:"all", catalogCursor:null, catalogHistory:[], nextCursor:null,
  };
  const writeControls = () => document.querySelectorAll("[data-write]");
  const uuid = () => crypto.randomUUID();

  function message(value, kind="") {
    $("message").textContent = value;
    $("message").dataset.kind = kind;
  }

  function requestIds(prefix) {
    const value = uuid();
    return {
      operation_id:`${prefix}-op-${value}`,
      request_id:`${prefix}-req-${value}`,
      idempotency_key:`${prefix}-idem-${value}`,
    };
  }

  function deepFreeze(value) {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      Object.values(value).forEach(deepFreeze);
      Object.freeze(value);
    }
    return value;
  }

  function freezeEnvelope(kind, path, body, next=null, target=null) {
    return deepFreeze({kind, path, body:deepFreeze(JSON.parse(JSON.stringify(body))), next, target});
  }

  async function api(path, body) {
    const hasBody = body !== undefined;
    const headers = hasBody ? {"Content-Type":"application/json"} : {};
    if (state.token) headers.Authorization = "Bearer " + state.token;
    const response = await fetch(path, {
      method:hasBody ? "POST" : "GET",
      credentials:"same-origin",
      headers,
      body:hasBody ? JSON.stringify(body) : undefined,
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

  function canApprove() {
    const baseConfirmed = BASE_ACKNOWLEDGEMENTS.every(id => $(id).checked);
    const risksConfirmed = [...$("risk-acknowledgements").querySelectorAll("input[data-risk-code]")]
      .every(input => input.checked);
    return baseConfirmed && risksConfirmed && $("review-reason").value.trim().length > 0;
  }

  function renderControls() {
    const expert = state.role === "expert";
    const blockedByPending = state.writeBusy || state.pending !== null;
    writeControls().forEach(button => { button.disabled = blockedByPending || !expert; });
    const selected = selectedDetailTarget() !== null;
    $("approve").disabled = blockedByPending || !expert || !selected || !canApprove();
    $("reject").disabled = blockedByPending || !expert || !selected;
    $("release").disabled = blockedByPending || !expert || !selected;
    document.querySelectorAll("[data-status], #catalog button").forEach(button => {
      button.disabled = blockedByPending || state.readBusy > 0;
    });
    $("connect").disabled = blockedByPending || state.readBusy > 0;
    $("previous-page").disabled = blockedByPending || state.readBusy > 0 || state.catalogHistory.length === 0;
    $("next-page").disabled = blockedByPending || state.readBusy > 0 || state.nextCursor === null;
    $("page-status").textContent = `第 ${state.catalogHistory.length + 1} 页`;
    $("retry").hidden = state.pending === null;
    $("retry").disabled = state.writeBusy || state.pending === null;
  }

  function setRole(role) {
    const changed = state.role !== (role || "unverified");
    state.role = role || "unverified";
    if (changed) clearProfessionalDetail({render:false});
    $("role-note").textContent = state.role === "expert"
      ? "专家：可导入、核验与发布。"
      : state.role === "auditor"
        ? "审计员：可查看专业证据，所有写操作已禁用。"
        : state.role === "operator"
          ? "业务角色：仅可查看安全摘要。"
          : "尚未连接；所有写操作已禁用。";
    renderControls();
  }

  function node(text, tag="span") {
    const result = document.createElement(tag);
    result.textContent = text;
    return result;
  }

  function draw(canvas, points, x, y) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#77d8cb";
    if (!points.length) return;
    const minX = Math.min(...points.map(point => point[x]));
    const maxX = Math.max(...points.map(point => point[x]));
    const minY = Math.min(...points.map(point => point[y]));
    const maxY = Math.max(...points.map(point => point[y]));
    for (const point of points) {
      const px = 12 + (point[x] - minX) / (maxX - minX || 1) * (width - 24);
      const py = height - 12 - (point[y] - minY) / (maxY - minY || 1) * (height - 24);
      context.fillRect(px, py, 2, 2);
    }
  }

  function clearCanvas(id) {
    const canvas = $(id);
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
  }

  function clearProfessionalDetail({render=true}={}) {
    state.detailSequence += 1;
    state.row = null;
    state.versionId = null;
    state.detailTarget = null;
    state.warningCodes = [];
    $("detail").hidden = true;
    $("detail-title").textContent = "";
    ["states", "point-count", "dimensions", "risk-summary", "source-summary", "quality-summary"].forEach(id => { $(id).textContent = ""; });
    $("publication-note").replaceChildren();
    $("professional-evidence").replaceChildren();
    $("professional-evidence").hidden = true;
    $("risk-acknowledgements").replaceChildren();
    $("index-link").hidden = true;
    BASE_ACKNOWLEDGEMENTS.forEach(id => { $(id).checked = false; });
    $("review-reason").value = "";
    $("release-reason").value = "";
    ["projection-xy", "projection-xz", "projection-yz"].forEach(clearCanvas);
    if (render) renderControls();
  }

  function selectedDetailTarget() {
    const target = state.detailTarget;
    if (!target || state.modelId !== target.modelId || state.versionId !== target.versionId) return null;
    return target;
  }

  function renderRiskAcknowledgements(row) {
    const warnings = Array.isArray(row.quality?.warning_codes)
      ? row.quality.warning_codes.filter(code => typeof code === "string")
      : [];
    state.warningCodes = [...new Set(warnings)];
    $("risk-acknowledgements").replaceChildren(...state.warningCodes.map((code, index) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.riskCode = code;
      input.id = `risk-${index}`;
      input.addEventListener("input", renderControls);
      label.append(input, document.createTextNode(`确认自动风险：${code}`));
      return label;
    }));
  }

  function renderProfessionalEvidence(row) {
    const evidence = $("professional-evidence");
    evidence.replaceChildren();
    if (!row.license || !row.provenance || !row.quality) {
      evidence.hidden = true;
      return;
    }
    const line = (label, value) => {
      const item = document.createElement("p");
      item.append(node(`${label}：${value}`));
      return item;
    };
    const quality = row.quality;
    const rejected = Array.isArray(quality.rejection_codes) ? quality.rejection_codes.join("、") : "无";
    const metrics = quality.metrics && typeof quality.metrics === "object" ? quality.metrics : {};
    evidence.append(
      node("冻结专业证据", "h4"),
      line("许可", row.license),
      line("来源与预处理", JSON.stringify(row.provenance)),
      line("质量拒绝码", rejected),
      line("质量关键指标", JSON.stringify(metrics)),
    );
    if (row.review) {
      evidence.append(
        line("核验决定", row.review.decision),
        line("核验原因", row.review.reason),
        line("核验确认项", Array.isArray(row.review.acknowledgements) ? row.review.acknowledgements.join("、") : ""),
      );
    } else {
      evidence.append(line("核验状态", "尚未核验"));
    }
    evidence.hidden = false;
  }

  function renderDetail(row, modelId) {
    state.row = row;
    state.versionId = row.version_id;
    state.detailTarget = deepFreeze({modelId, versionId:row.version_id});
    $("detail").hidden = false;
    $("detail-title").textContent = `${row.display_name} · ${row.version_id}`;
    $("states").textContent = `检查：${row.quality_status}；核验：${row.review_status}；发布：${row.publication_status}；检索：${row.index_status}`;
    $("risk-summary").textContent = `风险：${(row.risk_summary || []).join("、") || "无"}`;
    $("source-summary").textContent = row.source ? `来源：${row.source.format}；${row.source.path}` : "来源：业务安全摘要";
    $("quality-summary").textContent = row.quality ? `自动质量：${row.quality.status}` : "自动质量：业务安全摘要";
    renderProfessionalEvidence(row);
    if (row.preview) {
      $("point-count").textContent = String(row.preview.source_point_count);
      $("dimensions").textContent = row.dimensions_m.map(value => `${Number(value).toFixed(6)} m`).join(" × ");
      draw($("projection-xy"), row.preview.points, 0, 1);
      draw($("projection-xz"), row.preview.points, 0, 2);
      draw($("projection-yz"), row.preview.points, 1, 2);
    } else {
      $("point-count").textContent = "点数：业务摘要未提供";
      $("dimensions").textContent = "尺寸：业务摘要未提供";
      ["projection-xy", "projection-xz", "projection-yz"].forEach(clearCanvas);
    }
    BASE_ACKNOWLEDGEMENTS.forEach(id => { $(id).checked = false; });
    $("review-reason").value = "";
    renderRiskAcknowledgements(row);
    const history = (row.release_history || [])
      .map(release => `${release.release_id} · ${release.version_id} · ${release.action} · ${release.created_at}`)
      .join("\n");
    const publicationNodes = [];
    if (row.index_status === "update_required" && row.publication_status === "current") {
      publicationNodes.push(node("模板已发布，索引需要更新", "span"));
    }
    publicationNodes.push(node(history || "发布历史以服务端当前状态为准。", "span"));
    $("publication-note").replaceChildren(...publicationNodes);
    $("index-link").hidden = !(row.index_status === "update_required" && row.publication_status === "current");
    renderControls();
  }

  function resetCatalogHistory({clearCatalog=false}={}) {
    state.catalogCursor = null;
    state.catalogHistory = [];
    state.nextCursor = null;
    if (clearCatalog) $("catalog").replaceChildren();
    renderControls();
  }

  async function loadCatalog(status=state.catalogStatus, {duringWrite=false, cursor=state.catalogCursor, history=state.catalogHistory}={}) {
    if (!state.modelId) {
      message("请输入模型 ID 后创建并导入，或选择已有模型。");
      return;
    }
    if (!duringWrite && (state.writeBusy || state.pending)) return;
    const sequence = ++state.catalogSequence;
    const modelId = state.modelId;
    state.readBusy += 1;
    renderControls();
    try {
      const query = new URLSearchParams({status});
      if (cursor !== null) query.set("cursor", cursor);
      const data = await api(`/model-library/models/${encodeURIComponent(modelId)}/scanned-versions?${query.toString()}`);
      if (sequence !== state.catalogSequence || modelId !== state.modelId) return;
      setRole(data.viewer_role);
      state.catalogStatus = status;
      state.catalogCursor = cursor;
      state.catalogHistory = history;
      state.nextCursor = data.next_cursor || null;
      const buttons = data.items.map(row => {
        const button = document.createElement("button");
        button.append(
          node(`${row.display_name} · ${row.version_id}`, "strong"),
          node(`${row.quality_status} / ${row.review_status} / ${row.publication_status}`, "small"),
        );
        button.querySelector("small").className = "catalog-meta";
        button.addEventListener("click", () => loadDetail(row.version_id));
        return button;
      });
      $("catalog").replaceChildren(...buttons);
      if (!data.items.length) $("catalog").replaceChildren(node("暂无符合筛选的扫描版本。"));
    } catch (error) {
      if (sequence === state.catalogSequence) {
        message(error.status === 409 ? "数据已变化，请刷新后重新操作。" : `读取失败：${error.message}`, "error");
      }
    } finally {
      state.readBusy -= 1;
      renderControls();
    }
  }

  async function loadDetail(versionId, {duringWrite=false}={}) {
    if (!duringWrite && (state.writeBusy || state.pending)) return;
    const sequence = ++state.detailSequence;
    const modelId = state.modelId;
    state.readBusy += 1;
    renderControls();
    try {
      const row = await api(`/model-library/models/${encodeURIComponent(modelId)}/scanned-versions/${encodeURIComponent(versionId)}`);
      if (sequence !== state.detailSequence || modelId !== state.modelId) return;
      setRole(row.viewer_role);
      renderDetail(row, modelId);
    } catch (error) {
      if (sequence === state.detailSequence) message(`详情读取失败：${error.message}`, "error");
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
    let completedTarget = null;
    try {
      while (state.pending) {
        const current = state.pending;
        await api(current.path, current.body);
        completedTarget = current.target;
        state.pending = current.next;
      }
      message("操作已保存。", "ok");
      if (completedTarget && completedTarget.modelId === state.modelId && completedTarget.versionId) {
        await loadDetail(completedTarget.versionId, {duringWrite:true});
      }
      await loadCatalog("all", {duringWrite:true});
    } catch (error) {
      const recoverable = !error.status || error.status >= 500 || [
        "operation_busy", "publication_recovery_required", "operation_persistence_failed",
      ].includes(error.code);
      if (!recoverable) state.pending = null;
      message(
        recoverable ? "请求结果未确认；请重试原操作。" : "数据已变化，请刷新后重新操作。",
        "error",
      );
    } finally {
      state.writeBusy = false;
      renderControls();
    }
  }

  function startEnvelope(envelope) {
    if (state.writeBusy || state.pending) {
      message("上次提交结果尚未确认，请先重试原操作。", "error");
      return;
    }
    state.pending = envelope;
    renderControls();
    runPending();
  }

  $("create-import").addEventListener("click", () => {
    if (state.writeBusy || state.pending) return;
    try {
      const provenance = JSON.parse($("provenance").value);
      if (!provenance || typeof provenance !== "object" || Array.isArray(provenance)) throw new Error();
      const modelId = $("model-id").value.trim();
      const versionId = $("version-id").value.trim();
      state.modelId = modelId;
      state.versionId = versionId;
      const target = deepFreeze({modelId, versionId});
      const importBody = {
        version_id:versionId,
        staged_source:$("staged-source").value,
        declared_unit:$("declared-unit").value,
        license:$("license").value,
        provenance,
        supersedes_version_id:$("supersedes-version-id").value.trim() || null,
        ...requestIds("import"),
      };
      const importEnvelope = freezeEnvelope(
        "import",
        `/model-library/models/${encodeURIComponent(modelId)}/scanned-versions`,
        importBody,
        null,
        target,
      );
      if (!$("create-new-asset").checked) {
        startEnvelope(importEnvelope);
        return;
      }
      const assetBody = {
        model_id:modelId,
        display_name:$("display-name").value,
        category_id:$("category-id").value,
        manufacturer:$("manufacturer").value,
        model_number:$("model-number").value,
        keywords:$("keywords").value.split(",").map(value => value.trim()).filter(Boolean),
        tags:$("tags").value.split(",").map(value => value.trim()).filter(Boolean),
        source_family:"scanned_reference",
        ...requestIds("asset"),
      };
      startEnvelope(freezeEnvelope("asset", "/model-library/models", assetBody, importEnvelope, target));
    } catch (_) {
      message("导入准备失败：provenance 必须是 JSON 对象", "error");
    }
  });

  $("approve").addEventListener("click", () => {
    const target = selectedDetailTarget();
    if (!target) return;
    startEnvelope(freezeEnvelope("review", `/model-library/models/${encodeURIComponent(target.modelId)}/scanned-versions/${encodeURIComponent(target.versionId)}/review`, {
      decision:"approved", reason:$("review-reason").value.trim(),
      acknowledgements:[...BASE_ACKNOWLEDGEMENTS, ...state.warningCodes], ...requestIds("review"),
    }, null, target));
  });

  $("reject").addEventListener("click", () => {
    const target = selectedDetailTarget();
    if (!target) return;
    startEnvelope(freezeEnvelope("review", `/model-library/models/${encodeURIComponent(target.modelId)}/scanned-versions/${encodeURIComponent(target.versionId)}/review`, {
      decision:"rejected", reason:$("review-reason").value.trim(), acknowledgements:[], ...requestIds("review"),
    }, null, target));
  });

  $("release").addEventListener("click", () => {
    const target = selectedDetailTarget();
    if (!target) return;
    startEnvelope(freezeEnvelope("release", `/model-library/models/${encodeURIComponent(target.modelId)}/releases`, {
      version_id:target.versionId, release_id:`release-${uuid()}`, action:"activate",
      expected_current_release_id:state.row.current_release_id || null, rollback_of_release_id:null,
      reason:$("release-reason").value.trim(), ...requestIds("release"),
    }, null, target));
  });

  document.querySelectorAll("[data-status]").forEach(button => {
    button.addEventListener("click", () => {
      resetCatalogHistory();
      loadCatalog(button.dataset.status, {cursor:null, history:[]});
    });
  });
  $("next-page").addEventListener("click", () => {
    if (state.nextCursor === null) return;
    loadCatalog(state.catalogStatus, {
      cursor:state.nextCursor,
      history:[...state.catalogHistory, state.catalogCursor],
    });
  });
  $("previous-page").addEventListener("click", () => {
    if (!state.catalogHistory.length) return;
    const history = state.catalogHistory.slice(0, -1);
    loadCatalog(state.catalogStatus, {cursor:state.catalogHistory.at(-1), history});
  });
  $("retry").addEventListener("click", runPending);
  [...BASE_ACKNOWLEDGEMENTS, "review-reason"].forEach(id => $(id).addEventListener("input", renderControls));
  $("model-id").addEventListener("input", () => {
    const nextModelId = $("model-id").value.trim();
    if (nextModelId !== state.modelId) {
      state.catalogSequence += 1;
      clearProfessionalDetail({render:false});
      resetCatalogHistory({clearCatalog:true});
    }
    state.modelId = nextModelId;
    renderControls();
  });
  $("connect").addEventListener("click", async () => {
    if (state.writeBusy || state.pending || state.readBusy) return;
    state.catalogSequence += 1;
    clearProfessionalDetail({render:false});
    resetCatalogHistory({clearCatalog:true});
    state.token = $("token").value.trim();
    $("token").value = "";
    state.readBusy += 1;
    renderControls();
    try {
      const session = await api("/model-library/reference-session");
      setRole(session.viewer_role);
      message("已连接。", "ok");
      if (state.modelId) await loadCatalog(state.catalogStatus, {cursor:null, history:[]});
    } catch (error) {
      state.token = "";
      clearProfessionalDetail({render:false});
      setRole("unverified");
      message(`连接失败：${error.message}`, "error");
    } finally {
      state.readBusy -= 1;
      renderControls();
    }
  });

  setRole("unverified");
  const requestedModel = new URLSearchParams(location.search).get("model");
  if (requestedModel && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(requestedModel)) {
    state.modelId = requestedModel;
    $("model-id").value = requestedModel;
  }
})();
