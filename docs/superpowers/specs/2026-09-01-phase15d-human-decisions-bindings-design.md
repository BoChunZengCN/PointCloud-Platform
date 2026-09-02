# Phase 15D 人工决策、不可变模型绑定与双界面设计

## 1. 文档状态

- 阶段：Phase 15D
- 状态：书面规格已确认；2026-09-02 已完成本地实施与验收，尚未推送/合并/发布
- 基线：`main` 的 Phase 15C 合并提交 `3941390`
- 主要语言：中文
- 目标读者：平台开发、算法专家、业务操作人员、审计人员和测试人员

本次定向修订仅覆盖并发可见性、幂等恢复、已有绑定保护、事项定位和真实浏览器验收。保留不可变文件、对象资源锁、审计账本和双页面架构，不增加数据库、通用工作流或自动清理机制。

## 2. 背景

Phase 15B-2 已能从已发布的单对象点云检索 Top-K 模型候选，Phase 15C 已能对候选执行可审计刚性配准，并输出 `passed`、`review_required` 或 `rejected` 三态结论。现有结果仍然只是自动化建议，系统不会自动建立对象—模型生产绑定。

Phase 15D 在自动化建议之后增加人工业务决策层。人员可以确认、拒绝、标记无匹配、切换已有候选，专家可以重新配准、替换绑定和恢复历史绑定。所有决定和绑定均不可覆盖、可恢复、可审计，并作为 Phase 15F/Phase 17 后续受控优化的数据来源。

## 3. 阶段目标

Phase 15D 必须完成：

1. 从有效 Phase 15C 配准报告自动投影待处理事项。
2. 保存不可变人工决策：确认、拒绝和无匹配。
3. 确认时在同一业务提交包中发布不可变模型绑定。
4. 支持绑定替换、历史恢复、陈旧对象检测和完整历史查询。
5. 支持多人同时查看、第一位合法提交者获胜的乐观并发规则。
6. 提供业务决策页面和专业匹配页面。
7. 提供统一领域服务、API、CLI、审计和自动化测试。
8. 保留后续受控学习所需的可信决策来源，不在本阶段训练或修改生产参数。

## 4. 明确不做

本阶段不实现：

- 自动确认或自动建立生产绑定。
- 数据库、消息队列或通用工作流引擎。
- 实物参考点云模板；属于 Phase 15E。
- 自动参数搜索、Champion/Challenger 推广或模型训练；属于 Phase 15F/Phase 17。
- 点云、Gaussian Splatting 和语义模型的统一三维查看器；属于 Phase 16。
- 在线协同光标、任务领取、长租约或人员在线状态。
- 在页面中修改点云分割或 CAD 模型几何。
- 在业务页面暴露配准算法参数。

## 5. 已冻结的产品规则

### 5.1 权限

- `operator`：查看业务清单和详情；在已有有效配准结果中切换候选；确认 `passed`；提交拒绝或无匹配。
- `expert`：拥有业务操作能力；可确认 `review_required`；可触发新候选配准、选择已发布配准配置、替换绑定和恢复历史绑定。
- `auditor`：读取技术详情、历史、绑定链和审计记录；不能提交业务决定。
- `rejected` 结果不能由任何角色直接确认绑定。
- 所有写操作必须使用服务端绑定的可信主体、请求编号、操作编号和幂等键。

### 5.2 双页面

- 业务决策工作台：待处理、已处理、全部三个页签，面向 `operator` 和 `expert`。
- 专业匹配工作台：除清单外展示候选解释、配准指标、残差、矩阵、历史、绑定链和审计信息，面向 `expert` 和只读 `auditor`。
- 两个页面调用相同查询与决策 API；权限由后端强制执行，前端隐藏按钮不能替代授权。

### 5.3 并发

- 多人可以同时查看同一事项。
- 不提供手工领取或长时间锁定。
- 页面读取时获得对象指纹、候选证据集合指纹、决策头指纹、绑定头指纹和由它们共同计算的事项修订指纹。
- 提交时在对象级资源锁内重新验证事项修订指纹及其组成证据。
- 第一个合法提交成功；后续旧页面提交返回 `decision_conflict`，不得覆盖先前结果。
- 对象锁必须一直持有到 `complete_operation` 成功且提交包公开读取校验通过。其他操作即使使用不同 `decision_id` 或不同检索运行，也不能绕过同一对象的未完成提交。
- 每次新写入先检查该对象全部提交目录，包括尚未公开的目录；有效未完成 owner 返回 `publication_recovery_required`，损坏或身份不一致返回 `artifact_integrity_failed`。不得把不可见等同于不存在。
- 此锁串行化 Phase 15D 写操作，不锁住 Phase 14 对象发布或 Phase 15C 新证据发布。决定绑定锁内验证并冻结的输入快照；冻结后上游发生变化时，通过 `stale` 或 `pending` 投影反映，不追溯修改决定，也不宣称跨阶段原子提交。

### 5.4 待办来源

- 待办由有效 Phase 15C 配准结果、不可变决策和绑定历史动态投影，不保存独立队列任务文件。
- 同一对象版本和检索运行的多个候选配准归入一个决策事项。
- `passed` 和 `review_required` 进入待处理清单。
- 所有候选均为 `rejected` 时仍进入待处理清单，但只能重新配准或标记无匹配。
- 拒绝是候选级决定，不会关闭整个事项。只有确认或明确无匹配后才进入已处理投影；所有候选被算法门禁拒绝或被人员逐一拒绝时，事项仍保持待处理，只允许重新配准或无匹配。历史文件永久保留。

### 5.5 换候选

- `operator` 可在已有且完整性验证通过的候选配准结果之间切换，并确认其中的 `passed` 结果。
- 切换查看不是业务写操作，不为每次页面点击写审计事件。
- 尚未配准的候选、重新配准、配置选择和 `review_required` 处理只允许 `expert`。
- 专家触发新配准时调用 Phase 15C 既有领域服务；Phase 15D 不复制配准实现。

## 6. 总体架构

新增三个领域组件：

### 6.1 `model_match_decision`

负责决策请求规范化、权限与状态校验、不可变决策构造、提交包发布、幂等恢复和审计绑定。

### 6.2 `model_binding`

负责模型绑定结构、绑定链验证、当前有效绑定投影、陈旧检测、替换和历史恢复。绑定不能原位修改。

### 6.3 `model_decision_queue`

负责扫描并验证当前对象、Phase 15B-2 检索、Phase 15C 配准、Phase 15D 决策提交包，生成有界、可分页的待处理、已处理、全部和已失效投影。它是只读投影层，不保存队列任务文件。

### 6.4 依赖方向

```text
Phase 14 对象发布
  -> Phase 15B-2 检索报告
  -> Phase 15C 配准报告
  -> model_decision_queue
  -> model_match_decision
  -> model_binding
  -> API / CLI / 双页面

所有 Phase 15D 写操作
  -> model_resource_lock
  -> model_matching_audit
```

Phase 15D 可以读取 Phase 14、Phase 15B-2 和 Phase 15C 的公共验证函数，不得依赖页面状态或未经验证的 JSON。

## 7. 权威输入与身份

### 7.1 对象身份

沿用 Phase 15C 的：

- `asset_id`
- `source_id`
- `instance_id`
- `object_fingerprint`

对象点成员、源资产、纠正发布或类别身份变化会产生不同对象指纹。旧决策和绑定保留，但不能继续描述为当前有效状态。

### 7.2 决策事项身份

`case_id` 是以下规范字段的 SHA-256：

- `asset_id`
- `source_id`
- `instance_id`
- `object_fingerprint`
- `retrieval_run_id`

同一检索运行内新增候选配准不会改变 `case_id`；事项详情重新投影候选集合。新对象版本或新检索运行形成新事项。

`case_id` 不可反解为对象标识。统一使用 `resolve_decision_case_identity(project_root, case_id)`，从已验证的历史配准或决定工件定位唯一的 `asset_id`、`source_id`、`instance_id`、`object_fingerprint` 和 `retrieval_run_id`；不只扫描当前待办。未找到返回 `decision_item_not_found`，身份矛盾返回 `artifact_integrity_failed`。锁前定位仅用于选择资源锁，锁内必须复验身份与修订；API/CLI 不接受调用方另传字段覆盖定位结果。完成请求重放优先读取其已验证提交，因此事项变为陈旧或不再待办不影响历史结果重放。

### 7.3 候选与配准

候选必须来自该事项绑定的 Phase 15B-2 检索证据。配准必须满足：

- 路径身份和规范 JSON 完整性有效。
- 绑定同一 `asset_id`、`source_id`、`instance_id`、对象指纹和检索运行。
- 引用候选模型版本、采样表达和配置仍能验证。
- Phase 15C 审计链已完成且与报告指纹一致。
- `failed` 报告不可进入决定候选集合。

## 8. 数据模型

### 8.1 决策 `match_decision`

必需字段：

- `schema_version`：首版为 `1.0`
- `decision_id`
- `case_id`
- `asset_id`、`source_id`、`instance_id`
- `object_fingerprint`
- `retrieval_run_id`
- `evidence_fingerprint`：决定时已验证候选配准集合的规范摘要
- `registration_id`：`no_match` 时为空；其他决定必须存在
- `candidate_rank`：`no_match` 时为空
- `decision`：`confirmed`、`rejected` 或 `no_match`
- `decision_reason`：规范化的有限长度文本
- `verification_scope`：`identity`、`operational_pose` 或 `expert_pose`
- `decided_by`
- `decider_roles`
- `decided_at`
- `previous_decision_id`：首次决定为空，后续决定指向提交时的决策头
- `previous_decision_head_fingerprint`
- `expected_decision_head_fingerprint`
- `expected_binding_head_fingerprint`
- `expected_case_revision`
- `operation_id`

约束：

- `operator` 只能提交 `identity` 或 `operational_pose`。
- 只有 `expert` 可以提交 `expert_pose`。
- `confirmed` 必须引用可确认的配准。
- `rejected` 必须引用具体配准。
- `no_match` 不得引用配准或候选排名。
- 同一事项的决定通过 `previous_decision_id` 构成单链；分叉、循环或缺失前驱均为完整性错误。

### 8.2 绑定 `model_binding`

确认决定必须同时构造绑定：

- `schema_version`：首版为 `1.0`
- `binding_id`
- `case_id`
- `asset_id`、`source_id`、`instance_id`
- `object_fingerprint`
- `model_id`
- `model_version_id`
- `representation_id`
- `retrieval_run_id`
- `registration_id`
- `decision_id`
- `verification_scope`
- `rigid_transform_4x4`
- `created_by`
- `created_at`
- `transition`：`create`、`supersede` 或 `restore`
- `supersedes_binding_id`：首次绑定为空，替换和恢复必须引用当前绑定
- `restores_binding_id`：仅 `restore` 必须引用历史绑定
- `operation_id`

绑定中的矩阵沿用 Phase 15C 唯一方向：模型坐标变换到对象点云坐标。绑定复制矩阵和权威引用，并绑定来源配准报告指纹，不能引用预览工件作为权威输入。

### 8.3 提交清单 `decision_commit`

提交清单是一个决定是否可见的最后发布工件：

- `schema_version`
- `decision_id`
- `decision_sha256`
- `owner_sha256`：绑定恢复快照的规范原始字节
- `binding_id` 和 `binding_sha256`：非确认决定为空
- `case_id`
- `object_fingerprint`
- `evidence_fingerprint`
- `operation_id`
- `audit_event_hashes`
- `result_fingerprint`

读者只有在提交清单、文件原始字节哈希、审计完成快照和业务引用全部一致时，才公开该决定或绑定。

## 9. 存储布局

```text
reports/model_match_decisions/
  <asset_id>/<source_id>/<instance_id>/<decision_id>/
    owner.json
    decision.json
    binding.json        # 仅 confirmed
    commit.json         # 最后发布

reports/model_matching_resource_locks/
  model-decision-<digest>.lock
```

决策和绑定放在同一提交目录，避免跨目录双重发布。`model_binding` 组件从已验证提交包构建绑定链和查询视图。

不创建可修改的 `current.json`、状态文件或队列任务文件。当前决策头、绑定头和状态均从有效不可变提交投影；对象级内核文件锁保证写入时的检查与提交串行化。

### 9.1 owner 恢复快照与对象写入阻断

`owner.json` 不只是目录占用标记，必须保存有界、规范化的恢复快照：

- 操作编号、请求编号、幂等键及请求指纹；可信主体、角色与开始时间从已验证审计快照取得。
- 原样保存审计启动时的规范化业务请求（不得追加字段后另算哈希），包括事项修订、决定/绑定编号及适用的替换/恢复目标；请求指纹必须与审计账本一致。对象定位身份另存，并重算 case_id 验证；`transition` 必须与可信 `operation_type`、请求动作及前驱约束一致。
- 冻结的对象指纹、候选配准编号及报告指纹集合、决策前驱和绑定前驱的编号与指纹、恢复目标引用。候选证据摘要及两个头必须能重算出请求中的 `expected_case_revision`。

恢复时从这些历史权威工件重建完全相同的决定和绑定；不得信任未经复验的 owner 字段，也不得改用最新候选集合重新构造。owner 原始字节摘要纳入业务事件及提交清单。已存在文件只能逐字节比对，不能覆盖。

新操作扫描同一对象目录时：已完成且完整的提交正常参与投影；未完成 owner 阻止新写入；只有空目录且没有 owner/工件可忽略（不删除）；有工件却无 owner、审计失败但有 owner 或损坏 owner 均失败关闭。部分提交只能由原操作原位恢复；没有自动过期、抢占或丢弃路径，无法恢复时需要人工排障。

## 10. 队列投影

### 10.1 状态

- `pending`：当前对象版本和检索运行存在 Phase 15C 证据，且不存在覆盖当前证据的确认或无匹配决定。候选级拒绝只改变可选候选和允许动作，不关闭事项。
- `processed`：存在覆盖当前证据的确认或无匹配决定。
- `stale`：事项对象指纹不再等于当前发布对象指纹，或其当前绑定已失效。

同一 `case_id` 下新增有效候选配准会改变候选证据集合指纹和事项修订。原决定继续保留，但事项重新投影为 `pending`；这使专家重新配准或模型库补充候选后无需创建人工队列任务。

### 10.2 当前头

决策头和绑定头是对已验证提交集合、替换引用和对象指纹计算出的规范摘要。事项修订是对象指纹、候选证据集合指纹、决策头和绑定头的规范摘要。页面必须回传读取时的事项修订。写服务在锁内重新计算：

- 与期望值一致：继续。
- 不一致：返回 `decision_conflict`。
- 出现多个无合法链关系的当前绑定头：返回 `artifact_integrity_failed`，不得猜测赢家。

### 10.3 查询

查询必须支持：

- `status`：`pending`、`processed`、`stale` 或 `all`
- `asset_id`
- `class_id`
- `gate_status`
- 决策人
- 起止时间
- `limit`：1–100
- 不透明游标

排序固定为最近证据或决定时间降序，再按 `case_id` 升序打破并列。扫描只接受普通目录和普通文件，拒绝符号链接、重解析点、重复 JSON 键和不规范字节。

## 11. 决策规则

### 11.1 确认

- `passed`：`operator` 或 `expert` 可确认。
- `review_required`：仅 `expert` 可确认。
- `rejected`、`failed`：不能确认。
- 确认必须再次验证对象指纹、模型版本、表达、检索、配准、矩阵和审计链。
- 首次确认要求对象绑定历史中没有任何当前头；检查覆盖该对象所有事项和检索运行，而非只检查当前事项。
- 已存在有效绑定时必须使用显式替换或恢复操作。
- 普通确认遇到有效绑定返回 `binding_exists`；已有陈旧绑定头返回 `binding_stale`，也不得另建根链。专家通过显式替换接续旧链，或在历史目标与当前对象指纹一致时恢复。普通用户不能通过重新打开事项、新检索运行或更换绑定编号绕过该规则。

### 11.2 拒绝

- `operator` 或 `expert` 可拒绝具体配准。
- 拒绝只创建决定，不创建绑定。
- 拒绝一个候选不会隐式拒绝其他候选，事项保持 `pending`。
- 当前候选全部被拒后仍保持 `pending`，但只允许专家重新配准或人员明确提交 `no_match`。
- 拒绝原因进入后续困难负样本来源，但本阶段不训练。

### 11.3 无匹配

- `operator` 或 `expert` 可标记模型库中无合适模型。
- 无匹配只创建决定，不引用配准，不创建绑定。
- 后续新模型版本或新检索运行可以形成新事项；旧决定不删除。

### 11.4 替换

- 仅 `expert`。
- 必须引用当前绑定、新配准和新的 `binding_id`/`decision_id`。
- 新绑定 `transition=supersede`，`supersedes_binding_id` 指向提交时的当前绑定。
- 当前绑定头变化时返回冲突。

### 11.5 恢复

- 仅 `expert`。
- 恢复目标必须是同一对象身份链中的已验证历史绑定。
- 仍需重新验证当前对象指纹与目标模型版本完整性。
- 恢复使用历史目标原配准及检索运行；历史对象指纹必须等于当前对象指纹，不能把旧位姿直接用于已变更的点云。新决定属于该历史检索运行对应的事项，页面应读取该事项的最新修订再提交。
- 新绑定 `transition=restore`，同时引用当前绑定和历史目标绑定。
- 不重新激活、修改或复制旧提交清单。

## 12. 发布、幂等与恢复

### 12.1 操作顺序

1. 在审计账本启动操作并固化请求信封。
2. 若为已完成重放，验证并返回原提交；已失败重放返回原稳定错误，均不比较当前事项修订。
3. 由统一定位器取得对象身份，获取 `model-decision` 对象资源锁；锁内重新读取操作状态，处理同请求并发时已完成的结果。
4. 扫描该对象全部公开及未完成提交。别的操作存在未完成 owner 时阻断；本操作已有 owner 时进入恢复分支。
5. 仅对尚无 owner 的新提交：加载并验证权威输入、事项身份、修订、资格及当前绑定头；构造并 no-replace 发布 owner 恢复快照。
6. 从已验证 owner 和历史输入构造规范 `decision.json`、可选 `binding.json`，no-replace 补齐缺失工件；已存在时要求原始字节完全一致。
7. 幂等记录固定业务事件，最后发布 `commit.json`。
8. 仍在对象锁内完成审计，验证公开读取结果与完成结果一致后才能释放锁。

owner 发布之前且确定未落盘的领域拒绝可 `fail_operation`；owner 一旦存在，后续异常保留可恢复状态，不能把部分提交标为普通业务失败。发布是否落盘不确定时先在锁内检查实际工件，再决定分支。损坏证据不得自动补写或强行完成。

### 12.2 可见性

- 没有有效 `commit.json` 的目录永不出现在清单或绑定查询中。
- `confirmed` 的提交清单必须同时绑定决定和绑定；缺少任一文件均不可见。
- `rejected` 和 `no_match` 的提交清单必须明确绑定为空，不能残留绑定文件。

### 12.3 恢复

- 相同操作、请求编号和幂等键重试时，验证已有 owner 和工件原始字节，原位补齐缺失步骤。
- 已完成操作直接返回原结果；已失败操作重放原错误；运行中且无 owner 的操作仍需按最新头验证；运行中且有 owner 的操作使用冻结快照，不再用旧页面修订比较最新证据。
- 恢复须确认对象的决策/绑定前驱尚未被其他提交越过；若发现非法后继或分叉，返回完整性错误而不是另接新头。合法恢复后，上游快照变化只影响当前状态投影。
- 提交清单已耐久化但调用抛错时，重试以耐久化提交为准，不重复业务事件。
- 不同操作遇到未完成目录返回 `operation_busy` 或 `publication_recovery_required`，不得接管。
- 任何身份、路径或字节不一致均失败关闭。
- 不自动递归删除、移动或隔离异常目录。

## 13. 审计事件

稳定业务事件包括：

- `match.decision_confirmed`
- `match.decision_rejected`
- `match.decision_no_match`
- `model_binding.created`
- `model_binding.superseded`
- `model_binding.restored`

每个事件只记录固定、受界限的结构化字段和工件指纹。审计事件、提交清单和完成结果必须相互绑定。页面查看和候选切换不写业务事件；触发 Phase 15C 新配准由 Phase 15C 自身记录自动化审计。

## 14. API

### 14.1 查询

```text
GET /model-matching/decision-items
GET /model-matching/decision-items/{case_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}/history
```

查询根据可信角色裁剪字段：业务响应不返回完整矩阵和内部审计结构；专业响应可以返回经验证技术详情。

### 14.2 写入

```text
POST /model-matching/decisions
POST /model-matching/bindings/{binding_id}/supersede
POST /model-matching/bindings/{binding_id}/restore
```

所有写入在读取请求体前完成可信身份授权，并应用既有请求体大小、严格 JSON、请求编号、操作编号和幂等约束。

专家页面触发新配准时调用 Phase 15C 已有：

```text
POST /model-matching/registrations
```

## 15. CLI

新增：

- `list-model-decision-items`
- `show-model-decision-item`
- `decide-model-match`
- `list-model-bindings`
- `supersede-model-binding`
- `restore-model-binding`

CLI 和 API 调用同一领域服务，不实现旁路写入。专家的新配准继续使用 `register-model-candidate`。

## 16. 页面设计

### 16.1 业务决策工作台

文件边界：

- `frontend/model-decisions.html`
- `frontend/model-decisions.js`
- `frontend/model-decisions.css`

必须包含：

- 待处理、已处理、全部页签及数量。
- 项目、类别、质量状态、时间筛选。
- 对象、候选模型、配准状态、处理人和处理时间摘要。
- 已有候选切换。
- 确认、拒绝、无匹配操作和原因输入。
- 空状态、加载状态、失败状态和并发冲突刷新提示。
- 对 `review_required`、`rejected` 和陈旧事项给出明确中文说明。

### 16.2 专业匹配工作台

文件边界：

- `frontend/model-matching-lab.html`
- `frontend/model-matching-lab.js`
- `frontend/model-matching-lab.css`

除业务清单外必须包含：

- Top-K 候选解释和模型版本。
- 配准配置、引擎版本、矩阵、双向覆盖率、残差、尺寸和门禁原因。
- 决策历史、绑定链和审计引用。
- 专家确认、重新配准、替换和恢复入口。
- `auditor` 的只读状态。

本阶段可以显示 Phase 15C 已生成的验证摘要与预览链接，但不建设完整统一三维查看器。

### 16.3 前端共享逻辑

候选排序、权限动作矩阵、状态标签和错误提示放入可独立测试的共享 JavaScript 模块。页面不得自行推断后端未返回的权限或绑定有效性。

## 17. 稳定错误

- `decision_item_not_found`
- `decision_not_found`
- `decision_conflict`
- `decision_not_allowed`
- `decision_reason_invalid`
- `registration_not_eligible`
- `binding_not_found`
- `binding_exists`
- `binding_stale`
- `binding_chain_invalid`
- `object_fingerprint_stale`
- `operation_busy`
- `publication_recovery_required`
- `artifact_integrity_failed`
- `permission_denied`
- `idempotency_conflict`
- `invalid_audit_request`
- `audit_persistence_error`

API 保持既有结构化错误信封，并把错误稳定映射到 400、403、404、409 或 503。领域层和 CLI 使用相同错误码。

## 18. 安全与威胁边界

- 标识符继续使用 Phase 15 严格验证器，不能进入任意路径。
- 只接受普通目录和普通文件；拒绝符号链接、junction、重解析点和路径身份变化。
- JSON 拒绝重复键、非有限数字、非规范字节和超限输入。
- 决策原因使用严格长度与字符边界，不能把秘密、原始请求或任意文件内容写入审计。
- 资源锁只建立在项目内固定锁根，锁后必须重新验证路径和当前头。
- 文件系统不能保证跨文件原子写入，因此以最后提交清单定义业务可见性，不能假定多个 rename 构成事务。
- 恢复只原位补齐同一操作的预期规范字节；不得围绕自动移动或递归删除增加 TOCTOU 补丁。

## 19. 测试策略

### 19.1 单元与契约

- 角色动作矩阵和 `verification_scope`。
- `passed`、`review_required`、`rejected` 和 `failed` 门禁。
- 决策、绑定和提交清单精确结构。
- 矩阵方向、来源配准指纹和模型版本绑定。
- `create`、`supersede`、`restore` 合法链与非法分叉。
- 当前状态、陈旧状态和决策头/绑定头指纹投影。

### 19.2 并发与恢复

- 两个主体基于同一头提交，只有一个成功。
- 决策、绑定、业务事件、提交清单和完成事件每个边界的故障注入。
- 相同请求重试原位完成且事件不重复。
- 不同请求不能接管未完成提交。
- 提交清单耐久化后的异常以耐久化结果为准。

### 19.3 完整性与安全

- owner、决定、绑定、提交清单或审计事件篡改。
- 对象、检索、配准、模型版本或表达在确认前变化。
- 目录穿越、符号链接、junction、普通文件替换目录和路径身份竞态。
- 不一致绑定链、多当前头、跨对象恢复和无效历史引用。

### 19.4 API、CLI 与页面

- API 身份优先于请求体读取、角色裁剪、分页和错误映射。
- CLI 与 API 契约一致。
- 业务页面三个页签、筛选、候选切换、按钮权限、空状态和冲突刷新。
- 专业页面技术详情、审计、重新配准、替换、恢复和审计员只读。
- 页面 DOM 与共享纯函数测试；服务集成使用浏览器端到端测试。
- 浏览器验收使用 Playwright Python 与 Chromium，文件为 `tests/browser/test_phase15d_workbenches.py`；测试夹具在临时项目启动真实 FastAPI 服务并同源提供前端，使用确定性配准引擎和服务端绑定的测试身份，不模拟决策 API 返回值。
- 用独立浏览器上下文验证 operator/expert/auditor；双页面同时读取同一事项后提交，检查赢家、409 冲突提示、刷新后状态，以及 API 直调权限拒绝。
- 阶段门禁必须执行业务确认/拒绝/无匹配、专家重新配准/替换/恢复、审计员只读和加载/空/失败状态。缺少浏览器或运行依赖时门禁失败，不得用静态测试通过或 skip 代替。
- 浏览器依赖放入独立 `browser-test` 可选依赖组，日常聚焦后端测试无需下载浏览器；现有 `.github/workflows/test.yml` 增加真实浏览器门禁，并保留失败截图与 trace。

### 19.5 端到端

完整链路：

```text
Phase 14 已发布对象
  -> Phase 15B-2 检索
  -> Phase 15C 配准
  -> Phase 15D 待办
  -> 人工确认
  -> 当前有效绑定
  -> 决策、绑定和审计历史查询
```

同时覆盖拒绝、无匹配、专家确认 `review_required`、多人冲突、对象变更后陈旧、替换和恢复。

## 20. 验收标准

1. 有效 Phase 15C 结果无需人工建任务即可出现在待处理页。
2. 业务页和专业页均提供待处理、已处理和全部清单或等价清晰入口。
3. `operator` 能确认 `passed`，不能确认 `review_required` 或触发新配准。
4. `expert` 能确认 `review_required`、重新配准、替换和恢复。
5. `auditor` 只能读取。
6. `rejected` 和 `failed` 永不形成绑定。
7. 确认提交不会公开只有决策或只有绑定的半成品。
8. 同时提交只有一个赢家，失败方获得稳定冲突并可刷新。
9. 绑定不可覆盖；替换和恢复形成可验证历史链。
10. 对象变化后旧绑定自动投影为 `stale`。
11. 所有自动化和人工写操作可通过审计操作追溯。
12. 页面、API、CLI、恢复、并发和端到端测试通过。
13. 全仓测试在阶段就绪门禁运行一次并通过。

补充回归门禁：暂停在 commit 与审计完成之间时另一个写者不能成功；进程在 owner/commit 后退出时不同编号请求不能绕过恢复阻断；成功响应丢失后同请求仍返回原结果；新增候选导致事项重新待办时普通确认不能新建第二条绑定根链；浏览器缺失不能产生绿色阶段验收。

## 21. 后续数据使用

Phase 15D 的不可变决定可以在后续生成：

- 身份正样本：确认的模型身份。
- 业务位姿样本：`identity` 或 `operational_pose`，不得视为精确位姿真值。
- 专家位姿样本：`expert_pose`。
- 困难负样本：被拒绝的高排名候选。
- 无匹配样本：模型库覆盖缺口。

本阶段只保证来源、权限、指纹和审计完整性，不创建训练集、不搜索参数、不自动推广配置。

## 22. 实施边界

实施按后端不可变原语、队列投影、决策与绑定服务、API/CLI、业务页面、专业页面、端到端验证的顺序推进。若同类持久化或并发缺陷两轮后仍未收敛，必须停止局部补丁并重新审视提交包架构。任务不得顺带实现 Phase 15E、Phase 15F、Phase 16 或 Phase 17。
