"use strict";
(function () {
  const $ = id => document.getElementById(id);
  const state = {modelId:null, versionId:null, row:null, role:"operator", busy:false, pending:null};
  const write = () => document.querySelectorAll("[data-write]");
  const uuid = () => crypto.randomUUID();
  function message(value, kind="") { $("message").textContent = value; $("message").dataset.kind = kind; }
  function requestIds(prefix) { const value = uuid(); return {operation_id:`${prefix}-op-${value}`,request_id:`${prefix}-req-${value}`,idempotency_key:`${prefix}-idem-${value}`}; }
  async function api(path, body) {
    const response = await fetch(path, {method:body ? "POST" : "GET", credentials:"same-origin", headers:body ? {"Content-Type":"application/json"} : {}, body:body ? JSON.stringify(body) : undefined});
    let data; try { data = await response.json(); } catch (_) { throw new Error("服务未返回 JSON 响应"); }
    if (!response.ok) { const error = new Error(data.detail?.message || "请求失败"); error.status = response.status; throw error; }
    return data;
  }
  function setBusy(value) { state.busy = value; renderWrite(); }
  function renderWrite() {
    const expert = state.role === "expert";
    write().forEach(button => { button.disabled = state.busy || (!expert && button.id !== "create-import"); });
    $("create-import").disabled = state.busy || (state.role === "auditor") || (state.role === "operator" && !$("model-id").value.trim());
    $("approve").disabled = state.busy || !expert || !canApprove();
  }
  function canApprove() { return ["single_object","metadata_and_rights","coverage_limitations"].every(id => $(id).checked) && $("review-reason").value.trim().length > 0; }
  function setRole(role) { state.role = role || "operator"; $("role-note").textContent = state.role === "expert" ? "专家：可导入、核验与发布。" : state.role === "auditor" ? "审计员：可查看专业证据，所有写操作已禁用。" : "业务角色：仅可查看安全摘要。"; renderWrite(); }
  function node(text, tag="span") { const result = document.createElement(tag); result.textContent = text; return result; }
  function draw(canvas, points, x, y) {
    const context = canvas.getContext("2d"), width = canvas.width, height = canvas.height;
    context.clearRect(0,0,width,height); context.fillStyle = "#77d8cb";
    const values = points.flatMap(point => [point[x],point[y]]); const minX=Math.min(...points.map(p=>p[x])), maxX=Math.max(...points.map(p=>p[x])); const minY=Math.min(...points.map(p=>p[y])), maxY=Math.max(...points.map(p=>p[y]));
    if (!values.length) return;
    for (const point of points) { const px=12+(point[x]-minX)/(maxX-minX||1)*(width-24), py=height-12-(point[y]-minY)/(maxY-minY||1)*(height-24); context.fillRect(px,py,2,2); }
  }
  function renderDetail(row) {
    state.row = row; state.versionId = row.version_id; $("detail").hidden = false;
    $("detail-title").textContent = `${row.display_name} · ${row.version_id}`;
    $("states").textContent = `检查：${row.quality_status}；核验：${row.review_status}；发布：${row.publication_status}；检索：${row.index_status}`;
    $("risk-summary").textContent = `风险：${(row.risk_summary || []).join("、") || "无"}`;
    $("source-summary").textContent = row.source ? `来源：${row.source.format}；${row.source.path}` : "来源：业务安全摘要";
    $("quality-summary").textContent = row.quality ? `自动质量：${row.quality.status}` : "自动质量：业务安全摘要";
    if (row.preview) { $("point-count").textContent = String(row.preview.source_point_count); $("dimensions").textContent = row.dimensions_m.map(value => `${Number(value).toFixed(6)} m`).join(" × "); draw($("projection-xy"),row.preview.points,0,1); draw($("projection-xz"),row.preview.points,0,2); draw($("projection-yz"),row.preview.points,1,2); }
    else { $("point-count").textContent = "点数：业务摘要未提供"; $("dimensions").textContent = "尺寸：业务摘要未提供"; }
    $("publication-note").textContent = row.index_status === "update_required" && row.publication_status === "current" ? "模板已发布，索引需要更新" : "发布历史以服务端当前状态为准。";
    $("index-link").hidden = !(row.index_status === "update_required" && row.publication_status === "current"); renderWrite();
  }
  async function loadCatalog(status="all") {
    if (!state.modelId) { message("请输入模型 ID 后创建并导入，或选择已有模型。", ""); return; }
    setBusy(true); try { const data = await api(`/model-library/models/${encodeURIComponent(state.modelId)}/scanned-versions?status=${encodeURIComponent(status)}`); setRole(data.viewer_role); $("catalog").replaceChildren(...data.items.map(row => { const button=document.createElement("button"); button.append(node(`${row.display_name} · ${row.version_id}`,"strong"),node(`${row.quality_status} / ${row.review_status} / ${row.publication_status}`,"small")); button.querySelector("small").className="catalog-meta"; button.addEventListener("click",()=>loadDetail(row.version_id)); return button; })); if (!data.items.length) $("catalog").replaceChildren(node("暂无符合筛选的扫描版本。")); }
    catch (error) { message(error.status === 409 ? "数据已变化，请刷新后重新操作。" : `读取失败：${error.message}`,"error"); } finally { setBusy(false); }
  }
  async function loadDetail(versionId) { setBusy(true); try { const row=await api(`/model-library/models/${encodeURIComponent(state.modelId)}/scanned-versions/${encodeURIComponent(versionId)}`); setRole(row.viewer_role); renderDetail(row); } catch(error) { message(`详情读取失败：${error.message}`,"error"); } finally { setBusy(false); } }
  async function submit(kind, path, body) { if (state.busy) return; state.pending ||= {kind,path,body}; $("retry").hidden=true; setBusy(true); message("正在提交，请勿重复操作…"); try { await api(state.pending.path,state.pending.body); state.pending=null; message("操作已保存。","ok"); await loadDetail(state.versionId); await loadCatalog(); } catch(error) { if (error.status && error.status < 500) state.pending=null; if (state.pending) $("retry").hidden=false; message(error.status === 409 ? "数据已变化，请刷新后重新操作。" : "请求结果未确认；请重试原操作。","error"); } finally { setBusy(false); } }
  $("create-import").addEventListener("click", async () => { const provenance = (()=>{ try{return JSON.parse($("provenance").value);}catch(_){throw new Error("provenance 必须是 JSON 对象");} })(); try { state.modelId=$("model-id").value.trim(); const asset={model_id:state.modelId,display_name:$("display-name").value,category_id:$("category-id").value,manufacturer:$("manufacturer").value,model_number:$("model-number").value,keywords:$("keywords").value.split(",").filter(Boolean),tags:$("tags").value.split(",").filter(Boolean),source_family:"scanned_reference",...requestIds("asset")}; setBusy(true); await api("/model-library/models",asset); const body={version_id:$("version-id").value,staged_source:$("staged-source").value,declared_unit:$("declared-unit").value,license:$("license").value,provenance,supersedes_version_id:$("supersedes-version-id").value.trim()||null,...requestIds("import")}; state.versionId=body.version_id; setBusy(false); await submit("import",`/model-library/models/${encodeURIComponent(state.modelId)}/scanned-versions`,body); } catch(error) { setBusy(false); message(`导入准备失败：${error.message}`,"error"); } });
  $("approve").addEventListener("click",()=>submit("review",`/model-library/models/${encodeURIComponent(state.modelId)}/scanned-versions/${encodeURIComponent(state.versionId)}/review`,{decision:"approved",reason:$("review-reason").value.trim(),acknowledgements:["single_object","metadata_and_rights","coverage_limitations"],...requestIds("review")}));
  $("reject").addEventListener("click",()=>submit("review",`/model-library/models/${encodeURIComponent(state.modelId)}/scanned-versions/${encodeURIComponent(state.versionId)}/review`,{decision:"rejected",reason:$("review-reason").value.trim(),acknowledgements:[],...requestIds("review")}));
  $("release").addEventListener("click",()=>submit("release",`/model-library/models/${encodeURIComponent(state.modelId)}/releases`,{version_id:state.versionId,release_id:`release-${uuid()}`,action:"activate",expected_current_release_id:state.row?.publication_status === "current" ? state.row.release_id : null,rollback_of_release_id:null,reason:$("release-reason").value.trim(),...requestIds("release")}));
  document.querySelectorAll("[data-status]").forEach(button=>button.addEventListener("click",()=>loadCatalog(button.dataset.status)));
  $("retry").addEventListener("click",()=>{ if (state.pending) submit(state.pending.kind,state.pending.path,state.pending.body); });
  ["single_object","metadata_and_rights","coverage_limitations","review-reason"].forEach(id=>$(id).addEventListener("input",renderWrite));
  $("model-id").addEventListener("input",renderWrite);
  setRole("operator");
  const requestedModel = new URLSearchParams(location.search).get("model");
  if (requestedModel && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(requestedModel)) { state.modelId = requestedModel; $("model-id").value = requestedModel; loadCatalog(); }
})();
