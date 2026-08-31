# Phase 15C 刚性配准与质量门禁设计

**日期：** 2026-08-31

**状态：** 已完成会话设计确认，等待书面规格终审

**目标分支：** `codex/phase15c-rigid-registration`

## 1. 背景

Phase 15A 已交付版本化 CAD 模型库和可信审计基础，Phase 15B-1 已交付不可变模型版本与确定性表面采样，Phase 15B-2 已交付从已审查分割对象到可解释 Top-K 模型候选的检索闭环。

当前候选检索只能说明“哪些模型可能匹配”，不能证明模型在空间中如何与对象点云对齐，也不能判断候选是否满足尺寸、残差、覆盖率和刚性变换要求。Phase 15C 在检索与人工绑定之间增加独立的刚性配准和质量门禁层。

Phase 15C 的结果是可审计的配准建议，不是生产模型绑定。确认、换候选、拒绝、无匹配和不可变对象—模型绑定继续由 Phase 15D 负责。

## 2. 设计目标

Phase 15C 必须实现：

1. 从一个已验证的 Phase 15B-2 检索候选启动配准。
2. 冻结对象、模型版本、模型采样表达和特征证据，确保历史结果可重放。
3. 使用有限初始姿态、FPFH 与 RANSAC 完成刚性粗配准。
4. 支持将 FGR 作为可配置的附加粗配准策略。
5. 对多个最佳粗配准假设执行多尺度 point-to-plane ICP。
6. 严格拒绝缩放、反射、错切、非有限矩阵和超策略范围位姿。
7. 分别计算双向覆盖率、残差、尺寸一致性和对称姿态歧义。
8. 输出 `passed`、`review_required` 或 `rejected` 质量结论。
9. 记录输入、配置、假设、淘汰原因、引擎输出、质量指标、异常和恢复过程。
10. 保持核心模块可在未安装 Open3D 的环境中测试和运行；生产配准明确依赖 Open3D 适配器。

## 3. 非目标

本阶段不实现：

- 自动建立或更新对象—模型绑定。
- 非刚性配准、模型形变、比例缩放或自动修改 CAD 几何。
- GPU 强制依赖或分布式配准调度。
- 学习型局部描述子、在线训练或自动推广生产参数。
- 实物参考点云模板；该能力属于 Phase 15E。
- 面向业务人员和专家的完整匹配界面；该能力属于 Phase 15D。
- 全局最优位姿的数学保证。系统提供有界、可解释和可复核的工程结果。

## 4. 核心设计原则

### 4.1 不猜测历史证据

配准只能消费明确冻结的对象和模型表达。系统不得在配准时把可变的“当前模型版本”或“当前采样表达”替代为检索时实际使用的证据。

### 4.2 检索、配准和绑定分层

- Phase 15B-2 负责候选召回和排序。
- Phase 15C 负责空间对齐和几何质量判断。
- Phase 15D 负责人为业务决定和生产绑定。

高检索分数不等同于配准通过，配准通过也不等同于人员已经确认。

### 4.3 引擎与业务编排分离

配准引擎只接收经过验证的数值输入和配置，返回假设、矩阵和原始指标。对象身份、权限、审计、幂等、工件发布、质量门禁和恢复由引擎无关的编排核心负责。

### 4.4 失败关闭

引擎不可用、输入证据缺失、对象陈旧、矩阵非法或工件完整性失败时，系统不得静默使用低质量替代算法，也不得生成可供确认的成功结果。

## 5. 总体架构

```text
Phase 15B-2 检索报告 schema 1.1
              │
              ▼
      配准输入验证与证据冻结
              │
              ▼
      有限初始姿态与多尺度预处理
              │
              ▼
   配准引擎端口 ───── Open3D 生产适配器
       │                    │
       │              FPFH / RANSAC / FGR / ICP
       ▼
  刚性矩阵独立验证
              │
              ▼
      双向残差与覆盖率计算
              │
              ▼
       版本化质量门禁
              │
              ▼
  不可变配准报告 + 工件 + 审计事件
```

## 6. 模块边界

### 6.1 `model_registration_config.py`

负责配准配置的严格校验、指纹计算、不可变发布、读取和列表。配置内容包括预处理、初始姿态、粗配准、精配准、矩阵验证、残差和门禁策略。

该模块不执行配准，也不读取对象或模型点云。

### 6.2 `model_registration_input.py`

负责：

- 读取并验证 Phase 15B-2 检索报告。
- 按排名选择一个候选。
- 验证候选确实属于报告中的 Top-K。
- 验证对象指纹仍与当前发布对象一致。
- 验证模型版本、发布、采样表达和特征指纹。
- 加载对象点云和模型采样点云。
- 生成供引擎使用的只读输入快照。

该模块不得重新选择候选或重新解释缺失的历史证据。

### 6.3 `model_registration_engine.py`

定义引擎无关协议和返回结构：

- 能力与版本查询。
- 点云预处理。
- 粗配准。
- 精配准。
- 明确的引擎错误分类。

核心测试使用确定性测试引擎。测试引擎只用于验证业务编排和失败语义，不得被标记为生产配准结果。

### 6.4 `model_registration_open3d.py`

首个生产适配器，负责：

- 多尺度体素降采样。
- 法向量估计。
- FPFH 计算。
- RANSAC 全局配准。
- 可配置 FGR 假设生成。
- 多尺度 point-to-plane ICP。

Open3D 作为可选依赖单独声明。缺失或版本不兼容时返回 `registration_engine_unavailable`。

### 6.5 `model_registration_metrics.py`

独立计算最终指标，不直接信任引擎返回的单一 fitness：

- 对象点到模型表面的覆盖率和距离分布。
- 模型表面到对象点的覆盖率和距离分布。
- 双向 Chamfer 距离。
- P50/P95 点到表面距离。
- 内点数量和空间分布。
- 包围盒长宽高与体积偏差。
- 可用时的法向一致性。
- 粗配准到精配准的改善或退化幅度。
- 最佳和次佳对称姿态的分数差距。

### 6.6 `model_registration_gate.py`

根据已发布配置和指标输出门禁状态及稳定原因码。该模块无文件系统副作用，便于独立测试和后续 Phase 15F Champion/Challenger 评估。

### 6.7 `model_registration.py`

负责主流程编排、权限、幂等操作、审计事件、候选工件、原子发布、失败终态和重放。

API 和 CLI 只做边界输入校验及调用，不复制算法或门禁逻辑。

## 7. Phase 15B-2 候选契约升级

### 7.1 新契约

`candidates.json` 升级到 `schema_version: "1.1"`。每个候选在现有字段基础上增加：

- `release_id`
- `representation_id`
- `representation_fingerprint`
- `feature_id`
- `feature_vector_fingerprint`

检索报告继续冻结：

- `index_release_id` 或 Challenger `index_id`
- `config_id` 与 `config_fingerprint`
- `object_fingerprint`
- `candidates_fingerprint`

候选排序分数和解释继续保持现有语义，不因新增证据字段改变评分。

### 7.2 兼容策略

- 读取接口继续允许查看合法的 `1.0` 历史报告。
- `1.0` 报告不能启动正式配准，返回 `registration_input_incomplete`。
- 系统不得从当前索引或当前模型头猜测缺失表达。
- 用户通过重新执行检索生成 `1.1` 报告后再启动配准。
- 新写入的候选和检索报告只使用 `1.1`。

## 8. 配准配置

`registration_config` 至少包含：

- `config_id`
- `schema_version`
- `engine_name`
- `engine_constraints`
- `preprocessing`
- `initial_hypotheses`
- `coarse_registration`
- `fine_registration`
- `transform_validation`
- `residual_metrics`
- `quality_gates`
- `category_overrides`
- `config_fingerprint`
- `created_by`
- `created_at`
- `operation_id`

所有整数和浮点范围必须有界；禁止 NaN、无穷值、负距离、空多尺度列表和无上限迭代。单位统一为米和弧度。

首版允许发布多个不可变配置，但配准请求必须明确指定 `config_id`。配置推广和回滚属于后续受控优化，不在本阶段增加隐式“当前配置”切换。

## 9. 配准运行契约

已完成或诊断性失败的 `registration_run` 至少冻结：

- `registration_id`
- `retrieval_run_id`
- `candidate_rank`
- `candidate_model_id`
- `candidate_version_id`
- `candidate_release_id`
- `candidate_representation_id`
- `candidate_representation_fingerprint`
- `candidate_feature_id`
- `candidate_feature_vector_fingerprint`
- `object_fingerprint`
- `engine` 与 `engine_version`
- `config_id` 与 `config_fingerprint`
- `initial_hypotheses`
- `coarse_results`
- `fine_results`
- `rigid_transform_4x4`，失败时为空
- `coarse_metrics`
- `fine_metrics`
- `residual_metrics`
- `gate_status`
- `gate_reasons`
- `artifacts`
- `operation_id`
- `generated_by`
- `generated_at`
- `status`

业务状态区分：

- `completed`：计算和发布完成；门禁可以是任意三态。
- `failed`：只允许作为诊断性失败报告；`gate_status` 和正式矩阵必须为空，不能进入 Phase 15D 确认流程。

`rejected` 是成功完成计算后的质量结论，不等同于系统执行失败。

## 10. 配准算法

### 10.1 输入验证

配准前验证：

- 对象与模型点均为有限三维坐标。
- 坐标单位为米。
- 点数处于配置边界内。
- 点云不为空且不完全退化为单点或单线。
- 对象和候选证据指纹完全一致。
- 模型版本仍是合法、已发布且允许使用的不可变版本。

### 10.2 多尺度预处理

体素尺度、法向搜索半径、FPFH 搜索半径和邻居上限由版本化配置提供。配置可根据对象包围盒对基准尺度进行有界换算，但实际解析值必须写入运行报告。

系统必须保留每一级输入点数、输出点数、法向可用数量和淘汰原因。

### 10.3 有限初始姿态

初始姿态来源：

- 单位姿态。
- 对象与模型主轴对齐。
- 主轴符号组合。
- 模型元数据声明的离散对称变换。

配置限制最大假设数量。系统按稳定规则排序和去重，不进行无界随机重试。每个假设都保存来源、初始矩阵、处理状态及淘汰原因。

### 10.4 粗配准

默认生产路径使用 FPFH 对应与 RANSAC：

- 距离检查。
- 边长比例检查。
- 法向一致性检查。
- 有界最大迭代和置信度。
- 固定或可记录随机种子。

FGR 可作为配置启用的附加假设生成器，不替代默认 RANSAC 安全路径。粗配准结果经过刚性验证、范围检查和近似重复消除后，按稳定规则保留 Top-N。

### 10.5 精配准

对 Top-N 粗配准假设分别执行由粗到细的 point-to-plane ICP。每一级记录：

- 体素尺度与对应距离。
- 迭代上限和收敛条件。
- 输入与内点数量。
- fitness 和 RMSE。
- 输入与输出矩阵。
- 收敛、失败或退化原因。

系统不会因为第一个假设收敛就跳过其他允许的假设。最终结果按配置定义的稳定综合指标选择。

## 11. 刚性矩阵验证

任何来自引擎的矩阵都必须由核心模块独立验证：

- 结构严格为有限 4×4 数值矩阵。
- 最后一行在容差内等于 `[0, 0, 0, 1]`。
- 旋转子矩阵满足正交性容差。
- 行列式接近 `+1`。
- 奇异值接近 1，不包含比例缩放或错切。
- 不包含反射。
- 平移长度和旋转角不超过配置策略。

非法矩阵不能进入残差计算或成为最终结果，并记录 `non_rigid_transform` 及具体检查项。

## 12. 残差与质量门禁

### 12.1 独立的双向覆盖率

系统分别保存：

- `observed_to_model_coverage`：对象观测点有多少能被模型解释。
- `model_to_observed_coverage`：模型表面有多少已被实际扫描观察。

两者不得合并为单一覆盖率。工业现场遮挡可能造成前者高、后者低。

### 12.2 门禁状态

`passed`：

- 矩阵合法。
- 尺寸和残差满足配置。
- 覆盖证据充分。
- 最佳姿态相对其他非等价姿态具有足够区分度。

`review_required`：

- 观测点能被模型良好解释，但模型表面覆盖不足，符合部分遮挡特征。
- 多个姿态得分接近。
- 模型声明的业务等价对称姿态无法唯一确定。
- 精配准改善不足但没有达到拒绝条件。
- 证据处于门槛缓冲区。

`rejected`：

- 尺寸明显冲突。
- 残差或错误模型指标超过拒绝阈值。
- 覆盖不足且不能由遮挡策略解释。
- 没有合法粗配准或精配准假设。
- 矩阵非法。

引擎不可用、输入损坏和发布失败属于运行 `failed`，不是质量门禁 `rejected`。

### 12.3 精配准退化

如果精配准相对粗配准明显退化：

- 保留粗、精两套诊断结果。
- 不把退化后的矩阵标记为正常成功。
- 仅当配置允许且粗配准满足独立最低门槛时，才可输出粗矩阵并标记 `review_required`。
- 否则质量结论为 `rejected`。

## 13. 工件布局

```text
configs/model_registration/<config_id>/
  registration_config.json

reports/model_registrations/<asset_id>/<source_id>/<instance_id>/<registration_id>/
  operation_owner.json
  registration_input.json
  initial_hypotheses.json
  coarse_results.json
  fine_results.json
  residual_report.json
  registration_report.json
  transformed_preview.json
```

正式工件均使用规范 JSON、稳定字段顺序和内容指纹。`transformed_preview.json` 是可视化辅助工件，不是质量计算或绑定的权威输入。

## 14. API 与 CLI

首版提供：

- 发布配准配置。
- 列出和读取配准配置。
- 从检索运行及候选排名启动配准。
- 读取已验证配准报告。

建议 API 资源：

- `POST /model-matching/registration-configs`
- `GET /model-matching/registration-configs`
- `POST /model-matching/registrations`
- `GET /model-matching/registrations/{asset_id}/{source_id}/{instance_id}/{registration_id}`

建议 CLI 命令：

- `publish-model-registration-config`
- `list-model-registration-configs`
- `register-model-candidate`
- `show-model-registration`

执行配准要求可信 `expert` 主体。读取遵循现有项目读取权限边界。Phase 15C 不增加确认或绑定命令。

## 15. 幂等、并发与恢复

### 15.1 幂等语义

请求指纹必须覆盖：

- 配准编号。
- 检索运行和候选排名。
- 对象身份。
- 配置编号。
- 引擎选择。
- 可信主体和请求元数据。

相同请求重放原结果；同一幂等键但输入不同返回 `idempotency_conflict`。

### 15.2 发布语义

- 配准计算在受控候选目录中形成完整工件。
- 所有工件和相互引用验证通过后才发布正式结果。
- 同一 `registration_id` 不允许覆盖不同内容。
- 发布后耐久性未确认时返回 `publication_recovery_required`，保留原操作为可恢复状态。
- 同幂等重试验证 owner、请求指纹、工件和审计事件后原位完成。

### 15.3 安全边界

遵循现有 Phase 15 文件系统安全经验：

- 不自动递归删除或移动来源不明目录。
- 不通过路径存在性猜测所有者。
- 不接管其他操作的候选目录。
- 不使用无界等待或无界重试。
- 不在恢复时修改已发布历史工件。

无法安全判定时失败关闭并要求人工处理，而不是堆叠路径级修补。

## 16. 审计事件

每次自动配准至少记录：

- 操作开始或开始失败。
- 输入证据验证完成。
- 配置解析完成。
- 引擎能力与版本确认。
- 预处理完成。
- 初始假设生成与淘汰。
- 粗配准完成或失败。
- 精配准完成、退化或失败。
- 刚性矩阵验证结果。
- 残差计算完成。
- 质量门禁结论。
- 工件发布、恢复要求和操作终态。

审计事件保存配置和工件指纹，不在事件中重复存放大型点云。操作事件仍是状态重建的审计真值。

## 17. 错误模型

Phase 15C 新增或稳定使用：

- `registration_input_incomplete`
- `object_fingerprint_stale`
- `registration_engine_unavailable`
- `registration_config_invalid`
- `coarse_registration_failed`
- `fine_registration_failed`
- `non_rigid_transform`
- `registration_gate_rejected`，仅作为报告中的稳定质量原因码
- `ambiguous_symmetric_pose`
- `artifact_integrity_failed`
- `publication_recovery_required`
- `idempotency_conflict`
- `operation_busy`

质量门禁拒绝必须作为已完成报告返回，并以 `registration_gate_rejected` 记录在 `gate_reasons`；它不是传输层异常，不得映射成 HTTP 4xx/5xx 或 CLI 非零执行错误。输入损坏、权限失败、引擎异常和发布失败使用现有统一 API/CLI 错误映射规则。

## 18. 测试策略

### 18.1 单元测试

- 配置严格模式、数值边界和指纹。
- 候选 `1.1` 证据完整性及 `1.0` 正式配准拒绝。
- 4×4 矩阵、正交性、行列式、奇异值和平移旋转范围。
- 双向覆盖率、Chamfer、分位距离、尺寸偏差和改善幅度。
- 三态门禁、部分遮挡和对称歧义。

### 18.2 合成配准测试

- 已知旋转和平移。
- 多尺度点密度。
- 噪声和离群点。
- 部分遮挡。
- 对称模型和多个合理姿态。
- 错误尺寸和错误模型。
- 粗配准失败、ICP 退化和非法引擎矩阵。

### 18.3 编排与恢复测试

- 对象指纹陈旧。
- 候选证据或模型表达篡改。
- 幂等重放和幂等冲突。
- 同资源并发操作。
- 候选工件写入中断。
- 发布后确认失败及同操作恢复。
- 失败运行不产生可确认的正式矩阵。

### 18.4 接口与集成测试

- API 和 CLI 正常、拒绝和错误映射。
- Phase 14 对象经 Phase 15B-2 `1.1` 检索进入 Phase 15C。
- 确定性测试引擎的核心集成测试。
- 安装 Open3D 时执行独立生产适配器集成测试。
- 未安装 Open3D 时明确报告能力不可用，其他系统功能不受影响。

### 18.5 验证顺序

1. 新增或直接受影响的回归测试。
2. Phase 15B-2/15C 聚焦集成测试。
3. 静态语法和接口契约检查。
4. 阶段完成门禁执行一次全量测试。
5. 仅在推送或合并前确有需要时再执行一次全量测试。

## 19. 验收标准

Phase 15C 完成必须同时满足：

1. Phase 15B-2 新候选报告冻结确切模型表达与特征证据。
2. 旧候选报告可查看但不能被猜测性地用于正式配准。
3. 至少一个生产 Open3D 适配器实现 RANSAC 粗配准和多尺度 ICP。
4. 核心模块可使用确定性测试引擎独立验证。
5. 合法合成刚性变换可以恢复到配置容差内。
6. 缩放、反射、错切、非法值和超范围矩阵被拒绝。
7. 部分遮挡与双向低覆盖能够被正确区分。
8. 对称歧义不会被伪装成唯一精确位姿。
9. 运行结果、配置、证据和所有自动化步骤可通过 `operation_id` 追溯。
10. Phase 15C 不创建任何生产模型绑定。
11. Phase 1–15B-2 全量回归通过。

## 20. 实施边界

本设计作为一个阶段目标实施，但按依赖顺序拆分为可复审任务：

1. Phase 15B-2 候选证据契约 `1.1`。
2. 配准配置、输入验证和引擎协议。
3. 刚性矩阵、指标和质量门禁纯核心。
4. 配准编排、不可变工件、幂等和审计。
5. Open3D 生产适配器。
6. API、CLI 和端到端集成。
7. 中文操作文档、功能清单与阶段门禁。

任务不得顺带实现 Phase 15D 绑定、Phase 15E 参考模板或 Phase 15F 自动优化。发现非阻塞改进时记录为后续债务，不扩大当前范围。
