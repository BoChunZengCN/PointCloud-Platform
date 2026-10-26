# LAS 点云直接预览验证记录

## 本轮范围

现有展示页增加“直接预览 LAS”入口。页面使用浏览器本地 File API 和 WebGL，不上传或改写 LAS；仅接受未压缩 LAS 1.2/1.3、RGB 点格式 2/3，单文件上限 128 MiB 和 750,000 点。当前四份候选均在上限内，页面对单个获准文件不抽样。

## 已获得的证据

- `node --test tests/js/test_las_preview_core.cjs tests/js/test_las_preview_renderer.cjs`：8/8 通过；覆盖真实 XYZ/RGB 解码、格式 2/3、损坏头部、截断、超预算与数值溢出拒绝，以及 WebGL 缓冲失败、上下文丢失、上传/绘制报错时不得误报成功。
- 使用项目现有虚拟环境执行 `python -m pytest -q tests/test_las_preview_page.py`：2/2 通过；展示页入口和新页面控件存在。
- `node --check frontend/las-preview-core.js`、`node --check frontend/las-preview.js`：通过。
- `node scripts/verify-las-preview-samples.cjs <候选目录>`：四份真实候选分别解析 651,762、330,267、272,351、184,671 点，合计 1,439,051 点；RGB 非零、尺寸与导出清单相符。原始完整扫描 SHA-256 与处理前记录一致。

## 尚未通过的门禁

- 本机浏览器自动化未得到可用的 Chromium 会话；应用内浏览器明确阻止打开 `file://` 本地页面。不得将自动化尝试算作交互验收。需用户在本机浏览器手动打开 `frontend/las-preview.html`，依次选入四份候选，确认可见彩色点云、旋转/缩放、三视角和切换后旧图消失。
- 独立复审发现数值溢出与 WebGL 失败状态两个重要问题，已分别增加回归测试并修复。由于复审执行环境用量限制，最终复审报告未能返回；这两个问题的聚焦验证已通过，浏览器实测仍是未关闭门禁。
- 全仓非浏览器测试在 1,137 通过、3 跳过后，遇到旧 `tests/test_phase15d_decisions.py::test_scanned_candidate_summary_preserves_scanned_representation` 的 `model_index_coverage_rejected`。同一测试在未修改的 `main` 上复现，和本轮前端改动无关；列为已有技术债，不在本轮修复。初次测试还受本机内部回环 socket 与 Windows 临时路径长度限制，已分别用受控执行和短路径复核，不修改旧生产代码。

## 人工验收步骤

1. 使用本机浏览器打开 `frontend/las-preview.html`，从忽略的候选目录多选四个 `.las` 文件。
2. 首个文件应显示“全部原始点已显示”和 651,762 点；选择其余文件时点数分别为 330,267、272,351、184,671。文件切换后画布必须显示当前文件，而非保留旧图。
3. 拖动旋转、滚轮缩放，点击顶视 XY、正视 XZ、侧视 YZ；观察管线与设备是否混合、表面是否缺失。页面仅用于可视化复核，不能据此宣布分割或 Mesh 质量合格。
4. 若浏览器显示 WebGL 不可用、空白画布或尺寸明显异常，请保存截图与浏览器版本，作为下一轮修复输入。
