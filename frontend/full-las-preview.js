(() => {
  "use strict";

  const number = (value) => new Intl.NumberFormat("zh-CN").format(value);
  const status = document.getElementById("load-status");
  const budget = document.getElementById("point-budget");
  let viewer = null;
  let pointcloud = null;
  let loaded = false;

  function setStatus(state, message) {
    status.dataset.state = state;
    status.textContent = message;
  }

  function updateVisiblePoints() {
    if (!pointcloud) return;
    const visible = Number.isFinite(pointcloud.numVisiblePoints) ? pointcloud.numVisiblePoints : 0;
    document.getElementById("visible-points").textContent = number(visible);
    if (visible > 0 && !loaded) {
      loaded = true;
      setStatus("ready", "点云已显示；可旋转、平移、缩放并查看局部细节。");
    }
  }

  async function start() {
    try {
      const response = await fetch("/api/full-previews", { cache: "no-store" });
      if (!response.ok) throw new Error("无法取得已发布预览目录");
      const catalog = await response.json();
      const requested = new URLSearchParams(window.location.search).get("asset_id");
      const selected = requested
        ? catalog.previews?.find((item) => item.asset_id === requested)
        : catalog.previews?.[0];
      if (!selected) throw new Error("没有找到该资产已发布的完整预览，请先运行准备命令");
      const metadataUrl = selected.metadata_url;
      const expectedUrl = `/previews/${selected.asset_id}/runs/${selected.run_id}/metadata.json`;
      if (metadataUrl !== expectedUrl || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(selected.run_id)) {
        throw new Error("预览版本 URL 无效");
      }
      // 活动指针只在入口解析一次。之后的 metadata/hierarchy/octree 均锁定这一 run-id。
      document.getElementById("asset-name").textContent = selected.asset_id;
      document.getElementById("run-id").textContent = selected.run_id;
      document.getElementById("source-points").textContent = number(selected.source_point_count);
      document.getElementById("source-unit").textContent = selected.source_unit || "m";
      document.getElementById("rgb-state").textContent = selected.rgb_available ? "已记录" : "不可用";
      setStatus("loading", "正在读取分层元数据…");
      const metadataResponse = await fetch(metadataUrl, { cache: "no-store" });
      if (!metadataResponse.ok) throw new Error(`元数据请求失败：HTTP ${metadataResponse.status}`);
      const metadata = await metadataResponse.json();
      if (metadata.version !== "2.0" || metadata.points !== selected.source_point_count ||
          !metadata.attributes?.some((item) => item.name === "rgb" || item.name === "rgba")) {
        throw new Error("元数据与已发布来源的点数或 RGB 不一致");
      }
      if (!window.Potree) throw new Error("Potree 查看器资源未加载");
      viewer = new Potree.Viewer(document.getElementById("potree_render_area"));
      viewer.setEDLEnabled(false);
      viewer.setFOV(60);
      viewer.setPointBudget(Number(budget.value));
      setStatus("loading", "正在按需加载点云层级与 RGB 数据…");
      const initialTimeout = window.setTimeout(() => {
        if (!pointcloud) setStatus("error", "加载失败：查看器未能打开该版本，请检查服务和浏览器控制台。");
      }, 30000);
      Potree.loadPointCloud(metadataUrl, selected.asset_id, (event) => {
        window.clearTimeout(initialTimeout);
        try {
          if (!event?.pointcloud) throw new Error("查看器未返回点云对象");
          pointcloud = event.pointcloud;
          viewer.scene.addPointCloud(pointcloud);
          const material = pointcloud.material;
          material.size = 1;
          material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
          material.activeAttributeName = "rgba";
          viewer.fitToScreen();
          setStatus("loading", "层级已打开，正在加载可见点…");
          window.setTimeout(() => {
            if (!loaded) setStatus("error", "加载失败：30 秒内没有可见点，请检查 Range 请求和浏览器控制台。");
          }, 30000);
        } catch (error) {
          setStatus("error", `加载失败：${error.message}`);
        }
      });
      window.setInterval(updateVisiblePoints, 500);
    } catch (error) {
      setStatus("error", `加载失败：${error.message}`);
    }
  }

  budget.addEventListener("input", () => {
    document.getElementById("budget-value").textContent = number(Number(budget.value));
    if (viewer) viewer.setPointBudget(Number(budget.value));
  });
  document.getElementById("fit-view").addEventListener("click", () => viewer?.fitToScreen());
  document.getElementById("top-view").addEventListener("click", () => viewer?.setTopView());
  document.getElementById("front-view").addEventListener("click", () => viewer?.setFrontView());
  void start();
})();
