# Phase 15B-2 版本化特征与混合模型检索

## 1. 功能定位

Phase 15B-2 将 Phase 14 已发布的分割对象转换为可审计的模型检索输入，并从 Phase 15B-1 模型版本及确定性采样表达中返回可解释的 Top-K 候选。第一版采用精确、确定、可回放的混合评分，不执行刚性配准，也不会自动绑定模型。

完整生产链路如下：

```text
Phase 14 审查并发布对象
  → 发布版本化检索配置
  → 为当前模型版本生成/复用采样表达与特征
  → 构建不可变生产索引
  → 激活索引发布
  → 提取对象特征
  → 类别过滤或安全降级
  → 精确 Top-K 评分
  → 保存报告、评分解释和哈希链审计
```

实验链路可以读取 Phase 13A 已完成分割运行，并显式选择 Challenger 索引；实验索引不会成为生产当前投影。

## 2. 输入信任与 Phase 14 兼容

### 2.1 新 Phase 14 发布

新发布包含 `object_review_evidence.json`。系统会逐对象核对：

- 对象是否经过人工确认；
- 类别来源是否为人工确认；
- 对象点、标签、发布修订和来源指纹是否一致；
- 发布目录、对象审查证据和 Phase 14 工件是否未被修改。

只有审查证据完整、分类来源可信、类别映射存在且索引内确有该类别候选时，生产检索才应用类别硬过滤。

### 2.2 旧 Phase 14 发布

旧发布可能没有对象级审查证据。系统不会回写历史目录，而是将对象标记为 `legacy_unknown`，关闭类别硬过滤，并对全部候选执行软评分。报告中的 `category_filter.reason` 为 `category_filter_not_trusted`，便于审计人员区分兼容路径。

如果新发布声明了审查证据但文件缺失、内容被修改或指纹不一致，系统会拒绝输入，不会将其降级为旧发布。

### 2.3 Phase 13A 实验输入

Phase 13A 输入必须来自状态为 `completed` 的分割运行。它只允许用于显式 Challenger 检索，类别不作为可信硬过滤依据。生产请求不得借用 Challenger 索引。

## 3. 配置、特征与索引

### 3.1 版本化配置

每个 `config_id` 绑定三份不可变配置：

- 特征配置：采样算法、点数、随机种子、径向直方图、体素网格和退化阈值；
- 评分配置：Top-K 范围、生产覆盖率门槛、六类评分权重和尺寸非对称惩罚；
- 类别映射：把分割类别映射到模型库类别。

同一个配置编号只能重放完全相同的内容。需要调整参数时应发布新编号，不得覆盖旧配置。

```powershell
$env:PYTHONPATH="src"
python -m pc_system.cli create-model-retrieval-config `
  --project-root .\workspace `
  --config-id retrieval-v1 `
  --feature .\config\retrieval-feature-v1.json `
  --scoring .\config\retrieval-scoring-v1.json `
  --category-mapping .\config\retrieval-category-map-v1.json `
  --actor alice `
  --operation-id op-config-001 `
  --request-id req-config-001 `
  --idempotency-key idem-config-001
```

### 3.2 同构几何特征

模型和对象使用同一算法生成：

- 米制三轴观测跨度和比例；
- 观测包围盒体积；
- 主轴特征值比例；
- 径向分布直方图；
- 规范体素占用率；
- 点数、可用状态和退化原因。

算法先验证有限坐标和点数边界，再进行确定性归一化。几何完全退化时保留元数据评分能力，并在候选风险中标记 `metadata_only` 或具体退化原因。

### 3.3 生产索引

生产索引只读取每个模型当前激活的版本。若缺少匹配采样配置的 `cad_sampled` 表达，构建过程会创建可追溯子操作并自动采样。自动操作同样记录主体、请求、幂等键、结果和哈希链事件。

```powershell
python -m pc_system.cli build-model-feature-index `
  --project-root .\workspace `
  --index-id index-production-001 `
  --index-mode production `
  --config-id retrieval-v1 `
  --actor alice `
  --operation-id op-index-001 `
  --request-id req-index-001 `
  --idempotency-key idem-index-001
```

索引报告区分符合条件模型、已索引模型、无当前发布模型和排除项，并计算覆盖率。低于 `production_minimum_coverage` 的索引可以检查，但不能激活。

### 3.4 Challenger 索引

Challenger 只能包含调用方明确列出的历史发布，不能设置生产投影。历史选择文件示例：

```json
[
  {"model_id": "pump-a", "release_id": "release-pump-v1"}
]
```

```powershell
python -m pc_system.cli build-model-feature-index `
  --project-root .\workspace `
  --index-id index-challenger-001 `
  --index-mode challenger `
  --config-id retrieval-v1 `
  --historical-releases .\config\challenger-releases.json `
  --actor alice `
  --operation-id op-challenger-001 `
  --request-id req-challenger-001 `
  --idempotency-key idem-challenger-001
```

## 4. 索引激活、过期、历史与回滚

激活生产索引必须提供新的发布编号。首次激活可省略预期当前发布；升级时必须传入已知的当前发布编号，防止并发覆盖。

```powershell
python -m pc_system.cli release-model-feature-index `
  --project-root .\workspace `
  --index-id index-production-001 `
  --release-id index-release-001 `
  --action activate `
  --reason "首个生产检索索引" `
  --actor alice `
  --operation-id op-index-release-001 `
  --request-id req-index-release-001 `
  --idempotency-key idem-index-release-001
```

模型当前发布变化后，既有生产索引返回 `model_index_stale`。正确处理方式是构建并激活新索引；不得修改旧快照。

查询历史：

```powershell
python -m pc_system.cli list-model-feature-indexes --project-root .\workspace
python -m pc_system.cli list-model-feature-index-releases --project-root .\workspace
```

受审计回滚会创建新的发布记录，并指向历史索引：

```powershell
python -m pc_system.cli release-model-feature-index `
  --project-root .\workspace `
  --index-id index-production-001 `
  --release-id index-release-003 `
  --action rollback `
  --expected-current-release-id index-release-002 `
  --rollback-of-release-id index-release-001 `
  --reason "回滚到已验证索引" `
  --actor alice `
  --operation-id op-index-release-003 `
  --request-id req-index-release-003 `
  --idempotency-key idem-index-release-003
```

历史检索报告始终验证其生成时引用的不可变索引发布，不因后来激活或回滚而失效。

## 5. 生产与实验检索

### 5.1 生产 CLI

不传 `--index-id` 时选择当前生产索引。`--index-release-id` 可用于要求当前发布必须等于调用方已知编号。

```powershell
python -m pc_system.cli retrieve-model-candidates `
  --project-root .\workspace `
  --retrieval-run-id retrieval-001 `
  --source-kind correction_release `
  --asset-id scan-a `
  --source-id release-001 `
  --instance-id pump-001 `
  --index-release-id index-release-001 `
  --top-k 10 `
  --keyword pump `
  --tag industrial `
  --hint-source human `
  --actor alice `
  --operation-id op-retrieval-001 `
  --request-id req-retrieval-001 `
  --idempotency-key idem-retrieval-001
```

人工或上游系统提供任何关键字、标签、厂商或型号提示时，必须声明 `hint_source=human` 或 `hint_source=upstream_system`；无提示时不得伪造来源。

### 5.2 实验 CLI

实验请求必须使用 `source_kind=segmentation_run` 并显式指定 Challenger `--index-id`，同时不得指定索引发布编号。

```powershell
python -m pc_system.cli retrieve-model-candidates `
  --project-root .\workspace `
  --retrieval-run-id retrieval-exp-001 `
  --source-kind segmentation_run `
  --asset-id scan-a `
  --source-id run-001 `
  --instance-id pump-001 `
  --index-id index-challenger-001 `
  --top-k 10 `
  --actor alice `
  --operation-id op-retrieval-exp-001 `
  --request-id req-retrieval-exp-001 `
  --idempotency-key idem-retrieval-exp-001
```

读取已验证报告：

```powershell
python -m pc_system.cli show-model-retrieval `
  --project-root .\workspace `
  --asset-id scan-a `
  --source-id release-001 `
  --instance-id pump-001 `
  --retrieval-run-id retrieval-001
```

### 5.3 API

生产模式写接口要求 `expert` token；配置、索引和发布列表允许 `expert` 或 `auditor`；审计快照只允许 `auditor`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/model-matching/retrieval-configs` | 发布检索配置 |
| `GET` | `/model-matching/retrieval-configs` | 列出配置 |
| `POST` | `/model-matching/feature-indexes` | 构建生产或 Challenger 索引 |
| `GET` | `/model-matching/feature-indexes` | 列出索引 |
| `POST` | `/model-matching/feature-index-releases` | 激活或回滚生产索引 |
| `GET` | `/model-matching/feature-index-releases` | 查询发布历史 |
| `POST` | `/model-matching/retrievals` | 执行生产或实验检索 |
| `GET` | `/model-matching/retrievals/{asset_id}/{source_id}/{instance_id}/{retrieval_run_id}` | 读取已验证报告 |

PowerShell 请求示例：

```powershell
$headers = @{"X-API-Key" = "<expert-token>"}
$body = @{
  retrieval_run_id = "retrieval-001"
  source_kind = "correction_release"
  asset_id = "scan-a"
  source_id = "release-001"
  instance_id = "pump-001"
  index_release_id = $null
  index_id = $null
  top_k = 10
  keywords = @()
  tags = @()
  manufacturer = $null
  model_number = $null
  hint_source = $null
  operation_id = "op-retrieval-001"
  request_id = "req-retrieval-001"
  idempotency_key = "idem-retrieval-001"
} | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/model-matching/retrievals `
  -Headers $headers -ContentType application/json -Body $body
```

## 6. 评分解释、降级与风险

候选总分由可用分量加权组成：

- `category`：类别一致性；
- `terms`：关键字和标签词元重合，标签权重更高；
- `manufacturer_model`：厂商和型号一致性；
- `dimensions`：三轴尺寸误差，对模型小于实物施加更强惩罚；
- `shape`：主轴比例和径向分布相似度；
- `occupancy`：体素占用率接近程度。

报告为每个候选保存 `components`、`effective_weights` 和 `risks`。缺失分量会被移除，其余权重重新归一化；不会把缺失值当成零分。类别过滤为空时扫描全部候选并记录 `category_filter_empty`。所有候选均无有效评分分量时返回稳定的 `no_candidate_models`，相同请求重放得到相同错误终态。

稳定领域错误包括：

- `invalid_retrieval_input`：参数组合、Top-K 或提示来源非法；
- `retrieval_object_not_found`：对象或检索运行不存在；
- `object_review_evidence_invalid`：新发布的审查证据不完整或不一致；
- `model_index_coverage_rejected`：生产索引覆盖率不足；
- `model_index_stale`：模型当前头已变化；
- `model_index_release_not_found`：生产发布不可用或与预期不一致；
- `no_candidate_models`：没有可评分候选；
- `feature_integrity_error`、`model_index_integrity_error`：不可变工件或证据校验失败；
- `operation_busy`、`publication_recovery_required`：需按原标识恢复的并发或中断状态。

## 7. 审计与中断恢复

配置、自动采样、特征发布、索引构建、索引激活/回滚和检索运行都使用 Phase 15 哈希链操作账本。审计员可读取一致快照：

```powershell
$headers = @{"X-API-Key" = "<auditor-token>"}
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/audit/operations/op-retrieval-001 `
  -Headers $headers
```

恢复规则：

1. 保留原始 `operation_id`、`request_id`、`idempotency_key` 和完全相同的请求体。
2. 遇到 `operation_busy` 时等待竞争操作收敛后，用原请求重试。
3. 遇到 `publication_recovery_required` 时用原请求重试，让系统核对已可见工件并补齐审计终态。
4. 不手工删除、移动、覆盖或接管候选目录。
5. 参数或请求体变化必须使用新的三项标识，不能复用旧幂等键。

系统采用原位恢复和失败关闭策略；不在业务请求路径中自动递归清理不确定目录。

## 8. 工件位置

```text
models/retrieval_configs/<config_id>/
models/features/models/<model_id>/<version_id>/<feature_id>/
reports/model_features/objects/<asset_id>/<source_id>/<instance_id>/<feature_id>/
models/feature_indexes/<index_id>/
models/feature_index_releases/<release_id>/
models/current_feature_index.json
reports/model_retrieval/<asset_id>/<source_id>/<instance_id>/<retrieval_run_id>/
reports/model_matching_operations/<operation_id>/
```

索引、发布、对象特征和检索报告均为不可覆盖快照。读取接口会复核结构、资源边界、指纹、发布证据和审计链。

## 9. 当前边界与后续阶段

- Phase 15C：消费 Top-K 候选与采样表达，执行刚性粗配准、多尺度 ICP 和残差质量门禁；不由本阶段生成位姿。
- Phase 15D：保存人工确认、换候选、拒绝和对象—模型绑定；本阶段只提供候选，不自动绑定。
- Phase 15E：引入经过验证的实物参考点云模板，并与 `cad_sampled` 明确区分来源。
- Phase 15F：聚合真实人工决策，执行受控参数搜索、Champion/Challenger 评估、独立审批、推广和回滚；本阶段不在线学习，也不自动改写生产权重。

因此，Phase 15B-2 已完成“检索与解释”，但不代表模型配准、业务绑定或系统自训练已经交付。
