# 完整 LAS 分层预览实施计划

> **执行者：**逐任务使用 `superpowers:executing-plans`；每个功能先写失败测试、再做最小实现。此计划由主代理原地实施，结束时进行一次独立最终复审。

**目标：**让平台可读取 `machine zoom.las` 的完整 47,899,752 点，转换成可按需加载的分层预览，并在本机平台页面查看；原始文件保持不变。

**架构：**源文件仅头部读取和流式指纹，外部 PotreeConverter 生成新版本产物，严格验证后原子发布；只读回环 HTTP 服务为查看器提供同版本元数据与精确 Range 响应。小文件直读页保留原限额。预览清单采用来源格式与派生表示字段，为未来多格式导入留下边界，不在本轮实现其他格式。

**技术栈：**Python 标准库/现有 CLI、PotreeConverter 2.1.1 与 Potree 1.8.2 作为首选兼容探针对、静态 JavaScript、现有 pytest/Node 测试、Chromium 人工验收。

**规格：**`docs/superpowers/specs/2026-10-26-full-las-layered-preview-design.md`

## 全局约束

- 原始 LAS 为 1,245,393,779 字节、LAS 1.2、点格式 2、47,899,752 点；坐标单位按用户已确认的 m。禁止调用会整文件读入的现有 `run_ingest`。
- 一次准备只接受显式资产标识、源路径和转换器路径；本机静态服务绑定 `127.0.0.1`，绝不提供原始 LAS 或通用磁盘路径。
- 当前小文件页的 128 MiB / 750,000 点上限保持；大文件走独立分层路径。`source_format=las`、`representation_kind=point_cloud`、`viewer_format=potree2` 写入新预览清单，未来格式不在本轮导入。
- 所有运行目录不可变；同资产并发准备明确拒绝；完整验证后才切换活动指针。旧活动会话 URL 保持在原 run-id，不跨版本请求。
- 第三方程序与查看器资源必须来自官方发布并记录固定版本、来源和 SHA-256；探针不通过时停止，不用其他未经验证的二进制替代。
- 不上传、复制、改写或提交真实 LAS；转换产物、第三方资源与运行日志留在被 Git 忽略的 `workspace/`。不自动递归清理用户目录。
- 使用项目根 `.venv` 运行本工作区 Python 测试时须先设置 `PYTHONPATH=src`；其 editable 安装当前指向旧工作区，未设置会误测其他分支。

## 文件职责

- 新建 `src/pc_system/full_preview_source.py`：LAS 头部与源指纹、通用来源描述。
- 新建 `src/pc_system/full_preview_conversion.py`：转换器调用及 Potree 2.x 输出验证。
- 新建 `src/pc_system/full_preview_publication.py`：运行状态、同资产锁、不可变发布、活动指针、回滚及审计。
- 新建 `src/pc_system/full_preview_server.py`：固定路径的本机只读 HTTP 与单段 Range。
- 新建 `src/pc_system/commands/full_preview.py`，修改 `src/pc_system/cli_parser.py`、`src/pc_system/cli.py`：准备、启动查看、列版本、回滚命令。
- 新建 `frontend/full-las-preview.html`、`frontend/full-las-preview.js`、`frontend/full-las-preview.css`；修改 `frontend/viewer.js`、`frontend/viewer.html`：真实点云入口、加载状态和失败提示。
- 在 `tests/` 加各模块聚焦、服务集成与浏览器测试；更新 `README.md` 与中文验收报告。

## 复审重点

1. 源在转换期间变化：不发布；源 SHA-256 前后必须一致。测试归任务 2。
2. 元数据有点数但 RGB/层级偏移错误：不标记 ready。测试归任务 2。
3. 两个同资产运行并发：第二个明确拒绝，旧成功版本不受影响。测试归任务 3。
4. `Range` 无效、超界、多段或缺失：服务不得返回整份大二进制。测试归任务 4。
5. 发布/回滚发生在页面加载中：元数据与两个 bin URL 必须保持同一 run-id。测试归任务 5。

## 前置门禁：保护当前小文件预览工作

- [x] 用现有聚焦测试和 `git diff --check` 复核小文件预览；精确暂存其代码/测试/说明，形成独立本地提交，不把本规格与其混入同一提交（`c579a91`）。
- [x] 在新提交基础上继续本计划；不推送或合并，直到完整场景功能、真实样本与浏览器验收达到门禁。

## 任务 1：只读源检查与平台预览来源契约

**文件：**新建 `full_preview_source.py`、`tests/test_full_preview_source.py`；不调用或改造会整文件读取的旧资产登记路径。

**接口：**`inspect_las_source(path: Path, asset_id: str, declared_unit: str = "m") -> LasSource`；输出绝对源路径、`source_format`、`point_count`、`size`、`sha256`、`point_format`、`bounds`、`unit`，不持有点数组。

- [x] 写失败测试 `test_large_las_is_inspected_without_whole_file_read`：构造小 LAS、禁止调用 `laspy.read`，校验字段；非法签名、截断、无效标识分别拒绝。
- [x] 运行聚焦测试，确认缺少新模块时红灯。
- [x] 实现头部解析和流式 SHA-256；为来源描述保留 `source_format`/`representation_kind`，不改现有模型库格式校验。
- [x] 聚焦测试转绿；用真实 `machine zoom.las` 只读探针核对 47,899,752 点与文件字节数。
- [x] 精确暂存并提交本任务文件（`da2e222`）。

## 任务 2：转换器调用与严格产物验证

**文件：**新建 `full_preview_conversion.py`、`tests/test_full_preview_conversion.py`。

**接口：**`convert_and_validate(source: LasSource, converter: Path, run_dir: Path) -> ValidatedPotreeOutput`；结果含 `run_dir`、`metadata_file`、`point_count`、`bounds`、`rgb_available`，只在新运行目录执行命令，不负责切换活动版本。

- [x] 写失败测试：转换器缺失/非零退出、缺 `hierarchy.bin`/`octree.bin`、点数不符、RGB 缺失、边界异常、层级 offset 越界、转换后源指纹变化都拒绝。
- [x] 运行聚焦测试，确认缺少新模块时红灯。
- [x] 实现无 shell 字符串的 subprocess 调用、超时与日志边界，以及 Potree 2.0 元数据验证。
- [x] 聚焦测试转绿；通过官方来源取得并记录转换器 2.1.1 与查看器 1.8.2 的文件指纹，在一份小候选上完成真实转换和元数据探针。浏览器渲染仍归任务 5 验收，详见 `docs/2026-10-26-potree-compatibility-probe.md`。
- [x] 精确暂存并提交本任务文件及兼容性记录（`8ae7f54`）。

## 任务 3：版本化发布、审计与回滚

**文件：**新建 `full_preview_publication.py`、`tests/test_full_preview_publication.py`、`tests/test_full_preview_cli.py`，接入 `commands/full_preview.py` 与 `cli_parser.py`、`cli.py`。

**接口：**`prepare_full_preview(project_root: Path, source_path: Path, asset_id: str, converter_path: Path) -> PublishedPreview`；结果含 `asset_id`、`run_id`、不可变元数据相对 URL；`list_preview_versions(...)`、`activate_preview_version(...)`。

- [x] 写失败测试：同资产并发拒绝、失败运行不改变活动指针、成功运行生成不可变 run-id 与审计事件、回滚只切换既有成功版本、重启后只识别完整指针。
- [x] 运行聚焦测试，确认缺少新模块时红灯。
- [x] 实现准备状态机、单资产锁、原子指针更新和审计。复用任务 1/2 接口，不递归删除失败目录。
- [x] 聚焦测试转绿；CLI 虚拟转换器集成测试通过，真实小候选通过命令发布，清单包含 `source_format=las`、`representation_kind=point_cloud`、`viewer_format=potree2`。
- [x] 精确暂存并提交本任务文件（`b6d4361`）。

## 任务 4：本机只读 Range 服务

**文件：**新建 `full_preview_server.py`、`tests/test_full_preview_server.py`。

**接口：**`create_preview_server(project_root: Path, frontend_root: Path, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer`；`GET /api/full-previews` 从已发布活动指针列出资产和 run-id，`GET /previews/<asset_id>/runs/<run_id>/{metadata.json,hierarchy.bin,octree.bin}` 只映射已发布的不可变目录，另服务前端静态文件。

- [x] 写失败测试：合法 `bytes=start-end` 返回 `206`/精确内容与头，超界返回 `416`；无 Range 的大 bin、多段 Range、`..`、符号链接/重解析点及未发布 run-id 不得泄露数据。
- [x] 运行聚焦测试，确认缺少新模块时红灯；受限环境的本机套接字另需获准运行。
- [x] 实现固定端点路由、回环/Host 限制、单段有界读取及严格关闭文件句柄。
- [x] 聚焦测试转绿；真实小候选 `octree.bin` 的 `bytes=0-1023` 返回 `206` 与精确 1024 字节，无 Range 返回 `416` 与 0 字节。
- [x] 精确暂存并提交本任务文件（`1412b32`）。

## 任务 5：平台内真实 Potree 页面

**文件：**新建 `frontend/full-las-preview.{html,js,css}`、`tests/test_full_preview_page.py`、`tests/browser/test_full_preview_browser.py`；最小修改 `frontend/viewer.{html,js}`。

**接口：**展示页从 `/api/full-previews` 添加完整场景入口；预览页只接收 `asset_id`，入口先取活动 `run_id`，之后用相邻 URL 加载 `metadata.json`/`hierarchy.bin`/`octree.bin`。绝不将 manifest JSON 链接当成已加载点云。

- [x] 写失败页面测试：有成功产物才展示入口、加载失败有错误、点预算与源总点数分别标注，已打开页面固定旧 run-id。
- [x] 运行聚焦测试，确认新页面不存在时红灯。
- [x] 实现 Potree 查看器资源接入、旋转/平移/缩放/视角、RGB 和加载状态；不把 POC 的网格加载器误用为 LAS 加载器。
- [x] 聚焦及静态检查通过；真实小候选在本机 Chromium 显示点与颜色，从平台入口进入，网络记录证实 `206` Range，并保存截图。
- [x] 精确暂存并提交本任务文件（`6cd01b0`）。

## 任务 6：完整机电房验收与交付

**文件：**修改 `README.md`；新建 `docs/2026-10-26-full-las-preview-validation.md`；必要时仅添加直接受影响的回归测试。

- [x] 在磁盘空间充足且小候选端到端通过后运行完整转换；核对 47,899,752 点、源 SHA-256 前后相同、RGB 与范围元数据、版本化清单及审计。
- [x] 在本机浏览器实测完整场景旋转、缩放、局部细节、加载状态与不会整文件请求；记录截图和异常，不把可视化判作分割/Mesh 验收。
- [ ] 运行全部聚焦/相关集成、静态检查、`git diff --check` 及一次全仓测试；记录旧 Phase 15D 基线失败与新增失败的区别。
- [ ] 独立最终复审只检查严重/重要问题及证据；修复后精确提交。尚有浏览器或真实转换门禁未通过时，不推送/合并并明确报告。

## 自检结果

本计划覆盖规格中的源保护、数据转换、版本一致性、范围服务、查看器和真实样本六个可测试交付。未来 STL/OBJ/STEP/IGES/GLTF/FBX/DXF/XYZ/IFC 导入仅保留清单边界，另阶段规格化；本阶段不以 Potree 二进制冒充统一平台格式。
