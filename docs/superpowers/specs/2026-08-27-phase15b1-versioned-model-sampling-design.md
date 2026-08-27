# Phase 15B-1 版本化模型发布与确定性采样设计

**日期：** 2026-08-27

**状态：** 已确认，待实施

**依据：** `docs/superpowers/specs/2026-07-22-phase15-model-library-retrieval-registration-design.md`

## 1. 目标

Phase 15B-1 在 Phase 15A 不可变 CAD 模型库之上补齐两项基础能力：

1. 使用追加式发布记录管理模型的当前生产版本、历史版本和用户回滚；
2. 从任意已验证的不可变 CAD 版本生成可复现、不可覆盖的米制表面采样点云。

这一阶段使后续检索明确区分当前生产版本与历史或实验版本，并为尺寸特征、局部描述子和刚性配准提供稳定的点云表达。

## 2. 范围

### 2.1 包含

- 模型版本发布历史和当前发布投影；
- 激活新版本和回滚到历史版本；
- 版本及发布历史查询；
- 模型级并发控制、乐观前置版本检查和全程审计；
- 确定性网格三角化、面积加权表面采样和米制输出；
- 多套不可变 `cad_sampled` 表达；
- 采样配置、输入、输出和工件指纹；
- 采样 CLI、发布/回滚 CLI 与发布/回滚 API；
- 失败恢复、篡改拒绝和端到端测试。

### 2.2 不包含

- 法向量估计、FPFH 或其他检索特征；
- 模型特征索引和 Top-K 候选检索；
- 点云对象特征、粗配准或 ICP；
- 删除、覆盖或原地修改任何已发布模型版本、发布记录或采样表达；
- 采样 API；采样先通过 CLI 和领域服务交付，后续与特征索引 API 一起开放；
- 自动选择或自动推广模型版本；
- 模型版本内容回写、模型缩放或几何修复。

## 3. 核心原则

1. **模型版本不可变。** `versions/<version_id>` 发布后保持字节级不变，派生工件不得写入该目录。
2. **回滚也是新历史。** 回滚创建新的发布记录，不删除、不覆盖、不重新激活旧记录。
3. **审计是真值。** 发布记录是不可变业务事实，`current_release.json` 是可重建查询投影。
4. **生产与实验分离。** 只有当前发布版本默认进入后续生产索引；历史版本仍可显式采样和实验。
5. **确定性优先。** 相同源网格、单位、算法版本、种子和点数必须生成相同字节和 SHA-256。
6. **失败关闭。** 输入、发布历史、采样工件或审计证据不一致时停止处理，不猜测、不自动修复事实记录。

### 3.1 威胁边界

- 项目存储目录属于可信命名空间，只允许平台服务账号和受控运维流程写入。资源锁用于协调遵守同一协议的平台进程。
- 取得锁时必须拒绝符号链接、重解析点、非普通目录和非普通文件，并验证路径与已打开描述符指向同一对象。
- 资源锁不承诺抵御拥有同等目录写权限的恶意本地进程、系统管理员或存储驱动在锁持有期间替换目录项。此类能力需要操作系统专用目录句柄、访问控制和服务隔离，应作为独立安全加固阶段设计。
- 部署时必须限制项目目录的写权限；检测到可见的路径或工件异常时继续失败关闭，不通过重复路径复查声称能够消除检查时与使用时竞态（TOCTOU）。

## 4. 存储布局

```text
models/<model_id>/
  model_asset.json
  versions/<version_id>/
    model_manifest.json
    source_geometry.json
    source/model.<ext>

  releases/<release_id>/
    operation_owner.json
    release.json
  current_release.json

  representations/<version_id>/cad_sampled/<representation_id>/
    operation_owner.json
    sampled_points.json
    representation.json

reports/model_matching_resource_locks/
  release-<resource_identity_sha256>.lock
  sampling-<resource_identity_sha256>.lock
```

`versions`、`releases` 和完整的 `representations` 条目不可覆盖。资源身份由 `resource_kind` 与标识符数组的 canonical JSON 计算 SHA-256，锁文件名使用完整摘要，避免外部标识符拼接造成 Windows 长路径。资源锁文件是永久协调工件。`current_release.json` 可以原子替换，因为它只是投影；任何读取都必须验证投影引用的发布记录。

## 5. 模型发布记录

### 5.1 `release.json`

每条发布记录包含且仅包含：

- `schema_version = "1.0"`
- `model_id`
- `release_id`
- `version_id`
- `action`：首发或升级使用 `activate`，回滚使用 `rollback`
- `previous_release_id`：首条记录为 `null`，其余必须指向当时的当前发布
- `rollback_of_release_id`：`rollback` 必须指向一次历史发布；`activate` 为 `null`
- `reason`：非空、去除首尾空白、最长 500 个 Unicode 字符
- `operation_id`
- `actor_id`
- `created_at`：取 canonical `operation.started` 的 UTC 时间
- `version_manifest_fingerprint`

发布记录目录由调用方提供的 `release_id` 标识。发布前必须验证模型、目标版本、目标版本清单、操作主体和前置发布状态。

### 5.2 `current_release.json`

当前发布投影包含：

- `schema_version = "1.0"`
- `model_id`
- `current_release_id`
- `current_version_id`
- `release_fingerprint`
- `updated_at`

读取当前版本时必须重新读取 `releases/<current_release_id>/release.json`，校验指纹、模型、版本和时间完全匹配。投影缺失、损坏或引用不存在时返回稳定完整性错误，不静默选择目录名称最大的记录。

### 5.3 激活与回滚

激活请求必须提供：

- `model_id`、`version_id`、`release_id`
- `expected_current_release_id`
- `reason`
- `operation_id`、`request_id`、`idempotency_key`

模型尚无当前发布时，`expected_current_release_id` 必须为 `null`。已有当前发布时必须精确匹配，否则返回 `stale_model_release`。

回滚请求还必须提供 `rollback_of_release_id`，并满足：

- 指向同一模型的历史发布；
- 目标 `version_id` 与该历史发布一致；
- 不能指向当前发布；
- 新记录的 `previous_release_id` 指向回滚前当前发布；
- 新记录的 `action` 为 `rollback`。

回滚完成后，历史版本文件和历史发布记录均保持原状。

## 6. 发布并发与恢复

### 6.1 锁顺序

写操作先启动 canonical 审计操作，再取得模型发布资源锁。在上述可信存储命名空间内，资源锁使用与 Phase 15A 相同的非阻塞内核字节锁语义：Windows 使用 `msvcrt`，POSIX 使用 `fcntl`。锁忙返回 `operation_busy`，不得用经过时间判断所有者死亡。

### 6.2 发布步骤

在模型发布锁内：

1. 验证模型资产、目标版本和当前投影；
2. 验证 `expected_current_release_id`；
3. 创建或验证 `releases/<release_id>/operation_owner.json`，冻结操作和请求身份；
4. 构造发布记录并通过 no-replace 原子发布 `releases/<release_id>/release.json`；
5. 原子替换 `current_release.json` 投影；
6. 写入 `model_release.published` 或 `model_release.rolled_back`；
7. 完成审计操作。

发布记录可见但投影替换或审计完成失败时，操作保持 `running` 并返回 `publication_recovery_required`。完全相同的幂等请求在锁内验证已发布记录后重建投影、补齐事件并完成原操作。不同请求不得接管既有 `release_id`。

临时文件使用唯一操作编号且不被读取路径视为业务事实。同步请求不执行递归删除、目录隔离或历史清理。

### 6.3 发布恢复状态机

模型锁内由唯一的只读状态分类器解释发布请求。领域写流程不得自行拼接 owner、release、projection 和 audit 的局部判断，也不能先用旧的 `expected_current_release_id` 拒绝本操作自身的恢复。

状态分类器输入以下已验证证据：

- 冻结请求及 canonical `operation.started`；
- `operation_owner.json` 的完整内容与发布可见性结果；
- `release.json` 的完整内容与发布可见性结果；
- `current_release.json` 投影；
- 已验证审计操作、事件链与完成结果；
- 当前模型全部不可变发布记录组成的前序关系图。

状态分类器只返回下列互斥状态之一：

1. **无候选。** 当前投影必须等于预期头，随后发布所有者信封。
2. **候选已归属、发布记录不可见。** 只有所有者信封与原审计操作完全匹配时，原操作可以继续；其他请求失败关闭。
3. **发布记录可见、投影仍为旧头。** 原操作验证不可变记录后推进投影；其他请求返回 `publication_recovery_required`，不得从旧头创建分叉。
4. **投影已指向本发布。** 原操作补齐唯一业务事件并完成审计，不得再次比较旧的预期头。
5. **投影已指向本发布的后继。** 原操作只补齐自身审计，不得覆盖较新投影；后继关系必须由完整发布链证明。
6. **操作已完成。** 发布记录、投影或合法后继、业务事件和完成结果全部匹配时，返回原不可变发布记录。

任何证据同时符合两个状态、无法归类，或内容之间不一致时，分类器失败关闭。写流程只能根据分类结果执行该状态允许的一个动作，随后重新分类；不能依赖内存布尔值判断磁盘事实。

`operation_owner.json` 与 `release.json` 都先写入并同步同目录临时文件，再通过原子 no-replace 发布最终路径。最终路径从不可见直接变为完整内容；若最终路径可见但目录同步结果未知，操作保持 `running` 并进入恢复，不得写入失败终态。

所有者信封与当前操作完全匹配，或发布记录已经在最终路径可见但归属尚不能安全证明时，当前操作不得进入 `failed`。恢复时从 canonical start、冻结请求、当前版本清单和清单指纹重新构造完整预期 owner 与 release，并执行逐字段相等比较；不得只比较调用方字段子集。

异常路径不得把“owner 结构合法但不等于当前预期”直接当作其他操作。候选归属必须由独立的四状态分类器判定：

1. **`ABSENT`。** owner 与 release 两个最终路径都被明确证明不存在；只有该状态允许把当前操作写为业务失败。
2. **`OWNED`。** owner 与当前 canonical 操作完整逐字段相等；出现持久化中断时保留当前操作 `running` 并由同一幂等请求恢复。
3. **`VERIFIED_FOREIGN`。** owner 指向另一操作，而且 owner、可见 release（如有）、另一操作的 verified canonical start、冻结请求指纹、主体、时间和清单指纹形成完整一致的闭环。只有证明该闭环后，当前请求才可以按发布编号冲突或待恢复外部发布失败；不得修改外部操作。
4. **`UNCERTAIN`。** 任一最终路径可见，但 owner/release 损坏、缺失、暂时不可读，或者无法证明属于当前或另一 verified canonical 操作。该状态失败关闭，保留当前操作 `running`，返回完整性错误或 `publication_recovery_required`，等待重试或人工审计。

`VERIFIED_FOREIGN` 不是“字典不相等”的同义词。owner 中任一合法字段被修改、foreign 审计链不可验证、release 与 foreign 请求指纹不一致，或可见 release 没有可证明的 owner 时，都必须归入 `UNCERTAIN`。

公开读取必须把每条发布记录绑定到已验证审计链：`operation.started` 的主体和时间、唯一发布或回滚业务事件、事件中的发布指纹，以及完成结果必须与 `release.json` 一致。

发布链验证不使用 `created_at` 推断先后关系。系统以 `previous_release_id` 构建有向关系图，并且必须同时满足：

- 恰好一个 `previous_release_id = null` 的根；
- 除头节点外，每条记录恰好有一个后继；
- 不存在环、孤立节点或分叉；
- 从根遍历一次恰好覆盖全部发布记录；
- `current_release.json` 精确指向图计算出的唯一头；
- 回滚引用指向遍历路径中更早的同模型、同版本记录，且不是回滚前的当前头。

`created_at` 与 `release_id` 只用于接口返回的稳定展示排序，不参与链合法性和当前头判定。即使两个操作启动时间相同、后启动者先取得锁，或系统时间发生回拨，追加链仍以 `previous_release_id` 为唯一依据。

### 6.4 失败审计规则

- owner/release 两个最终路径都被明确证明不存在时，业务失败必须写入与原错误完全一致的 `operation.failed`。
- `fail_operation` 返回异常时，必须重新读取操作并验证是否已以同一错误完成失败；无法证明时返回原稳定审计错误或 `audit_persistence_error`，不得静默返回业务错误。
- 候选归属为 `OWNED` 或 `UNCERTAIN` 时不调用 `fail_operation`；返回原完整性错误或 `publication_recovery_required` 并保留 `running`。
- 只有候选归属为 `VERIFIED_FOREIGN` 时，当前请求才可以记录自身失败；不得修改外部操作、外部候选或当前投影。

## 7. 版本查询

领域查询返回：

- 所有不可变版本，按 `version_id` 稳定排序；
- 每个版本的 `supersedes_version_id`、导入时间和清单指纹；
- `is_current`；
- 引用该版本的发布次数；
- 最近一次发布编号和动作；
- 完整发布历史，按 `created_at`、`release_id` 稳定排序。

现有 `GET /model-library/models/{model_id}` 增加 `current_release` 和 `release_history`。新增写接口：

```text
POST /model-library/models/<model_id>/releases
```

写接口只允许 `expert`，生产身份继续由 `X-API-Key` 绑定可信主体。请求体通过 `action` 区分 `activate` 与 `rollback`。CLI 提供：

```text
release-model-version
list-model-releases
```

## 8. 采样配置与身份

采样请求必须提供：

- `model_id`
- `version_id`
- `point_count`：整数，范围 `1..500000`
- `random_seed`：整数，范围 `0..9223372036854775807`
- `operation_id`、`request_id`、`idempotency_key`

采样配置固定为：

```json
{
  "schema_version": "1.0",
  "algorithm": "sha256_area_weighted_v1",
  "point_count": 50000,
  "random_seed": 20260827,
  "coordinate_unit": "m",
  "coordinate_precision_decimals": 12
}
```

示例中的点数和种子不构成默认值；CLI 要求调用方显式提供。配置使用 canonical JSON 计算 SHA-256。表达编号由系统确定为：

```text
cad-sampled-<完整配置指纹>
```

同一模型版本和同一配置只能对应一个表达目录。配置变化必然产生新的表达编号。

## 9. 确定性表面采样

### 9.1 输入

采样必须调用 Phase 15A 的验证读取接口，确认：

- 模型版本清单结构、身份和审计绑定有效；
- 原始源文件与 `source_geometry.json` 指纹有效；
- 解析后的顶点和面索引合法；
- 声明单位和米制换算与清单一致。

采样允许显式处理当前版本或历史版本。后续生产索引默认只消费当前发布版本，但不限制历史版本生成实验表达。

### 9.2 三角化与面积

- 面按源文件稳定顺序处理；
- 三角面保持不变；
- 四边形 `[v0,v1,v2,v3]` 固定展开为 `[v0,v1,v2]`、`[v0,v2,v3]`；更多顶点继续以 `v0` 为扇心按源索引顺序展开；
- 顶点先按清单比例转换为米；
- 三角形面积使用三维叉积计算；
- 面积为零的退化三角形忽略；
- 所有三角形均退化时返回 `invalid_model_geometry`；
- 不自动修复自交、翻转法向或非流形拓扑。

### 9.3 伪随机序列

算法不依赖 Python `random` 的实现细节。第 `i` 个点的三个均匀值分别取自：

```text
SHA256("phase15b1" || config_fingerprint_raw_32_bytes || uint64_be(i) || lane)
```

`lane` 分别为 `0`、`1`、`2`。摘要前 8 字节按无符号大端整数解释，再除以 `2^64`，得到 `[0,1)`。

- lane 0 按累计面积选择三角形；
- lane 1 和 lane 2 使用平方根重参数化生成均匀重心坐标；
- 输出坐标四舍五入到小数点后 12 位；
- `-0.0` 规范化为 `0.0`；
- 点保持采样编号顺序，不另行空间排序。

### 9.4 `sampled_points.json`

采样点工件包含：

- `schema_version = "1.0"`
- `coordinate_unit = "m"`
- `point_count`
- `points`：长度等于 `point_count` 的三维有限浮点数组

文件使用现有稳定 JSON 写出规则。Phase 15B-1 不写法向量、颜色、标签或描述子。

### 9.5 `representation.json`

表达清单包含：

- `schema_version = "1.0"`
- `representation_id`
- `representation_type = "cad_sampled"`
- `model_id`、`source_version_id`
- `source_manifest_fingerprint`
- `source_geometry_fingerprint`
- `geometry_fingerprint`：`sampled_points.json` 的 SHA-256
- `generation_config` 和 `generation_config_fingerprint`
- `point_count`、`coordinate_unit`
- `artifact_uri = "sampled_points.json"`
- `operation_id`、`generated_by`、`generated_at`
- `status = "ready"`

读取表达时必须验证目录路径绑定、结构、配置指纹、点数组、源版本当前完整性和采样工件指纹。

## 10. 采样发布与恢复

表达目录是一个原位恢复的 canonical 候选：

1. 在采样资源锁内创建表达目录；
2. 写入 `operation_owner.json`，冻结操作、模型、版本和配置指纹；
3. 写入 `sampled_points.json`；
4. 最后 no-replace 发布 `representation.json` 作为可见性标记；
5. 写入 `model_sampling.completed` 并完成审计操作。

读取方只枚举具有完整、验证通过的 `representation.json` 的目录。中断留下的候选目录不被视为表达；完全相同的操作重试时验证 owner 和已有字节并原位继续。不同操作遇到未完成候选返回 `operation_busy` 或完整性错误，不删除、不接管。

若表达清单已发布但审计完成失败，返回 `publication_recovery_required`；相同幂等请求验证工件和事件后完成原操作。已完成的相同配置请求返回既有表达，不重复采样。

## 11. 审计事件

发布与回滚：

- `operation.started`
- `model_release.prepared`
- `model_release.published` 或 `model_release.rolled_back`
- `operation.completed`

采样：

- `operation.started`
- `model_sampling.source_verified`
- `model_sampling.points_generated`
- `model_sampling.representation_published`
- `operation.completed`

失败使用现有 `operation.failed` 或 `operation.start_failed`。事件详情记录模型、版本、发布或表达编号、输入指纹、配置指纹、随机种子、点数、工件指纹和自动动作原因，不记录 API token。

## 12. 稳定错误码

- `model_release_not_found`
- `model_release_exists`
- `model_release_integrity_error`
- `stale_model_release`
- `invalid_model_release`
- `invalid_sampling_config`
- `model_representation_not_found`
- `model_representation_exists`
- `model_representation_integrity_error`
- `mesh_engine_unavailable`
- `operation_busy`
- `idempotency_conflict`
- `publication_recovery_required`
- `audit_integrity_error`
- `audit_persistence_error`

Phase 15A 的 `model_asset_not_found`、`model_version_not_found`、`model_version_integrity_error`、`invalid_model_geometry` 和权限错误继续复用。

## 13. 权限与用户操作

- 查询模型版本、当前发布和发布历史：公开只读；
- 激活、回滚和采样：`expert`；
- 读取完整审计快照：`auditor`；
- 生产模式不接受调用方伪造 actor 或 roles；
- 回滚请求必须提交非空原因；
- 前端版本历史和回滚界面不在本阶段实现，但 API 契约支持后续简单操作界面。

## 14. 测试与验收

### 14.1 版本发布

- 首次激活、升级和回滚生成正确追加历史；
- 回滚不修改任何历史版本或发布记录；
- 陈旧 `expected_current_release_id` 被拒绝；
- 并发写只有一个请求能推进指定当前发布；
- 投影丢失、篡改或引用错误失败关闭；
- 发布后中断能够通过相同请求恢复；
- 不同请求不能复用发布编号或幂等键；
- CLI、API 和领域服务共享同一规则。

### 14.2 确定性采样

- 同输入同配置产生字节相同的点云和表达清单；
- 不同种子或点数产生不同配置指纹和表达编号；
- 单三角形样本全部位于三角形内；
- 不同面积三角形按固定手算样例选择；
- `mm`、`cm` 和 `m` 转换正确；
- 多边形三角化顺序固定；
- 部分退化面被忽略，全部退化稳定失败；
- 点数和种子边界被验证；
- 源版本或采样工件篡改被拒绝；
- 中断候选原位恢复，不覆盖完整表达；
- 所有成功和失败操作具有有效审计哈希链。

### 14.3 阶段验收

- 用户可以查看模型全部版本、发布历史和当前版本；
- 用户可以通过受审计操作回滚到历史版本；
- 已发布模型版本目录在激活、回滚和采样前后字节不变；
- 任意已验证版本可以生成多套不可变确定性采样表达；
- 当前生产版本与历史实验版本能够明确区分；
- Phase 15A、Phase 14 及全仓测试保持通过；
- 文档不宣称已完成特征索引、检索或配准。

## 15. 后续接口

Phase 15B-2 读取当前发布版本的 `cad_sampled` 表达，计算尺寸、形状和局部几何特征，并建立版本化特征索引。历史版本表达只有在实验请求明确指定时进入 Challenger 索引。Phase 15C 使用同一表达和特征配置执行刚性配准。
