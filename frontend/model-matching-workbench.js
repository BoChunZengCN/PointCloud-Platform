(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.modelMatchingWorkbench = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const labels = {pending:"待处理", processed:"已处理", stale:"已陈旧"};
  const gateLabels = {passed:"通过", review_required:"待专家复核", rejected:"算法拒绝"};
  function statusLabel(status) { return labels[status] || "未知状态"; }
  function validMatrix(value) {
    return Array.isArray(value) && value.length === 4 && value.every(row =>
      Array.isArray(row) && row.length === 4 && row.every(number => typeof number === "number" && Number.isFinite(number)));
  }
  function availableActions(item, role) {
    const allowed = role === "expert" ? ["confirm","reject","no_match","rerun","supersede","restore"] :
      role === "operator" ? ["confirm","reject","no_match"] : [];
    return (item.available_actions || []).filter(action => allowed.includes(action));
  }
  function buildListViewModel(response, role) {
    return {items:(response.items || []).map(item => ({...item, label:statusLabel(item.status), actions:availableActions(item, role)})),
      counts:response.counts || {}, next_cursor:response.next_cursor || null};
  }
  function buildDecisionPayload(item, form) {
    const candidate = item.candidate_summary.find(value => value.registration_id === form.registration_id);
    if (form.decision !== "no_match" && !candidate) throw new Error("请选择有效候选。");
    if (!form.decision_reason.trim()) throw new Error("请填写处理原因。");
    return {case_id:item.case_id, expected_case_revision:item.case_revision, decision_id:form.decision_id,
      binding_id:form.decision === "confirmed" ? form.binding_id : null,
      registration_id:form.decision === "no_match" ? null : candidate.registration_id,
      candidate_rank:form.decision === "no_match" ? null : candidate.candidate_rank,
      decision:form.decision, decision_reason:form.decision_reason.trim(), verification_scope:form.verification_scope,
      operation_id:form.operation_id, request_id:form.request_id, idempotency_key:form.idempotency_key};
  }
  function mount(options) {
    const $ = id => document.getElementById(id);
    const state = {status:"pending",item:null,items:[],cursor:null,busy:false,conflict:false,sequence:0,pending:null,role:"operator",token:""};
    const requested = new URLSearchParams(location.search).get("api");
    let base;
    try {
      base = new URL(requested || location.origin);
      if (!["http:","https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash) throw new Error();
    } catch (_) { $("message").textContent = "API 地址无效，请使用不含凭据的 HTTP(S) 地址。"; return; }
    const apiBase = base.href.replace(/\/$/, "");
    function message(text, kind="info") { $("message").textContent = text; $("message").dataset.kind = kind; }
    async function api(path, body) {
      const headers = {};
      if (state.token) headers.Authorization = "Bearer " + state.token;
      if (body) headers["Content-Type"] = "application/json";
      const response = await fetch(apiBase + path, {method:body ? "POST" : "GET", headers,
        credentials:"same-origin", ...(body ? {body:JSON.stringify(body)} : {})});
      const data = await response.json();
      if (!response.ok) { const error = new Error(data.detail?.message || "请求失败"); error.code = data.detail?.code; error.status = response.status; throw error; }
      return data;
    }
    function reportError(error) {
      if (error.status === 409) {
        state.conflict = true;
        message(error.code === "decision_conflict" ? "记录已被其他用户处理，请刷新后重新核验。" : "数据版本或完整性已变化，请刷新；若仍失败请联系专家。", "conflict");
      } else if (error.code === "publication_recovery_required" || error.code === "operation_busy") {
        message("操作正在提交或需要恢复。请保留页面并重试原操作；不要创建新的操作编号。", "error");
      } else message(error.status === 403 ? "当前身份没有此操作权限，请使用有权限的账户。" : "请求失败：" + error.message, "error");
      renderActions();
    }
    function ids() { const value = crypto.randomUUID(); return {decision_id:"decision-"+value,binding_id:"binding-"+value,
      operation_id:"op-"+value,request_id:"req-"+value,idempotency_key:"idem-"+value}; }
    function selectedCandidate() { return state.item?.candidate_summary.find(c => c.registration_id === $("candidate").value); }
    function renderActions() {
      document.querySelectorAll("[data-status], #apply-filters, #more, #refresh, #connect, .decision-row").forEach(button => {
        button.disabled = state.busy;
      });
      const actions = state.item ? availableActions(state.item, state.role) : [];
      const candidate = selectedCandidate();
      document.querySelectorAll("[data-action]").forEach(button => {
        const action = button.dataset.action;
        const candidateAllows = !["confirm","reject"].includes(action) || candidate?.available_actions?.includes(action);
        button.disabled = state.busy || state.conflict || !actions.includes(action) || !candidateAllows;
      });
    }
    function renderDetail() {
      const item = state.item;
      if (!item) { $("detail").hidden = true; return; }
      $("detail").hidden = false;
      state.role = options.professional ? (item.viewer_role || "auditor") : (item.viewer_role === "auditor" ? "auditor" : "operator");
      $("object-title").textContent = item.object.instance_id;
      $("object-class").textContent = item.object.class_id || "未分类";
      $("case-status").textContent = statusLabel(item.status);
      $("binding-id").textContent = item.binding_summary?.binding_id || "暂无绑定";
      $("decision-summary").textContent = item.decision_summary ? `最近处理：${item.decision_summary.decided_by} · ${item.decision_summary.decided_at} · ${item.decision_summary.decision_reason}` : "尚未人工处理";
      const previous = $("candidate").value;
      $("candidate").replaceChildren(...item.candidate_summary.map(c => {
        const option = document.createElement("option"); option.value = c.registration_id;
        option.textContent = `#${c.candidate_rank} ${c.model_id} / ${c.model_version_id} · ${gateLabels[c.gate_status]}${c.human_rejected ? " · 人工已拒绝" : ""}`;
        return option;
      }));
      if (item.candidate_summary.some(c => c.registration_id === previous)) $("candidate").value = previous;
      renderCandidate();
    }
    function renderCandidate() {
      const c = selectedCandidate();
      $("candidate-summary").textContent = c ? `配准记录：${c.registration_id} · ${c.generated_at}` : "没有可用候选";
      $("gate-note").textContent = state.item.status === "stale" ? "对象或绑定已陈旧，普通确认已禁用，请专家基于当前对象重新配准或替换。" :
        c?.gate_status === "review_required" ? "自动配准需要专家复核，普通用户不能确认。" :
        c?.gate_status === "rejected" ? "算法门禁未通过，此配准不能绑定；可声明无匹配或请专家重新配准。" :
        c?.human_rejected ? "此候选已被人工拒绝；事项仍待处理，可选择其他候选或声明无匹配。" : "通过自动门禁不等于已核验身份，请确认对应实物及使用范围。";
      if (options.renderProfessional) options.renderProfessional({item:state.item,candidate:c,role:state.role,api,$});
      renderActions();
    }
    function renderList() {
      $("decision-list").replaceChildren(...state.items.map(item => {
        const button = document.createElement("button"); button.className = "decision-row"; button.dataset.testid = "decision-row";
        const title = document.createElement("strong"); title.textContent = `${item.object.instance_id} · ${statusLabel(item.status)}`;
        const summary = document.createElement("small"); summary.textContent = `${item.object.asset_id} · ${item.object.class_id || "未分类"} · ${item.candidate_summary.length} 条配准`;
        button.append(title,summary); button.addEventListener("click", () => loadDetail(item.case_id)); return button;
      }));
      $("more").hidden = !state.cursor;
    }
    async function loadDetail(caseId) {
      if (state.busy) return;
      if (state.pending && state.item?.case_id !== caseId) { message("上次提交结果尚未确认，请先重试原操作。", "error"); return; }
      const sequence = ++state.sequence;
      state.busy = true; state.conflict = false; renderActions(); message("正在加载事项…");
      try {
        const item = await api("/model-matching/decision-items/"+encodeURIComponent(caseId));
        if (sequence !== state.sequence) return;
        state.item = item;
        if (state.pending?.request.body.decision_id === item.decision_summary?.decision_id) state.pending = null;
        renderDetail(); message("已加载最新修订。");
      } catch (error) { if (sequence === state.sequence) reportError(error); }
      finally { if (sequence === state.sequence) { state.busy = false; renderActions(); } }
    }
    async function loadList(append=false) {
      state.busy = true; renderActions(); message("正在加载清单…");
      try {
        const query = new URLSearchParams({status:state.status,limit:"50"});
        for (const [id,key] of [["asset-filter","asset_id"],["class-filter","class_id"],["gate-filter","gate_status"]]) if ($(id).value) query.set(key,$(id).value.trim());
        for (const [id,key] of [["start-filter","started_at"],["end-filter","ended_at"]]) if ($(id).value) query.set(key,new Date($(id).value).toISOString());
        if (append && state.cursor) query.set("cursor",state.cursor);
        const data = await api("/model-matching/decision-items?"+query);
        state.items = append ? state.items.concat(data.items) : data.items; state.cursor = data.next_cursor;
        document.querySelectorAll("[data-count]").forEach(node => { node.textContent = data.counts[node.dataset.count] || 0; });
        renderList(); message(state.items.length ? `已加载 ${state.items.length} 条事项。` : "暂无符合条件的事项。");
      } catch (error) { reportError(error); }
      finally { state.busy = false; renderActions(); }
    }
    async function submit(action) {
      if (state.busy || state.conflict || !state.item) return;
      try {
        const form = {decision:{confirm:"confirmed",reject:"rejected",no_match:"no_match"}[action],
          decision_reason:$("reason").value,verification_scope:$("scope").value,registration_id:$("candidate").value,...ids()};
        let request;
        if (form.decision) request = {path:"/model-matching/decisions",body:buildDecisionPayload(state.item,form)};
        else if (options.buildProfessionalRequest) request = await options.buildProfessionalRequest({action,item:state.item,candidate:selectedCandidate(),form,api,$});
        else return;
        const signature = JSON.stringify({action,caseId:state.item.case_id,
          reason:form.decision_reason,scope:form.verification_scope,candidate:form.registration_id,
          target:$("restore-target")?.value,config:$("registration-config")?.value});
        if (state.pending && state.pending.signature !== signature) throw new Error("上次提交结果尚未确认，请刷新核对后再改变操作内容。");
        if (!state.pending) state.pending = {signature,request};
        state.busy = true; renderActions(); message("正在提交，请勿关闭页面…");
        const result = await api(state.pending.request.path,state.pending.request.body);
        state.pending = null;
        const caseId = result.decision?.case_id || state.item.case_id;
        await loadList(); await loadDetail(caseId); message("操作已保存，已加载最新决定与绑定。");
      } catch (error) {
        if (error.status && error.status < 500) state.pending = null;
        reportError(error);
      } finally { state.busy = false; renderActions(); }
    }
    document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click",() => submit(button.dataset.action)));
    document.querySelectorAll("[data-status]").forEach(button => button.addEventListener("click",() => {
      if (state.busy) return; state.status = button.dataset.status;
      document.querySelectorAll("[data-status]").forEach(node => node.setAttribute("aria-pressed",String(node === button))); loadList();
    }));
    $("candidate").addEventListener("change",renderCandidate);
    $("apply-filters").addEventListener("click",() => { if (!state.busy) loadList(); });
    $("more").addEventListener("click",() => { if (!state.busy) loadList(true); });
    $("refresh").addEventListener("click",async () => { if (state.busy) return; const caseId = state.item?.case_id; await loadList(); if (caseId) await loadDetail(caseId); });
    $("connect").addEventListener("click",() => {
      if (state.busy) return;
      if (state.pending) { message("上次提交结果尚未确认，请先重试原操作再切换身份。", "error"); return; }
      state.token = $("token").value.trim(); $("token").value = ""; state.item = null; renderDetail(); loadList();
    });
    loadList();
  }
  return {statusLabel,validMatrix,availableActions,buildListViewModel,buildDecisionPayload,mount};
});
