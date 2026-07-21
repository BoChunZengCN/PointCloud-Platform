(function exposeGoldenEvaluationViewModel(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.buildGoldenEvaluationViewModel = api.buildGoldenEvaluationViewModel;
  root.latestByLifecycle = api.latestByLifecycle;
})(typeof globalThis === "object" ? globalThis : this, function createApi() {
  function latestByLifecycle(items, idField) {
    return (items || []).reduce((latest, item) => {
      if (!latest) {
        return item;
      }
      const itemTime = Date.parse(item.completed_at || item.started_at || "") || 0;
      const latestTime =
        Date.parse(latest.completed_at || latest.started_at || "") || 0;
      if (itemTime !== latestTime) {
        return itemTime > latestTime ? item : latest;
      }
      return String(item[idField] || "") >= String(latest[idField] || "")
        ? item
        : latest;
    }, null);
  }

  function buildGoldenEvaluationViewModel(
    evaluationPayload,
    searchPayload,
    comparisonPayload,
  ) {
    const evaluations = evaluationPayload?.evaluations || [];
    const completed = evaluations.filter(
      (item) => item.status === "completed" && item.summary,
    );
    const searches = searchPayload?.searches || [];
    const latestSearch = latestByLifecycle(searches, "search_id");
    const recommendation = latestSearch?.recommendation || null;
    const recommendedEvaluationId = recommendation?.evaluation_id || null;
    const recommendedEvaluation = recommendedEvaluationId
      ? completed.find(
        (item) => item.evaluation_id === recommendedEvaluationId,
      ) || null
      : null;
    const latest = recommendedEvaluation
      || latestByLifecycle(completed, "evaluation_id");
    if (!latest) {
      return { state: "empty" };
    }

    const comparisonId = recommendation?.comparison_id || null;
    let gateStatus = "未执行";
    if (comparisonId && recommendedEvaluationId && !recommendedEvaluation) {
      gateStatus = "评估未加载";
    } else if (comparisonId) {
      gateStatus = comparisonPayload?.gate?.status || "读取失败";
    }

    return {
      state: "ready",
      evaluationId: latest.evaluation_id,
      instanceF1: Number(latest.summary.instance_f1) || 0,
      pointMiou: Number(latest.summary.point_miou) || 0,
      meanBoxIou: Number(latest.summary.mean_box_iou) || 0,
      matchedLabelRatio: Number(latest.summary.matched_label_ratio) || 0,
      gateStatus,
      comparisonId,
      recommendationConfig: recommendation?.config || null,
      recommendationScore:
        recommendation && Number.isFinite(Number(recommendation.score))
          ? Number(recommendation.score)
          : null,
    };
  }

  return { buildGoldenEvaluationViewModel, latestByLifecycle };
});
