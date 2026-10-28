# 私有真实 LAS 样本 CI 实施计划 / Implementation Plan

> **供执行者使用：** 按任务逐项实施并勾选；执行前必须使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`，每项先测后改。

**目标：** 让受控 GitHub Actions 真正下载私有机电房 LAS，完整转换 8,394,852 点并完成浏览器预览验收，同时不公开点云数据。

**架构：** 独立 GitHub 私有仓库的固定 Release 附件承载原始 LAS；公开仓库的专用 Windows 托管作业只在 `main` push 或从 `main` 手动运行。下载后先校验大小与 SHA-256，再使用现有固定版本 PotreeConverter 和查看器运行真实转换、浏览器测试；私有数据和截图不上传为公开工件。

**技术栈：** Python 3.11、pytest、pytest-playwright/Chromium、PowerShell、GitHub Actions、GitHub CLI、PotreeConverter 2.1.1 Windows x64、Potree 查看器 1.8.2。

**规格：** [私有真实 LAS 样本持续集成设计](../specs/2026-10-28-private-real-las-ci-design.md)。

## 全局约束

- 样本为 LAS 1.2、RGB 点格式 2，218,266,379 字节、8,394,852 点，SHA-256 `ee1ac0dc4f49f9634d803d4a62313fda903d4d07d08dc86c5bd4afd040bffb69`，单位经用户确认是 m。
- 只在 `main` 的 `push`/`workflow_dispatch` 使用私有凭据；PR、fork 和任意 PR 分支不下载样本。
- 仅用 GitHub 托管 Windows 运行器；转换器执行文件 SHA-256 `117dab69b06c99a01367f178019ce8f094a9faa2ecffcbaca7b6c554313c0a1e`，查看器脚本 SHA-256 `e503483823e30ff5398382840343fc46bbc3df604d7d589ee4508a014c38ca4c`。
- 官方转换器下载包 SHA-256 `8b4a70194fa85ceafa51e017b58e455cf3539edc9ab212b2c92b4a1f2b15e887`，查看器下载包 SHA-256 `c140a8bce0ce129de762fbc0fd3ba7c21120ffd36d423dd2755abb39f32aebe5`。
- 原始 LAS、Potree 二进制、截图、trace 不进入公开仓库、缓存或 Actions Artifacts；私有资源缺失时失败，不跳过。
- 不改生产转换/发布/服务逻辑，不做分割准确率或 Mesh/CAD 评价。若必须改生产逻辑，暂停并请用户重新确认范围。

## 文件结构

- 新增 `scripts/verify_private_real_las.py`：验证下载文件的大小、SHA-256、LAS 格式和点数；作为专用 CI 前置门禁。
- 新增 `tests/test_verify_private_real_las.py`：小型合成 LAS 的成功和错误输入回归。
- 新增 `ci_private/test_real_las_browser.py`：位于常规 `tests/` 收集树之外，仅专用 CI 调用的真实页面、Range 和原始 LAS 不外传断言。
- 新增 `.github/workflows/private-real-las.yml`：受控 Windows 作业，下载固定工具与私有附件，执行转换及专用浏览器测试。
- 新增 `tests/test_private_real_las_workflow.py`：静态检查工作流事件、凭据边界和禁用工件上传。
- 更新 `README.md`：中文操作说明、手动触发、凭据轮换和“PR 未覆盖私有样本”的边界。
- 更新 `tests/browser/test_full_preview_browser.py`：旧测试显式选择原有完整资产，避免新增本地样本改变列表排序后误选。

## 重点复审情形

1. 私有令牌为空或失效：专用作业明确失败，不能 `skip` 或误用公开仓库 `GITHUB_TOKEN`。
2. Release 附件不存在或更名：下载步骤失败，不能回退到另一份 LAS。
3. 下载截断或附件被替换：大小或 SHA-256 不符时，在转换前失败。
4. 官方工具包下载内容变化：包和解压后关键文件指纹不符时失败，不自动接受新版本。
5. 浏览器未加载层级/点数据，或请求原始 LAS：即使页面框架显示出来，专用验收仍失败。

---

### 任务 1：私有 LAS 前置校验

**文件：** 新增 `scripts/verify_private_real_las.py`、`tests/test_verify_private_real_las.py`。

**接口：** `verify_fixture(path: Path, *, expected_size: int, expected_sha256: str, expected_points: int) -> None`；CLI 接收一个文件路径，使用全局约束中的固定值，不输出文件内容。

- [ ] 写失败测试：生成小型 LAS 1.2/RGB 点格式 2，正确大小、SHA 与点数通过；缺文件、错误大小、错误 SHA、错误点数、错误点格式与符号链接逐一抛出异常。
- [ ] 运行 `python -m pytest tests/test_verify_private_real_las.py -q -p no:cacheprovider`，确认新增用例先失败。
- [ ] 用 `pc_system.full_preview_source.inspect_las_source` 完成只读格式检查及哈希，增加大小、点数比较与 CLI 非零退出码。
- [ ] 重跑聚焦测试至通过，并执行 `python -m compileall -q scripts/verify_private_real_las.py`。
- [ ] 只暂存本任务两个文件；按项目单意图提交原则提交。

### 任务 2：真实浏览器验收

**文件：** 新增 `ci_private/test_real_las_browser.py`。

**接口：** 从环境变量读取专用 CI 的项目根、Potree 查看器目录和资产 ID；CI 资产 ID 固定为 `private-real-las`，本机已有产物可用 `filtered-points-ci-probe`；用 `get_active_preview(project_root, asset_id)` 获取动态版本，不硬编码 run ID。

- [ ] 写专用测试：缺失环境或未发布资产直接失败；已发布资产的清单断言 8,394,852 点、预期 SHA 和 RGB。
- [ ] 对本地已生成的真实样本运行该测试，确认页面 `ready`、可见点数大于零、层级和八叉树请求返回 206、无 `.las` 请求；不生成截图或 trace。若本机套接字权限仍阻止联测，记录环境阻塞并以远端 CI 为最终证据，不改测试为 `skip`。
- [ ] 确认常规 `tests`、`tests/browser` 收集范围均不包含仓库根的 `ci_private`，现有 PR 测试不依赖私有样本。
- [ ] 只暂存此测试文件并提交。

### 任务 3：受控工作流与静态门禁

**文件：** 新增 `.github/workflows/private-real-las.yml`、`tests/test_private_real_las_workflow.py`；更新 `README.md`、`tests/browser/test_full_preview_browser.py`，纳入本规格与实施计划。

**接口：** 工作流调用任务 1 CLI，然后调用现有 `python -m pc_system.cli prepare-full-preview`，最后只运行任务 2 测试。私有仓库、Release 标签和附件名由设计规格固定；令牌通过 `private-real-las` 环境的 `PRIVATE_FIXTURE_READ_TOKEN` 注入下载步骤。

- [ ] 先写静态失败测试：仅 `main` push/手动触发；作业不运行 PR/fork；缺 secret 不跳过；不含 `upload-artifact`、缓存私有数据、`--tracing retain-on-failure` 或 `--screenshot only-on-failure`。
- [ ] 运行 `python -m pytest tests/test_private_real_las_workflow.py -q -p no:cacheprovider`，确认失败。
- [ ] 实现 Windows 专用工作流：固定来源下载工具包并核对文档中的包 SHA；私有 Release 下载到 `$RUNNER_TEMP`；执行任务 1、完整转换、任务 2。`GH_TOKEN` 只供下载步骤使用，公开仓库 `permissions: contents: read`。
- [ ] README 用中文解释公开 PR 与私有 `main` 后置验收的区别、手动运行、失败处理、令牌到期轮换；不附样本字节或私有令牌。
- [ ] 聚焦测试通过，执行 `git diff --check`；再按项目门禁运行一次非浏览器全仓测试和常规浏览器测试。只暂存本任务工作流、测试、README、既有浏览器测试、规格和计划文件并提交。

### 任务 4：私有附件配置和远端验收

**外部对象：** 私有仓库 `PointCloud-Platform-Private-Fixtures`；Release `real-las-v1` 的 `filtered-points-v1.las`；公开仓库 Actions 环境 `private-real-las` 及只读 secret。

- [ ] 在创建前确认目标账户与仓库名，创建后用 API 明确核对 `visibility=PRIVATE`；仓库 Git 历史不包含 LAS。
- [ ] 上传固定附件前后核对本机 SHA-256；上传完成后以授权和未授权请求分别验证访问边界。不得把附件加到公开仓库 Release。
- [ ] 指导用户创建仅授予该私有仓库 `Contents: read`、有到期日的细粒度令牌，并由用户在 GitHub 设置页存入 `PRIVATE_FIXTURE_READ_TOKEN`；不索取、展示或复用用户现有宽权限令牌。
- [ ] 用任务 1 和任务 3 的本地测试核对缺凭据会失败；先由用户配置私有凭据，**再**将工作流并入 `main`，避免主分支首次运行因尚未配置而预期失败。
- [ ] 工作流并入 `main` 后先核对首次 `push main` 自动运行结果：真实点数、RGB、转换、HTTP 206、无 `skip`，且无私有数据 Artifacts；再从 `main` 手动触发复验。若网络或账户权限阻止，保留可恢复状态并明确报告未完成项，不宣称 CI 已覆盖。

### 最终门禁

- [ ] 独立复审精确 Git 变更与工作流信任边界，修复严重/重要问题。
- [ ] 核对全部聚焦测试、常规 CI 与首次远端私有作业日志；检查公开仓库、Release 和 Artifacts 均不含原始 LAS 或 Potree 数据。
- [ ] 汇报提交 SHA、私有样本仓库可见性、远端 Actions 运行链接与实测结论；只在证据齐备时标记完成。
