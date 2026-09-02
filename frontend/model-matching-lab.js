"use strict";
(function () {
  const shared = window.modelMatchingWorkbench;
  let selectedCase = null, configLoading = false, configLoaded = false;
  const json = value => JSON.stringify(value, null, 2);
  function renderProfessional({item,candidate,role,api,$}) {
    $("role-status").textContent = role === "expert" ? "专家操作" : role === "auditor" ? "审计员 · 只读" : "业务角色 · 技术详情不可用";
    $("technical").hidden = !item.technical;
    $("scope").querySelector('[value="expert_pose"]').disabled = role !== "expert";
    if (!item.technical) return;
    const report = item.technical.registrations.find(r => r.registration_id === candidate?.registration_id);
    $("engine").textContent = report ? `${report.engine} ${report.engine_version} · 配置 ${report.config_id}${report.engine_production ? "" : " · 非生产引擎"}` : "无配准报告";
    $("matrix").replaceChildren();
    if (shared.validMatrix(report?.rigid_transform_4x4)) for (const row of report.rigid_transform_4x4) {
      const tr = document.createElement("tr"); for (const value of row) { const td = document.createElement("td"); td.textContent = value.toFixed(6); tr.append(td); } $("matrix").append(tr);
    }
    const metrics = report?.residual_metrics || {};
    $("metrics").replaceChildren();
    for (const [key,label,unit] of [["observed_to_model_coverage","观测 → 模型覆盖率","%"],["model_to_observed_coverage","模型 → 观测覆盖率","%"],
      ["inlier_rmse_m","内点均方根残差","m"],["chamfer_distance_m","双向平均距离","m"],["p95_distance_m","95% 分位距离","m"],["maximum_dimension_relative_error","最大尺寸相对误差","%"]]) {
      const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = label;
      dd.textContent = Number.isFinite(metrics[key]) ? `${(metrics[key]*(unit === "%" ? 100 : 1)).toFixed(4)} ${unit}` : "不可用";
      $("metrics").append(dt,dd);
    }
    $("gate-reasons").textContent = "门禁原因：" + (report?.gate_reasons?.join("；") || "无额外原因");
    $("decision-history").textContent = json(item.technical.decisions);
    $("audit").textContent = json(item.technical.audit);
    $("binding-history").replaceChildren(...item.technical.binding_history.map(binding => {
      const li = document.createElement("li"); li.textContent = `${binding.binding_id} · ${binding.model_id} / ${binding.model_version_id} · ${binding.status} · ${binding.created_at}`; return li;
    }));
    const previous = $("restore-target").value;
    $("restore-target").replaceChildren(...item.technical.binding_history.filter(binding =>
      binding.binding_id !== item.binding_summary?.binding_id && binding.object_fingerprint === item.object.object_fingerprint).map(binding => {
        const option = document.createElement("option"); option.value = binding.binding_id; option.textContent = `${binding.binding_id} · ${binding.model_version_id}`; return option;
      }));
    if ([...$("restore-target").options].some(option => option.value === previous)) $("restore-target").value = previous;
    if (!configLoaded && !configLoading) {
      configLoading = true;
      api("/model-matching/registration-configs").then(data => {
        $("registration-config").replaceChildren(...data.configs.map(config => { const option = document.createElement("option"); option.value = config.config_id; option.textContent = config.config_id; return option; }));
        configLoaded = true; $("config-note").textContent = data.configs.length ? "仅可使用服务端已发布配置。" : "暂无已发布配置，请先发布配置。";
      }).catch(error => { $("config-note").textContent = "配置读取失败：" + error.message; }).finally(() => { configLoading = false; });
    }
    selectedCase = item.case_id;
    const identity = [item.object.asset_id,item.object.source_id,item.object.instance_id,item.retrieval_run_id].map(encodeURIComponent).join("/");
    $("retrieval-evidence").textContent = "正在加载已验证检索依据…";
    api("/model-matching/retrievals/"+identity).then(data => { if (selectedCase === item.case_id) $("retrieval-evidence").textContent = json(data); })
      .catch(error => { if (selectedCase === item.case_id) $("retrieval-evidence").textContent = "检索依据读取失败："+error.message; });
  }
  async function buildProfessionalRequest({action,item,candidate,form,api,$}) {
    const identity = {asset_id:item.object.asset_id,source_id:item.object.source_id,instance_id:item.object.instance_id,retrieval_run_id:item.retrieval_run_id};
    if (action === "rerun") {
      if (!candidate || !$("registration-config").value) throw new Error("请选择候选与已发布配准配置。");
      return {path:"/model-matching/registrations",body:{...identity,registration_id:form.decision_id.replace("decision-","registration-"),
        candidate_rank:candidate.candidate_rank,config_id:$("registration-config").value,
        operation_id:form.operation_id,request_id:form.request_id,idempotency_key:form.idempotency_key}};
    }
    if (!form.decision_reason.trim()) throw new Error("请填写处理原因。");
    if (!item.binding_summary) throw new Error("当前没有可以接续的绑定。");
    const body = {...identity,decision_id:form.decision_id,binding_id:form.binding_id,decision_reason:form.decision_reason.trim(),
      verification_scope:form.verification_scope,expected_case_revision:item.case_revision,
      operation_id:form.operation_id,request_id:form.request_id,idempotency_key:form.idempotency_key};
    if (action === "supersede") {
      if (!candidate || candidate.human_rejected || candidate.gate_status === "rejected") throw new Error("请选择合格且未被人工拒绝的配准。");
      body.registration_id = candidate.registration_id; body.candidate_rank = candidate.candidate_rank;
    } else if (action === "restore") {
      const binding = item.technical.binding_history.find(b => b.binding_id === $("restore-target").value);
      if (!binding) throw new Error("请选择可恢复的历史版本。");
      body.restores_binding_id = binding.binding_id; body.retrieval_run_id = binding.retrieval_run_id;
      if (binding.case_id !== item.case_id) {
        const targetCase = await api("/model-matching/decision-items/"+encodeURIComponent(binding.case_id));
        body.expected_case_revision = targetCase.case_revision;
      }
    } else throw new Error("不支持的专业操作。");
    return {path:"/model-matching/bindings/"+encodeURIComponent(item.binding_summary.binding_id)+"/"+action,body};
  }
  shared.mount({professional:true,renderProfessional,buildProfessionalRequest});
})();
