# Phase 15 模型库、候选检索与刚性配准设计

日期：2026-07-22

状态：书面规格待审核

依赖：Phase 13A 分割运行、Phase 13B 黄金评估、Phase 14 人工纠正与发布

## 1. 目标

Phase 15 把经过分割或人工纠正的单对象点云，与版本化三维模型库中的候选模型进行检索和刚性配准，形成可确认、可审计、可恢复的对象—模型绑定。

完整闭环为：

```text
已发布的分割对象
  -> 关键字、标签和几何特征混合检索
  -> 带解释的 Top-K 候选
  -> 刚性粗配准
  -> 刚性精配准
  -> 残差与覆盖率门禁
  -> 人员确认、拒绝或标记无匹配
  -> 不可变模型绑定与反馈数据
  -> 受控 Champion/Challenger 优化
```

本阶段必须坚持平台的三层原则：

1. 点云是尺寸、位置和残差判断的几何真值。
2. 匹配模型承担对象语义、仿真和设备管理职责。
3. 模型的位置与尺寸必须能够回查原始点云、对象点成员和配准残差。

## 2. 已确认决策

- 先支持 CAD/网格模型，再支持实物参考点云模板。
- 首批网格格式为 STL、OBJ 和 PLY。
- 网格入库后生成标准化表面采样点云，用于检索和配准。
- 配准严格限制为旋转和平移，不允许统一缩放、非等比例缩放、错切或形变。
- 模型导入时处理毫米、厘米和米等单位，内部计算统一使用米。
- 候选检索采用关键字、标签、类别、厂商、型号和几何特征的混合策略。
- 标签低可信时自动退化为几何检索，避免分割分类错误造成硬性漏检。
- 系统自动检索、配准和评分，人员负责确认、拒绝或选择其他候选。
- 所有自动化和人工操作必须记录，能够按操作、对象、模型和配置版本审计。
- 自动优化只产生 Challenger，不自动替换生产 Champion。
- 默认界面面向业务操作人员，专业界面面向算法、模型库和审计人员。

## 3. 范围

Phase 15 包含：

- 版本化模型资产、模型版本和多表达清单。
- STL、OBJ、PLY 网格验证、单位归一化和不可变入库。
- 网格表面采样、几何摘要和特征计算。
- 关键字、标签、元数据和几何特征索引。
- 可解释的 Top-K 候选检索。
- Open3D 生产适配器边界。
- FPFH 与 RANSAC/FGR 粗配准。
- 多尺度 point-to-plane ICP 精配准。
- 严格刚性矩阵验证。
- 双向残差、覆盖率、尺寸一致性和对称歧义门禁。
- 人工确认、拒绝、无匹配和重新匹配。
- 不可变模型绑定、版本失效和恢复语义。
- 实物参考点云模板及统一模板接口。
- 有界、可复现的检索权重、配准参数和门槛优化。
- Champion/Challenger 比较、职责分离审批和回滚。
- 双角色前端、API、CLI、文档和测试。
- 所有自动化和人工动作的不可变审计事件。

## 4. 非目标

Phase 15 不包含：

- 在线直接训练或部署学习型点云特征网络。
- 未经验证自动替换生产检索或配准配置。
- 非刚性配准、模型变形或尺寸拟合。
- STEP、IGES 等原生 CAD/B-Rep 解析；可由后续外部转换器生成受支持网格。
- 自动生成缺失的 CAD 模型。
- 点云、高斯泼溅和语义模型的完整统一查看器；该能力属于 Phase 16。
- 分割模型重训练、漂移检测和自动生产推广；该能力属于 Phase 17。
- 跨时间设备身份解析、设备全生命周期管理或仿真执行。
- 同一对象的实时多人共同编辑。

## 5. 交付里程碑

### P15-M1 模型库与 CAD 入库

- 建立模型身份、版本、来源、单位、许可证和标签契约。
- 支持 STL、OBJ 和 PLY。
- 原始版本不可覆盖，修订产生新版本。
- 生成源文件、清单和配置指纹。

### P15-M2 标准化、特征与混合检索

- 对网格生成确定性的表面采样点云。
- 计算尺寸、比例、体积、主轴、占用率和形状描述子。
- 建立元数据与几何特征索引。
- 对一个分割对象输出带解释的 Top-K 候选。

### P15-M3 刚性粗配准、精配准与门禁

- 建立配准引擎接口和 Open3D 适配器。
- 支持有限初始姿态、FPFH、RANSAC/FGR 和多尺度 ICP。
- 输出刚性变换、双向残差、覆盖率和门禁结论。

### P15-M4 人工决策、模型绑定与双界面

- 业务界面支持确认、换候选、拒绝和无匹配。
- 专业界面支持模型管理、技术详情、重试和审计。
- 确认结果形成不可变绑定，变更通过 supersede 生成新版本。

### P15-M5 实物参考点云模板

- 支持经过验证的单对象参考点云入库。
- 使用与 CAD 派生点云相同的模板、特征和配准接口。
- 保留 `cad_sampled` 与 `scanned_reference` 来源差异。

### P15-M6 受控自动优化

- 对检索权重、配准参数和门槛执行有界参数搜索。
- 生成 Challenger、比较报告和升级建议。
- 实施独立审批、黄金回归门禁和回滚。

## 6. 核心组件

### 6.1 模型库 `model_library`

负责模型身份、版本、表达、来源、单位、许可证、业务元数据和不可变状态。

### 6.2 模型标准化 `model_preprocessing`

负责网格验证、坐标有限性检查、单位归一化、表面采样和标准几何摘要。所有随机采样必须使用记录在配置中的固定种子。

### 6.3 特征索引 `model_feature_index`

负责版本化特征配置、特征工件、文本/标签索引、几何索引和索引发布状态。索引内部实现可以替换，但公共特征契约保持稳定。

### 6.4 候选检索 `model_retrieval`

负责快速过滤、混合评分、Top-K 排序、评分解释和降级策略。检索运行不可覆盖。

### 6.5 配准引擎 `model_registration`

负责统一的粗配准与精配准适配器协议。首个生产引擎使用 Open3D；核心测试使用确定性轻量替身，不要求安装重型依赖。

### 6.6 残差门禁 `registration_quality`

负责刚性矩阵验证、残差、覆盖率、尺寸一致性、对称歧义和三级门禁结论。

### 6.7 模型绑定 `model_binding`

负责人类决定、对象—模型—配准关系、历史版本、过期检测、supersede 和恢复。

### 6.8 受控优化 `model_matching_optimization`

负责有界实验、固定种子、训练/验证/黄金分区、Champion/Challenger 比较、审批和回滚。

### 6.9 审计账本 `model_matching_audit`

负责自动化与人工事件、哈希链、操作状态投影、查询和重放。业务实体只能引用审计操作，不能伪造审计身份。

## 7. 数据模型

### 7.1 `model_asset`

一个稳定的设备或模型身份：

- `model_id`
- `display_name`
- `category_id`
- `manufacturer`
- `model_number`
- `keywords`
- `tags`
- `lifecycle_status`
- `created_by`
- `created_at`

### 7.2 `model_version`

一个不可变模型版本：

- `model_id`
- `version_id`
- `source_format`
- `source_uri`
- `source_fingerprint`
- `declared_unit`
- `coordinate_unit = m`
- `import_transform`
- `license`
- `provenance`
- `status`
- `supersedes_version_id`
- `artifacts`

### 7.3 `model_representation`

同一模型版本的具体表达：

- `representation_id`
- `representation_type`：`cad_mesh`、`cad_sampled` 或 `scanned_reference`
- `source_version_id`
- `point_count` 或网格统计
- `coordinate_unit`
- `geometry_fingerprint`
- `generation_config_fingerprint`
- `artifact_uri`

### 7.4 `feature_profile`

- `feature_profile_id`
- `representation_id`
- `feature_schema_version`
- `feature_engine`
- `engine_version`
- `config`
- `config_fingerprint`
- `random_seed`
- `geometry_summary`
- `descriptor_artifacts`
- `status`

### 7.5 `retrieval_run`

- `retrieval_id`
- `asset_id`
- `object_id`
- `object_fingerprint`
- `source_release_id` 或 `segmentation_run_id`
- `feature_index_version`
- `retrieval_config_version`
- `query_terms`、`query_tags` 和标签可信度
- `candidates`
- `score_explanations`
- `status`
- `operation_id`

### 7.6 `registration_run`

- `registration_id`
- `retrieval_id`
- `candidate_model_id`
- `candidate_version_id`
- `candidate_representation_id`
- `object_fingerprint`
- `engine` 和 `engine_version`
- `coarse_config`、`fine_config` 和指纹
- `initial_hypothesis`
- `rigid_transform_4x4`
- `coarse_metrics`
- `fine_metrics`
- `residual_metrics`
- `gate_status`
- `gate_reasons`
- `artifacts`
- `operation_id`

### 7.7 `model_binding`

- 只在人员执行 `confirm` 后创建；拒绝和无匹配不能创建绑定。
- `binding_id`
- `asset_id`
- `object_id`
- `object_fingerprint`
- `model_id`
- `model_version_id`
- `representation_id`
- `registration_id`
- `decision_id`
- `decided_by`
- `decided_at`
- `status`：`active`、`stale` 或 `superseded`
- `supersedes_binding_id`
- `operation_id`

### 7.8 `match_decision`

- `decision_id`
- `match_id`
- `registration_id`，在 `no_match` 时可以为空
- `decision`：`confirmed`、`rejected` 或 `no_match`
- `decision_reason`
- `verification_scope`：`identity`、`operational_pose` 或 `expert_pose`
- `decided_by`
- `decider_roles`
- `decided_at`
- `object_fingerprint`
- `operation_id`

拒绝和无匹配只生成决策及反馈。确认生成决策，并在同一原子业务操作中创建引用该决策的 `model_binding`。

### 7.9 `optimization_experiment`

- `experiment_id`
- 数据集和分区指纹
- Champion 配置版本
- 搜索空间、预算和固定随机种子
- 试验列表和失败状态
- 检索、配准、安全和分组指标
- Challenger 配置和比较结论
- 审批状态、审批人和回滚引用
- `operation_id`

## 8. 对象身份与失效

检索输入优先级为：

1. Phase 14 已发布人工纠正对象。
2. Phase 13 已完成且通过质量门禁的分割对象。
3. 未通过门禁的分割对象只能进入实验检索，不能建立正式绑定。

`object_fingerprint` 必须由以下内容稳定生成：

- 原始点云指纹。
- 分割运行或纠正发布版本。
- 排序后的源点索引。
- 对象类别与坐标单位。

对象点成员变化后，旧检索、旧配准和旧绑定保留，但绑定状态自动投影为 `stale`。系统不得在对象变化后继续把旧变换描述为当前有效结果。

## 9. 工件布局

```text
models/<model_id>/versions/<version_id>/
  model_manifest.json
  source/
  derived/
    sampled_points.json
    geometry_summary.json
    feature_profile.json

reports/model_retrievals/<asset_id>/<object_id>/<retrieval_id>/
  retrieval_run.json
  candidates.json
  score_explanations.json

reports/model_registrations/<asset_id>/<object_id>/<registration_id>/
  registration_run.json
  coarse_result.json
  fine_result.json
  residual_report.json
  transformed_preview.json

reports/model_bindings/<asset_id>/<object_id>/<binding_id>/
  binding.json
  decision.json
  lineage.json

datasets/model_matching_feedback/<dataset_version>/
  dataset_manifest.json
  decisions.jsonl
  identity_examples.jsonl
  pose_examples.jsonl

reports/model_optimizations/<experiment_id>/
  experiment.json
  trials.jsonl
  comparison.json
  recommendation.json
  approval.json

reports/model_matching_operations/<operation_id>/
  operation.json
  events.jsonl
  artifact_index.json
```

操作事件是审计真值；可查询的任务状态和审计索引是可重建投影。

## 10. 模型入库与标准化

入库执行：

1. 验证模型、版本、格式、来源、单位和许可证。
2. 计算原文件指纹并建立暂存目录。
3. 解析网格并拒绝空网格、非法坐标和无法解释的单位。
4. 保存原始坐标系和显式导入变换。
5. 统一转换为米，不改变物体比例。
6. 计算包围盒、主轴、体积或近似体积、表面积和拓扑摘要。
7. 使用固定种子和版本化参数采样表面点云。
8. 生成几何特征和索引工件。
9. 原子发布模型版本和索引状态。

任一步骤失败都必须删除未发布暂存工件，但保留失败审计事件。已发布版本不可修改或删除；业务停用通过状态变更完成。

## 11. 混合候选检索

### 11.1 快速过滤

- 模型必须具有已发布、兼容的特征工件。
- 物理尺寸超过配置容差时过滤，因为刚性配准不能修正尺寸。
- 可信的精确型号冲突可以过滤。
- 高可信类别可参与过滤；低可信或缺失类别只能参与加权。
- 许可证或业务状态不允许使用的模型必须过滤。

### 11.2 混合评分

评分输入包括：

- 规范化关键字。
- 标签和类别。
- 厂商和型号。
- 包围盒尺寸、长宽高比例和体积。
- 主轴、占用率和全局形状描述子。
- 历史确认率、拒绝率和混淆关系。

每个候选输出总分、各分量、加权原因、过滤/降级原因和使用的配置版本。评分不能只返回不可解释的单一置信度。

### 11.3 降级语义

- 类别可信：类别可以过滤并加权。
- 类别低可信：类别只加权。
- 类别缺失：几何与其他元数据继续检索。
- 几何特征不可用：运行失败，不得仅凭文本建立正式绑定。

## 12. 刚性配准

### 12.1 预处理

- 对对象点云和模型模板执行版本化多尺度降采样。
- 估计法向量和 FPFH。
- 根据模型主轴与已声明对称性生成有限初始姿态。
- 保存每个初始姿态和被淘汰原因。

### 12.2 粗配准

首个生产适配器支持：

- FPFH 特征对应。
- RANSAC 全局配准。
- 可选 FGR 快速全局配准。
- 距离、边长和法向对应检查。

粗配准保留多个最佳假设，避免对称模型过早收敛到单一姿态。

### 12.3 精配准

- 对最优粗配准假设执行多尺度 point-to-plane ICP。
- 每一级记录迭代上限、收敛条件、fitness 和 RMSE。
- 精配准结果必须优于或合理保持粗配准质量；异常退化进入复核或拒绝。

### 12.4 刚性矩阵验证

最终矩阵必须满足：

- 最后一行为 `[0, 0, 0, 1]`，在数值容差内成立。
- 旋转子矩阵正交。
- 行列式接近 `+1`。
- 不包含缩放、反射、错切或非有限值。
- 平移和旋转范围符合项目策略。

验证失败时不能生成可确认的配准结果。

## 13. 残差与质量门禁

核心指标：

- `fitness`
- `inlier_rmse`
- 观测点到模型的覆盖率
- 模型表面到观测点的覆盖率
- 双向 Chamfer 距离
- P50/P95 点到表面距离
- 长宽高和体积偏差
- 法向一致性
- 内点数量和空间分布
- 粗配准到精配准的改善幅度
- 对称姿态候选的分数差距

双向覆盖率必须分别保留。复杂工业现场可能只扫描到设备部分表面，因此“观测点能够被模型解释”和“模型表面被观测的完整程度”不能混为一个指标。

门禁状态：

- `passed`：可以提交普通确认。
- `review_required`：遮挡、对称歧义、候选接近或证据不足，需要重点复核。
- `rejected`：尺寸冲突、残差过高、覆盖不足、矩阵非法或引擎失败。

门槛按设备类别、点密度和遮挡等级版本化，不硬编码为单一全局值。

## 14. 人工决策与绑定

系统永远不会在 Phase 15 自动建立生产绑定。自动化结果只能生成候选和建议。

人员操作：

- `confirm`：确认一个通过或已复核的模型与配准。
- `select_other`：改选 Top-K 中的其他候选并重新配准或确认。
- `reject`：拒绝某候选，可选填原因。
- `no_match`：模型库中没有合适模型。
- `rerun`：使用当前 Champion 重新运行。

确认时必须再次验证对象指纹、模型版本、配准版本和权限，避免在查看后对象已变化的情况下写入陈旧绑定。

普通业务确认的 `verification_scope` 为 `identity` 或 `operational_pose`。只有具备专业权限并完成精确复核的决定才能标记为 `expert_pose`。

绑定不可覆盖。修正绑定创建新 `binding_id`，并通过 `supersedes_binding_id` 形成历史链。恢复历史绑定同样创建新版本，而不是重新激活旧文件。

## 15. 参考点云模板

P15-M5 引入 `scanned_reference`：

- 参考点云必须是经过验证的单对象数据。
- 记录设备型号、来源扫描、单位、许可证、裁剪/清理步骤和点云指纹。
- 参考点云和 `cad_sampled` 共享特征、检索和配准协议。
- 同一 `model_asset` 可以同时拥有 CAD 与实物点云表达。
- 检索结果必须解释最终使用了哪一种表达。
- CAD 与扫描模板之间的差异可以作为后续模型质量分析输入，但 Phase 15 不修改 CAD 几何。

## 16. 受控持续优化

### 16.1 反馈数据

- 身份正样本：人员确认的模型和表达，可用于检索权重评估与优化。
- 业务位姿样本：普通人员确认可用于业务绑定与质量统计，但不视为精确位姿真值。
- 专家位姿样本：具有 `expert_pose` 验证范围的确认，可用于配准参数优化。
- 困难负样本：曾高排名但被人员拒绝的候选。
- 无匹配样本：确认模型库缺少合适对象。
- 普通负样本：低排名候选，仅以较低权重参与统计。
- 黄金回归样本：仅评估，禁止进入参数搜索或训练。

反馈数据集清单必须记录版本、来源决策、对象与模型指纹、许可证、分区和可用范围。系统不得把普通业务确认自动提升为精确位姿真值。

### 16.2 可优化内容

- 语义和几何检索权重。
- 类别可信度降级策略。
- 粗配准与精配准参数。
- 类别、点密度和遮挡分组门槛。
- 候选数量和进入精配准的数量。

### 16.3 目标与安全优先级

优化报告至少包含：

- Recall@K
- Mean Reciprocal Rank
- Top-1 正确率
- 错误模型通过率
- 正确模型拒绝率
- 配准残差和覆盖率
- 按类别、场景和密度分组的最差表现

“错误模型通过质量门禁”具有最高惩罚，不允许通过提高总体平均值掩盖关键类别退化。

### 16.4 Champion/Challenger

- 搜索空间、试验预算和随机种子必须有界并记录。
- Challenger 必须在独立验证集和黄金回归集上比较。
- 操作者不能审批自己创建的 Challenger。
- 未经 `approver` 明确批准，Challenger 不能成为生产 Champion。
- 推广和回滚都生成新配置状态与审计事件。
- Phase 15 不在线训练学习型特征网络；后续 Phase 17 可以消费已确认反馈。

## 17. 审计与自动化追踪

### 17.1 强制审计范围

以下动作无论成功或失败都必须审计：

- 模型创建、版本导入、验证、采样、特征计算和索引发布。
- 对象特征计算、候选过滤、评分和排序。
- 每个粗配准、精配准、门禁和重试。
- 人工确认、拒绝、无匹配、重新运行和绑定替换。
- 参数搜索、试验失败、Challenger 推荐、审批、推广和回滚。
- 权限拒绝、幂等冲突、陈旧对象和工件校验失败。

### 17.2 审计事件契约

每个事件记录：

- `event_id`
- `operation_id`
- `parent_operation_id`
- `sequence`
- `event_type`
- `timestamp`
- `actor_id`
- `principal_type`：`human` 或 `system`
- `roles` 和授权决定
- `interface_id` 和客户端版本
- `request_id` 和幂等键
- 输入对象、模型、数据和配置指纹
- 算法、引擎版本、参数和随机种子
- 自动决策原因和评分解释
- 前后状态与版本
- 工件引用和工件指纹
- 结果、错误代码、异常摘要和重试引用
- `previous_event_hash` 和当前事件哈希

事件只能追加。哈希链用于发现同一操作日志被修改或截断。审计状态投影可以重建，不作为唯一真值。除哈希完整性外，账本还必须验证生命周期语义：首个生命周期事件只能是一次 `operation.started` 或 `operation.start_failed`，任何终止事件之后都不得再追加 `operation.started`，也不得出现重复或相互矛盾的终止转换；终止后的重放/冲突尝试事件只记录尝试，不改变投影状态。

每个操作使用稳定的外部协调路径 `reports/model_matching_locks/<operation_id>.lock`。锁文件是永久协调工件，不能删除；操作目录的重命名或清理不得改变锁身份。实现必须使用非阻塞操作系统内核字节锁：Windows 通过 `msvcrt`，POSIX 通过 `fcntl`。只有内核锁所有权证明存活；owner token、PID、用途和取得时间仅用于诊断，且只能在取得内核锁后覆盖。进程退出或崩溃由内核自动释放所有权，残留或不完整元数据不会阻塞替代 owner，也不能用超时单独判定 owner 已死亡。

初始化者必须在发布幂等索引前取得该操作的内核锁，并持续持有到 `operation.started` 已写入、刷新并 `fsync` 完成。发现已有索引但尚无事件的重放者，必须先验证索引指纹和被索引操作一致，再尝试取得同一内核锁；锁忙只返回稳定 `operation_busy`，绝不能根据经过时间终止仍存活的初始化者。只有成功取得锁才证明初始化者已离开，恢复流程才能在锁内完成确定性协调。

幂等索引采用同目录原子 no-replace 发布：先把 canonical JSON 写入 `reports/model_matching_idempotency` 中唯一临时文件，刷新并 `fsync`，再通过经过能力探测的硬链接原语原子发布最终名称。不得使用 `os.replace` 或任何覆盖回退。目标已存在时，完整目标是唯一 winner，进入正常重放/冲突流程；发布前崩溃只留下读者不可见的临时文件，发布后清理前崩溃留下完整目标及无害临时硬链接。POSIX 在支持时还要 `fsync` 父目录；Windows 的承诺边界是在支持硬链接与非阻塞字节锁的本地 NTFS 类存储上的进程崩溃安全。

审计写入前必须执行存储能力预检，验证非阻塞内核锁和硬链接 no-replace 语义；每个进程、每个项目根目录只缓存成功结果，失败不得缓存或静默降级。能力缺失返回稳定 `audit_persistence_error`，且不得暴露部分索引或修改既有索引。网络文件系统或不提供这些本地协调语义的存储不在本阶段支持边界内。

### 17.3 身份信任

生产环境的身份、角色和界面权限必须来自可信认证层，不能接受浏览器自由填写的操作者。开发模式可以使用显式测试身份，但必须标记为开发来源。

## 18. 双角色界面

### 18.1 业务匹配界面 `frontend/model-matching.html`

面向现场人员、设备管理人员和普通确认人员。

默认流程只有：

1. 选择待匹配对象。
2. 查看系统推荐模型与叠加预览。
3. 确认、查看下一个或标记无匹配。

默认只显示：

- 推荐模型名称、版本和预览。
- `高匹配`、`需要复核` 或 `不建议使用`。
- 尺寸、形状、类别和覆盖率的简短自然语言说明。
- 确认、换一个、拒绝和无匹配操作。

算法术语、参数和底层审计字段默认隐藏。所有自动日志由系统写入，用户无需手工填写。

### 18.2 专业管理界面 `frontend/model-matching-lab.html`

面向算法工程师、模型库管理员和审计人员。

包括：

- 模型入库、版本、标签和索引状态。
- 完整候选评分、过滤和降级原因。
- 粗/精配准、残差、覆盖率和矩阵详情。
- 失败、重试、陈旧绑定和历史调查。
- 有界实验和 Champion/Challenger 比较。
- 审批、回滚和完整审计事件。

高级参数仍采用渐进展开，避免普通专业操作也被不必要复杂度占据。

### 18.3 权限

- `operator`：运行匹配、确认、拒绝、无匹配和查看业务结果。
- `expert`：模型入库、索引、实验、技术详情和重试。
- `approver`：批准 Champion 推广和回滚，可附加在指定专家账号。
- `auditor`：读取全部版本和事件，不能修改。

权限必须由服务端执行。隐藏按钮不能替代授权检查。

## 19. 异步任务与幂等性

耗时操作统一采用：

```text
queued -> running -> completed
                 -> failed
                 -> cancelled
```

适用范围包括模型采样、特征计算、索引、配准和优化。检索即使同步完成，也必须生成可审计运行。

- 每次重试生成新 `attempt_id` 并引用原尝试。
- 同一幂等键和相同请求返回原操作。
- 同一幂等键但内容不同返回稳定冲突错误。
- 取消只停止后续工作，不删除已产生的审计和诊断工件。
- 发布业务状态前使用暂存目录和原子完成语义。

## 20. 公共接口

### 20.1 API

模型库：

```text
GET  /model-library
POST /model-library/models
GET  /model-library/models/<model_id>
POST /model-library/models/<model_id>/versions
POST /model-library/models/<model_id>/versions/<version_id>/index
```

检索和配准：

```text
POST /model-matches/<asset_id>/<object_id>
GET  /model-matches/<asset_id>/<object_id>/<match_id>
GET  /model-matches/<asset_id>/<object_id>/<match_id>/candidates
POST /model-matches/<asset_id>/<object_id>/<match_id>/register
GET  /model-registrations/<registration_id>
```

决策和绑定：

```text
POST /model-matches/<match_id>/confirm
POST /model-matches/<match_id>/reject
POST /model-matches/<match_id>/no-match
GET  /model-bindings/<asset_id>/<object_id>
POST /model-bindings/<binding_id>/supersede
```

优化和审计：

```text
POST /model-matching-optimizations
GET  /model-matching-optimizations/<experiment_id>
POST /model-matching-optimizations/<experiment_id>/approve
POST /model-matching-configurations/<version_id>/rollback
GET  /audit/operations/<operation_id>
```

所有写接口要求可信身份、权限、请求 ID 和幂等键。

### 20.2 CLI

- `import-model`
- `index-model-version`
- `retrieve-model-candidates`
- `register-model-candidate`
- `confirm-model-binding`
- `reject-model-candidate`
- `run-model-matching-optimization`
- `approve-model-matching-config`
- `audit-model-matching-operation`

CLI 和 API 调用同一服务函数和校验规则。

## 21. 稳定错误

- `invalid_model_format`
- `invalid_model_unit`
- `invalid_model_geometry`
- `model_version_exists`
- `model_version_immutable`
- `model_index_not_ready`
- `object_not_found`
- `object_fingerprint_stale`
- `no_candidate_models`
- `registration_engine_unavailable`
- `coarse_registration_failed`
- `fine_registration_failed`
- `non_rigid_transform`
- `registration_gate_rejected`
- `ambiguous_symmetric_pose`
- `binding_exists`
- `binding_stale`
- `permission_denied`
- `self_approval_forbidden`
- `idempotency_conflict`
- `artifact_integrity_failed`
- `golden_data_optimization_forbidden`

失败不能留下已发布的半成品模型版本、绑定或生产配置。诊断和审计工件必须保留。

## 22. 测试策略

### 22.1 单元与契约测试

- 模型清单、单位转换、标识符和版本不可变性。
- STL、OBJ、PLY 解析适配器契约。
- 固定种子采样和特征确定性。
- 文本、标签、几何评分和降级策略。
- 刚性矩阵验证。
- 残差、覆盖率和门禁。
- 绑定失效、supersede 和恢复。
- 审计哈希链和状态重放。
- 权限与职责分离。
- 幂等性和原子失败。

### 22.2 配准测试

- 已知旋转和平移的合成配准。
- 部分遮挡、噪声和离群点。
- 对称模型和多个合理姿态。
- 错误尺寸和错误模型。
- 粗配准失败、ICP 退化和引擎不可用。
- Open3D 安装环境下的独立集成测试。

### 22.3 端到端与前端测试

- Phase 14 发布对象到 Top-K、配准、确认和绑定。
- 业务界面三步流程。
- 专业界面的模型入库、技术详情和审计查询。
- Challenger 不能未经审批进入生产。
- 所有关键操作能通过 `operation_id` 完整追溯。
- Phase 1-14 全量回归。

核心测试不要求安装 Open3D；生产工具检查必须明确报告 Open3D 适配器是否可用。

## 23. 验收标准

- CAD 模型可以版本化入库、采样并建立特征索引。
- Phase 14 发布对象可以直接进入模型检索。
- 系统输出带原因的 Top-K 候选。
- 至少一个生产适配器完成刚性粗配准和精配准。
- 缩放、反射、错切或非法矩阵被拒绝。
- 业务用户通过简洁界面确认、拒绝或标记无匹配。
- 确认形成不可变、可追溯且能检测陈旧对象的模型绑定。
- 实物参考点云使用同一套检索和配准接口。
- 自动搜索只产生 Challenger。
- 所有自动化和人工操作均可通过 `operation_id` 追溯。
- 审计事件可以重建任务状态和关键版本关系。
- 全量测试通过且不破坏 Phase 1-14 契约。

## 24. 后续阶段

- Phase 16：点云、高斯泼溅和语义模型的统一对象查看器。
- Phase 17：学习型检索特征、离线重训练、漂移检测、Champion/Challenger 和受控持续学习。
- 后续：跨时间设备身份、对象生命周期、仿真和设备运维管理。

Phase 15 的确认、拒绝、无匹配、配准和审计数据为 Phase 17 提供训练与评估基础，但 Phase 17 仍必须执行数据分区、黄金回归、独立审批和回滚。

## 25. 技术依据

- [Open3D 全局配准文档](https://www.open3d.org/docs/latest/tutorial/Advanced/global_registration.html) 展示了 FPFH、RANSAC、FGR 和对应检查的组合。
- [Open3D ICP 文档](https://www.open3d.org/docs/latest/python_api/open3d.registration.registration_icp.html) 提供 point-to-point/point-to-plane ICP 及收敛配置。
- [FPFH 原始论文](https://www.cvl.iis.u-tokyo.ac.jp/class2016/2016w/papers/6.3DdataProcessing/Rusu_FPFH_ICRA2009.pdf) 给出局部几何描述与对应搜索基础。
- [TEASER++ 论文](https://arxiv.org/abs/2001.07715) 作为后续高离群点鲁棒粗配准适配器依据，不是首期唯一引擎。
