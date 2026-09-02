# Phase 15D 人工决策、不可变模型绑定与双工作台

## 交付状态

本阶段已完成本地开发与验收。阶段全仓门禁为 **1153 passed、1 skipped**；最终恢复边界收紧后，另有 **41 项受影响模块/API 回归通过**。真实 Chromium 内核浏览器场景 **6 项通过**。本地提交不代表已推送、合并或发布到 GitHub。

## 角色与页面

| 角色 | 主要页面 | 允许的操作 |
| --- | --- | --- |
| `operator` 业务人员 | [业务决策工作台](../frontend/model-decisions.html) | 确认通过门禁的候选、拒绝候选、声明无匹配。 |
| `expert` 专家 | [专业匹配工作台](../frontend/model-matching-lab.html) | 包含业务操作；复核 `review_required`、重新配准、替换及恢复绑定。 |
| `auditor` 审计员 | 专业匹配工作台 | 只读查看技术依据、历史和审计，不执行任何写操作。 |

生产身份由服务端 `principal_bindings` 验证。页面可在“连接与身份”填写 API 访问令牌，仅保留在当前页面内存，不写入 URL、浏览器本地存储或业务工件。已有网关请求身份也可使用。页面展示不能授予权限；后端会再次验证每次操作。

页面默认请求同源 API，也支持显式 `?api=https://可信服务地址`。跨源部署需要既有 CORS 配置；不要向不可信 API 输入令牌。静态资源由部署服务器提供，本阶段不改变生产 API 的静态托管架构。

## 清单与日常操作

清单由已验证、已完成的配准报告自动生成，不需要人工创建标注任务。

- 待处理：尚未决定，或已决定后新增了配准证据。
- 已处理：最新确认或无匹配决定覆盖当前证据。
- 全部：包含待处理、已处理和陈旧事项。
- 已陈旧：对象指纹变化或当前绑定不再匹配对象。普通确认被阻止，由专家处理。

支持资产、类别、质量状态和时间筛选。API/CLI 还支持处理人、状态和游标分页；页大小为 1–100，游标不能跨筛选复用。

业务操作通常只有三步：选择事项和候选、填写核验范围及原因、点击确认/拒绝/无匹配。候选拒绝只排除此条配准，事项仍待处理；全部拒绝后可声明无匹配。确认才会创建对象—模型绑定，拒绝和无匹配不会创建绑定。已有绑定的事项不能通过普通确认创建第二条根链。

`identity` 表示物体身份核验，`operational_pose` 表示作业参考位姿，`expert_pose` 仅供专家使用。任何确认均不等于安装可行性、碰撞安全或工程安全认证。算法 `rejected` 以及失败配准不能被人工强行绑定。

## 专家处理与版本恢复

专业页展示候选模型版本、Top-K 检索依据、配置与引擎、模型到对象的刚性矩阵、双向覆盖率、残差（m）、尺寸相对误差、门禁原因、决定历史、绑定链和审计引用。

1. 重新配准：选择已有发布配置，调用 Phase 15C 配准服务，生成新的独立报告；不会直接修改当前绑定。
2. 替换：选择合格配准并填写原因，创建新决定和新绑定，以 `supersedes_binding_id` 接续当前头。
3. 恢复：选择同一对象、指纹仍适用的历史绑定，创建新的绑定版本，以 `restores_binding_id` 记录来源。**将创建新版本，不修改旧绑定。**

恢复沿用历史绑定的原始模型版本、表达、配准报告和矩阵；不能把旧位姿复制到已经改变的对象点集。历史工件保留可追溯性，不提供自动递归删除或重命名恢复。

## 多人同时操作与提交恢复

多人可以同时查看同一事项，不使用长期页面占用锁。每次提交携带 `expected_case_revision`，服务端在对象级内核锁内复验：第一提交者获胜，其他旧修订提交返回 `decision_conflict`（409）。页面禁用旧操作并提示刷新，不能自动覆盖另一人的决定。

一次提交由 `owner.json`、`decision.json`、可选 `binding.json` 和最后发布的 `commit.json` 组成。提交清单、业务事件和审计完成结果互相绑定；未完成提交不作为正式决定公开。对象锁覆盖发布、审计完成和公开读取核验，不承诺与 Phase 14/15C 构成跨阶段事务。

- 请求响应丢失：使用完全相同的请求、`operation_id`、`request_id` 和 `idempotency_key` 重试，返回原结果。
- 已有 owner 的中断操作：按冻结上下文原位补齐，其他操作不能接管；恢复不被后来新增证据改写。
- 页面仍在时：原操作重试沿用内存中的提交请求；不要在结果未确认时修改原因、候选或切换身份。
- 关闭页面前仍未确认：保留操作编号和原请求，由专家使用 API/CLI 查询并重试；本阶段没有跨浏览器会话的自动恢复收件箱。
- 完整性损坏：停止写入，核对备份和审计证据，不自动“修复”被篡改工件。
- 事项定位依赖暂时不可用：无法证明不存在 owner，操作保持 `running` 并返回稳定错误；恢复证据后重试原请求。只有对象锁内已确认无 owner 的拒绝才终结为 `failed`。

## API 与 CLI

查询接口：

```text
GET /model-matching/decision-items
GET /model-matching/decision-items/{case_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}/history
```

写入接口返回 201：

```text
POST /model-matching/decisions
POST /model-matching/bindings/{binding_id}/supersede
POST /model-matching/bindings/{binding_id}/restore
```

替换/恢复路径中的 `binding_id` 表示预期当前头；请求体的 `binding_id` 表示要新建的版本。请求体必须精确匹配字段集合，不接受客户端角色、主体覆盖、重复 JSON 键或非有限数字。业务响应不包含完整矩阵，审计员和专家可查询技术详情。

六个 CLI 命令与 API 复用同一领域服务：

```text
list-model-decision-items
show-model-decision-item
decide-model-match
list-model-bindings
supersede-model-binding
restore-model-binding
```

每个命令支持 `--help`。例如：

```powershell
pc-system list-model-decision-items --project-root "D:\项目数据" --actor operator-a --status pending
pc-system show-model-decision-item --project-root "D:\项目数据" --actor expert-a --expert --case-id <事项编号>
pc-system list-model-bindings --project-root "D:\项目数据" --actor expert-a --expert --asset-id <资产> --source-id <来源> --instance-id <对象> --history
```

CLI 属于已批准的本地可信操作边界；`--expert` 不能作为远程用户认证方案。重新配准复用 `register-model-candidate`，配置与身份部署参见 [Phase 15C 操作说明](phase15c-rigid-registration.md)。

## 稳定错误与处理建议

| HTTP | 典型错误码 | 建议 |
| --- | --- | --- |
| 400 | `decision_not_allowed`、`decision_reason_invalid`、`registration_not_eligible` | 核对核验范围、原因和配准资格，不提升权限绕过门禁。 |
| 403 | `permission_denied` | 使用服务端已配置的正确角色。 |
| 404 | `decision_item_not_found`、`decision_not_found` | 刷新清单并核对对象及事项编号。 |
| 409 | `decision_conflict`、`binding_exists`、`binding_stale` | 刷新后重新核验，已有绑定由专家替换或恢复。 |
| 409 | `artifact_integrity_failed`、`binding_chain_invalid` | 停止写入并核对权威工件与审计。 |
| 409 | `operation_busy` | 保留原请求，等待锁释放后重试同一操作。 |
| 503 | `publication_recovery_required`、审计持久化错误 | 保留原请求，按同一操作恢复；不要另建操作编号。 |

## 后续边界

本阶段只记录人工决定和自动化证据，**不训练、不自动推广生产参数或模型**。后续 Phase 15E 接入实物参考点云模板，Phase 15F/17 才建设受控优化与训练；统一三维查看器属于 Phase 16。绑定也不等于跨时间设备资产生命周期管理。

## 验证方式

```powershell
uv run --extra test pytest tests --ignore=tests/browser -q -p no:cacheprovider
uv run --extra test --extra browser-test playwright install chromium
uv run --extra test --extra browser-test pytest tests/browser -q --browser chromium --tracing retain-on-failure --screenshot only-on-failure -p no:cacheprovider
```

CI 的后端和浏览器作业分别执行；浏览器缺失或启动失败必须失败，不允许跳过。浏览器场景使用真实本机 HTTP API、独立用户上下文和确定性配准引擎，不替代生产 Open3D 精度评估。

本次 Windows 验收使用已安装的 Google Chrome（`--browser-channel chrome`）。新下载的 Chromium 在本机出现 `spawn UNKNOWN`，因此使用独立无界面 Chrome 测试配置；没有连接用户日常浏览器资料。CI 保持 Linux 标准 Chromium 安装与运行方式，远端 CI 尚需推送后验证。
