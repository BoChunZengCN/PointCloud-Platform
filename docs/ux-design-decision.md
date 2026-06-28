# UX Design Decision / 用户体验设计决策

Date: 2026-06-27

## Decision

The product will use a hybrid page strategy:

- Project-level workspace: use Version B, "Project Dashboard".
- Per-point-cloud showcase view: use Version C, "Viewer First".
- Version A remains a reference for operator-heavy production workflows, but it is not the primary direction for the next formal frontend iteration.

中文决策：

- 项目级首页采用 B 版本“项目驾驶舱型”。
- 每个点云项目内部的“展示页”采用 C 版本“查看器优先型”。
- A 版本“生产工作台型”保留为生产操作密集场景的参考，不作为下一轮正式页面主方向。

## Page Roles

### Project Dashboard

The dashboard is the landing view for the platform. It should help users understand:

- How many point-cloud assets exist in the current workspace.
- Overall project health and risk.
- Current production progress.
- Which assets or tasks need attention.
- Which project should be opened next.

中文说明：

项目驾驶舱是平台进入后的主视图，用于展示项目健康度、资产规模、生产进度、风险提醒和下一步决策事项。

### Viewer First Showcase

The showcase view is the visual entry inside a selected point-cloud project. It should help users:

- Inspect the project result first.
- Switch layers such as RGB point cloud, segmentation labels, QA markers, Potree view, or Gaussian Splatting view.
- See asset metadata without leaving the viewer.
- Open reports and production outputs as supporting panels.

中文说明：

展示页是进入单个点云项目后的成果查看入口。页面应优先呈现三维点云或可嵌入查看器，图层、资产信息、报告和任务状态作为辅助面板。

## Navigation Model

Recommended navigation flow:

1. Platform opens to the project dashboard.
2. User selects a project or asset from the dashboard.
3. User enters the viewer-first showcase page for that point-cloud project.
4. Supporting actions such as reports, production plan, QA status, and job status are available without obscuring the viewer.

中文导航模型：

1. 平台默认进入项目驾驶舱。
2. 用户从驾驶舱选择项目或点云资产。
3. 进入该点云项目的查看器优先展示页。
4. 报告、生产计划、质量状态和任务状态作为侧边或浮层信息提供。

## Implementation Priority

1. Convert the main frontend entry to the Version B dashboard structure.
2. Add real API/workspace data source status to the dashboard.
3. Add project and asset selection from the real asset registry.
4. Create a project detail route or page based on Version C.
5. Connect the showcase page to viewer URLs, Potree outputs, Gaussian Splatting outputs, and report links.
6. Preserve the existing design-option pages as decision references until the formal frontend is stable.

中文实现顺序：

1. 将正式首页改造成 B 版本项目驾驶舱结构。
2. 在驾驶舱中加入真实数据来源状态。
3. 接入真实 asset registry，实现项目/资产选择。
4. 基于 C 版本建立项目内展示页。
5. 展示页接入 Potree、Gaussian Splatting、报告和生产输出链接。
6. 保留当前设计候选页面，直到正式前端稳定。

## Notes For Future Development

- Avoid presenting simulated point-cloud visuals as real data. Label mock or sample visualization clearly.
- Distinguish manifest JSON from iframe-ready viewer HTML.
- Keep the dashboard management-oriented and the showcase visual-first.
- Keep production job state visible, but avoid letting task controls dominate the visual showcase page.

中文注意事项：

- 模拟点云视觉必须明确标注为样例或占位，避免误导用户。
- 需要区分 manifest JSON 和可嵌入 iframe 的 viewer HTML。
- 驾驶舱保持管理和决策视角，展示页保持视觉优先。
- 生产任务状态需要可见，但不应压过点云展示本身。
