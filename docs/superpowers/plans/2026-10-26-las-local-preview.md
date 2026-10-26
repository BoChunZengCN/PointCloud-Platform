# 平台内 LAS 点云直接预览实施计划

> **实施者提示：**按 `superpowers:executing-plans` 在当前隔离工作区逐任务执行；每项功能先写失败测试，再写实现。

**目标：**现有展示页可进入本地 LAS 三维预览页，并用 4 个真实候选文件验证原始点加载。

**架构：**纯浏览器解析器负责边界校验与解码，WebGL 页面负责交互和状态；文件只由用户在浏览器选择，不经过后端。超限文件明确拒绝，保留现有 Potree 发布路径供未来大场景使用。

**技术栈：**原生 JavaScript、WebGL、Node 内置测试、现有静态前端。

**规格：**`docs/superpowers/specs/2026-10-26-las-local-preview-design.md`

## 全局约束

- 只读取未压缩 LAS 1.2/1.3、点格式 2/3；上限 128 MiB 和 750,000 点；不静默抽样。
- 不上传、持久化或提交真实扫描文件；所有文档和页面说明以中文为主。
- 页面必须区别真实显示点云、错误状态和占位状态；WebGL 不可用不得伪装成功。

## 复审重点

- 恶意或截断 LAS 头部是否可能越界读取、分配过量内存或误显示旧数据。
- 格式 2/3 RGB 偏移及比例尺是否正确，坐标中心化是否保留现场尺寸。
- 文件切换时是否释放旧 WebGL 缓冲并拒绝迟到的异步读结果。
- 真实候选 LAS 是否按清单点数完整加载，超限原始场景是否明确拒绝。
- 文件名及状态输出是否安全，页面是否可在现有本地静态前端打开。

## 文件职责

- `frontend/las-preview-core.js`：LAS 校验、解码与视角数学的可测试核心。
- `frontend/las-preview.js`：文件选择、WebGL 绘制、交互和 UI 状态。
- `frontend/las-preview.html`、`frontend/las-preview.css`：平台内页面与样式。
- `frontend/viewer.html`、`README.md`：入口和操作说明。
- `tests/js/test_las_preview_core.cjs`：解析器和视角核心回归测试。
- `tests/test_las_preview_page.py`：页面可打开、入口可达的集成测试。

### 任务 1：有界 LAS 解析器

- [x] 新建 Node 测试，构造有效格式 2/3 小型 LAS 和损坏头部，断言点数、XYZ、RGB、明确拒绝路径。
- [x] 运行 `node --test tests/js/test_las_preview_core.cjs`，确认因模块/行为缺失而失败。
- [x] 实现 `parseLasHeader(arrayBuffer, fileSize)` 和 `readLasFile(file)`，输出完整原始点的 `Float32Array` 位置及 `Uint8Array` 颜色。
- [x] 运行聚焦测试并确认通过。

### 任务 2：平台内交互页面

- [x] 写页面集成测试，检查展示页的真实入口，以及预览页加载核心与渲染脚本并具有文件输入、画布、错误状态。
- [x] 运行测试，确认因页面/入口缺失而失败。
- [x] 实现 WebGL 绘制、旋转、缩放、三视角、多个文件切换及明确状态提示；失败或切换时清理旧画面和缓冲。
- [ ] 运行聚焦测试及 `node --check`；在可用浏览器中做实际交互冒烟。

### 任务 3：真实样本与交付验证

- [x] 使用 4 个候选 LAS 做只读解码验证，逐个比对清单点数、坐标边界和颜色存在；对超限完整场景验证拒绝。
- [x] 补中文使用说明，说明本地读取、上限、用途与非对象级精度结论。
- [ ] 运行聚焦测试、静态检查、全仓测试一次及 `git diff --check`，复核 Git 不包含扫描数据。

未勾选的两项仅因浏览器画面尚未实测、全仓旧 Phase 15D 用例在未修改的 `main` 上也失败。已完成的聚焦测试、静态检查和真实样本证据见 `docs/2026-10-26-las-preview-validation.md`。
