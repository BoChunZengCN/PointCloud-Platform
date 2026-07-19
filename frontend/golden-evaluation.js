(function exposeGoldenEvaluationViewModel(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.buildGoldenEvaluationViewModel = api.buildGoldenEvaluationViewModel;
})(typeof globalThis === "object" ? globalThis : this, function createApi() {
  function buildGoldenEvaluationViewModel(
    evaluationPayload,
    searchPayload,
    comparisonPayload,
  ) {
    const evaluations = evaluationPayload?.evaluations || [];
    const completed = evaluations.filter(
      (item) => item.status === "completed" && item.summary,
    );
    const latest = completed.length ? completed[completed.length - 1] : null;
    if (!latest) {
      return { state: "empty" };
    }

    const searches = searchPayload?.searches || [];
    const latestSearch = searches.length ? searches[searches.length - 1] : null;
    const recommendation = latestSearch?.recommendation || null;
    const comparisonId = recommendation?.comparison_id || null;
    let gateStatus = "未执行";
    if (comparisonId) {
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

  return { buildGoldenEvaluationViewModel };
});
