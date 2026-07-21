(function exposeSegmentationCorrection(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.segmentationCorrection = api;
})(typeof globalThis === "object" ? globalThis : this, function createApi() {
  function buildCorrectionViewModel(session, queue, objects) {
    return {
      status: session?.status || "unknown",
      revision: Number(session?.revision) || 0,
      changedPointCount:
        Number(session?.correction_diff?.changed_point_count) || 0,
      suggestions: (queue?.items || []).map((item) => ({ ...item })),
      objects: (objects?.objects || []).map((item) => ({ ...item })),
      undoAvailable: Boolean(session?.undo_available),
      redoAvailable: Boolean(session?.redo_available),
    };
  }

  function projectPoint(point, camera, viewport) {
    const view = camera?.view || "top";
    const axes =
      view === "front" ? ["x", "z"] : view === "side" ? ["y", "z"] : ["x", "y"];
    const zoom = Number(camera?.zoom) || 1;
    return {
      source_point_index: point.source_point_index,
      instance_id: point.draft?.instance_id || point.instance_id,
      class_id: point.draft?.class_id || point.class_id,
      is_noise: Boolean(point.draft?.is_noise ?? point.is_noise),
      screenX:
        viewport.width / 2 + (Number(point[axes[0]]) + (camera?.panX || 0)) * zoom,
      screenY:
        viewport.height / 2 + (Number(point[axes[1]]) + (camera?.panY || 0)) * zoom,
    };
  }

  function insidePolygon(x, y, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const [xi, yi] = polygon[i];
      const [xj, yj] = polygon[j];
      const crosses =
        yi > y !== yj > y &&
        x <= ((xj - xi) * (y - yi)) / ((yj - yi) || Number.EPSILON) + xi;
      if (crosses) inside = !inside;
    }
    return inside;
  }

  function pickIndices(projectedPoints, polygon) {
    if (!Array.isArray(polygon) || polygon.length < 3) return [];
    return projectedPoints
      .filter((point) => insidePolygon(point.screenX, point.screenY, polygon))
      .map((point) => point.source_point_index)
      .sort((a, b) => a - b);
  }

  function buildOperation(action, sourcePointIndices, context) {
    const indices = [...new Set(sourcePointIndices)].sort((a, b) => a - b);
    const instanceIds = [...new Set(context?.instanceIds || [])].sort();
    if (action === "confirm") return { type: "confirm", instance_ids: instanceIds };
    if (action === "merge") {
      return {
        type: "merge",
        instance_ids: instanceIds,
        target_instance_id: context.targetInstanceId || instanceIds[0],
      };
    }
    if (action === "split") {
      return {
        type: "split",
        instance_id: context.instanceId,
        source_point_indices: indices,
      };
    }
    if (action === "relabel") {
      return { type: "relabel", instance_ids: instanceIds, class_id: context.classId };
    }
    if (action === "noise") return { type: "mark_noise", source_point_indices: indices };
    if (action === "restore-noise") {
      return {
        type: "restore_from_noise",
        source_point_indices: indices,
        target_instance_id: context.instanceId,
      };
    }
    if (action === "restore") return { type: "restore", scope: "all" };
    if (action === "undo" || action === "redo") return { type: action };
    throw new Error(`Unsupported correction action: ${action}`);
  }

  return {
    buildCorrectionViewModel,
    projectPoint,
    pickIndices,
    buildOperation,
  };
});
