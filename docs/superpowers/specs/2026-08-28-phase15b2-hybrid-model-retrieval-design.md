# Phase 15B-2 版本化特征与混合模型检索设计

**状态：** 已确认，等待实施计划

**日期：** 2026-08-28

**依据：**

- `docs/superpowers/specs/2026-07-22-phase15-model-library-retrieval-registration-design.md`
- `docs/superpowers/specs/2026-08-27-phase15b1-versioned-model-sampling-design.md`

## 1. 目标

Phase 15B-2 在 Phase 14 不可变分割发布、Phase 15A 模型库和 Phase 15B-1 模型版本发布与确定性采样之上，交付可生产使用的完整候选检索闭环：

1. 补强 Phase 14 发布对象的对象级审查证据；
2. 对模型采样表达和分割对象生成同构、版本化的几何特征；
3. 从当前激活模型版本构建不可变生产候选索引；
4. 支持与生产隔离的历史版本 Challenger 索引；
5. 使用类别、文本、尺寸、整体形状和空间占用执行可解释的精确 Top-K 检索；
6. 保存不可变检索运行、完整评分解释、风险和审计链；
7. 支持索引发布历史、显式晋升和受审计回滚。

本阶段优先保证可解释、可复现、可恢复和高召回。检索结果是后续刚性配准与人工确认的候选，不直接形成对象—模型绑定。

## 2. 已确认的产品边界

### 2.1 检索输入

- 生产检索只接受 Phase 14 已发布、不可变的纠正版本。
- 专家实验模式可以显式读取 Phase 13A 状态为 `completed` 的分割运行。
- Phase 14 草稿或 `in_review` 会话不得进入检索。
- Phase 14 发布点坐标单位必须为米，源指纹、对象点集合和对象标识必须通过完整性验证。

### 2.2 候选模型版本

- 生产索引只包含每个模型当前激活的发布版本。
- 未发布版本和历史版本默认不进入生产索引。
- Challenger 索引可以显式包含历史版本，但必须使用独立索引编号和 `index_mode=challenger`；Challenger 不设置当前投影，实验请求必须显式指定索引编号。
- 模型版本回滚后必须构建新索引快照；不得修改旧索引。

### 2.3 类别过滤

- 对象类别已人工确认且存在显式类别映射时，先执行类别硬过滤。
- 算法类别、未确认类别、旧发布缺少对象级审查证据或类别未映射时，类别只参与软评分。
- 硬过滤没有候选时自动降级到全索引几何检索，并记录 `category_filter_empty`。
- 不允许根据字符串相似度猜测 `class_id → category_id`。

### 2.4 权重与自动学习

- 第一版使用版本化、固定、可审计的评分配置。
- 缺失评分分量时只对可用分量重新归一化权重。
- Phase 15B-2 记录检索结果与后续人工决定所需的关联标识，但不自动修改生产权重。
- 自动参数搜索、Champion/Challenger 比较、独立审批和推广属于 Phase 15F。

## 3. 非目标

- FPFH、RANSAC、FGR、ICP 或其他配准算法；属于 Phase 15C。
- 对象—模型绑定、确认、拒绝、换候选和双界面；属于 Phase 15D。
- 实物参考点云模板；属于 Phase 15E。
- 自动训练、在线学习、自动替换生产配置；属于 Phase 15F/17。
- SQLite、外部向量数据库或近似最近邻服务。
- 最终用户模型匹配页面。
- 自动生成缺失 CAD 模型。
- 修改历史 Phase 14 发布、历史模型版本、历史索引或历史检索报告。

## 4. 架构

### 4.1 `retrieval_input`

负责读取并验证 Phase 14 发布对象或专家指定的 Phase 13A 完成运行，输出统一查询对象：

```python
load_retrieval_object(
    project_root: Path,
    *,
    source_kind: str,
    asset_id: str,
    source_id: str,
    instance_id: str,
) -> dict
```

`source_kind` 只允许 `correction_release` 或 `segmentation_run`。返回值包含规范点列表、类别、类别可信状态、对象指纹、源指纹和来源证据。

### 4.2 `model_features`

负责配置校验、确定性几何特征计算、特征完整性验证和不可变特征发布。模型点和对象点使用相同算法版本与配置。

### 4.3 `model_feature_index`

负责枚举候选模型发布、补齐匹配采样表达、生成或复用模型特征、构建不可变索引、输出覆盖率、排除清单和依赖证据。

### 4.4 `model_index_release`

负责索引发布历史、当前投影、激活、回滚、预期头比较和完整性验证。其语义与 Phase 15B-1 模型版本发布一致，但资源为特征索引。

### 4.5 `model_retrieval`

负责读取已验证对象、生产或 Challenger 索引、类别映射和评分配置，输出确定性 Top-K 与解释，并发布不可变检索运行。

### 4.6 公共入口

- 领域服务是唯一业务真相来源。
- CLI 提供专家构建、发布、回滚、检索和查询命令。
- API 复用领域服务、可信主体、请求大小限制、幂等与稳定错误映射。
- API/CLI 不得自行计算评分或绕过完整性检查。

## 5. Phase 14 对象审查证据补强

### 5.1 新发布工件

新 Phase 14 发布增加：

```text
reports/segmentation_correction_releases/<asset_id>/<release_id>/
  object_review_evidence.json
```

并在 `correction_release.json.artifacts.object_review_evidence` 中引用该文件。

文档结构：

```json
{
  "schema_version": "1.0",
  "asset_id": "scan-a",
  "release_id": "correction-001",
  "source_fingerprint": "<sha256>",
  "objects_fingerprint": "<sha256>",
  "objects": [
    {
      "instance_id": "pump-001",
      "class_id": "centrifugal-pump",
      "object_fingerprint": "<sha256>",
      "point_count": 1234,
      "review_state": "confirmed",
      "classification_source": "human_confirmed",
      "confirmation_event_sequence": 7,
      "confirmation_request_id": "request-confirm-007"
    }
  ]
}
```

### 5.2 对象指纹

对象指纹基于按 `source_point_index` 排序后的以下字段计算规范 JSON SHA-256：

- `source_point_index`
- 有限的米制 `x`、`y`、`z`
- `instance_id`
- `class_id`
- `is_noise`

同一 `instance_id` 必须至少包含一个非噪声点。一个源点不得属于多个非噪声对象。

### 5.3 最终确认语义

- `confirmed` 只表示最后一次影响该对象身份或类别的事件之后，存在有效 `confirm` 事件。
- 合并、拆分、改类、噪声化、恢复或撤销若使确认失效，发布证据必须显示 `unreviewed`。
- 发布级 reviewer 不能代替对象级确认。
- `classification_source=human_confirmed` 只有在最终确认闭环时成立；其他值为 `human_edited_unconfirmed` 或 `automatic_segmentation`。

### 5.4 旧发布兼容

- 旧发布缺少 `object_review_evidence.json` 时仍可构建对象特征。
- 此时 `review_state=legacy_unknown`，类别不得硬过滤。
- 系统不得回写或补写历史发布目录。

## 6. 配置契约

配置目录：

```text
models/retrieval_configs/<config_id>/
  feature_config.json
  scoring_config.json
  category_mapping.json
```

目录发布后不可覆盖。三个文件均进入配置总指纹。

### 6.1 `feature_config.json`

第一版固定契约：

```json
{
  "schema_version": "1.0",
  "config_id": "retrieval-v1",
  "algorithm_version": "phase15b2-feature-v1",
  "sampling": {
    "algorithm": "sha256_area_weighted_v1",
    "point_count": 100000,
    "random_seed": 20260828
  },
  "radial_bins": 12,
  "voxel_grid_size": 4,
  "minimum_points": 16,
  "maximum_points": 2000000,
  "degenerate_eigenvalue_ratio": 0.000001,
  "ambiguous_axis_relative_gap": 0.001
}
```

约束：

- 整数不得为布尔值。
- `point_count`、`minimum_points` 和 `maximum_points` 必须满足 `16 <= minimum_points <= point_count <= 500000` 且 `point_count <= maximum_points <= 2000000`，与 Phase 15B-1 采样上限一致。
- `radial_bins` 范围为 4–64。
- `voxel_grid_size` 范围为 2–16。
- 浮点阈值必须有限且大于 0。
- 未知字段、缺失字段和重复 JSON 键均拒绝。

### 6.2 `scoring_config.json`

```json
{
  "schema_version": "1.0",
  "config_id": "retrieval-v1",
  "top_k_default": 10,
  "top_k_maximum": 50,
  "production_minimum_coverage": 0.95,
  "weights": {
    "category": 0.20,
    "terms": 0.15,
    "manufacturer_model": 0.10,
    "dimensions": 0.25,
    "shape": 0.20,
    "occupancy": 0.10
  },
  "dimension_penalties": {
    "model_smaller_multiplier": 2.0,
    "model_larger_multiplier": 0.75
  }
}
```

权重必须有限、非负且规范总和精确归一化为 1。生产覆盖率范围为 0–1。第一版不允许请求覆盖生产权重。

### 6.3 `category_mapping.json`

```json
{
  "schema_version": "1.0",
  "config_id": "retrieval-v1",
  "mappings": {
    "centrifugal-pump": "pump"
  }
}
```

键和值均通过标识符校验。多个 `class_id` 可以映射到同一 `category_id`；一个 `class_id` 只能映射到一个类别。未映射时不得猜测。

## 7. 几何特征算法 v1

### 7.1 输入规范化

1. 校验点数组、点数范围、有限坐标和米制单位。
2. 使用规范 JSON 点内容计算输入指纹。
3. 按坐标 `(x, y, z)` 稳定排序，使输入顺序不影响结果。
4. 计算质心并将所有点平移到质心坐标系。
5. 不修改或随机抽取对象点；超过最大点数直接拒绝。

### 7.2 主轴与退化检测

- 使用总体协方差矩阵和确定性对称 3×3 Jacobi 特征分解。
- 特征值按降序排列，并将负的数值舍入误差夹到 0。
- 特征值除以总和形成 `principal_value_ratios`。
- 总特征值小于阈值时标记 `geometry_degenerate`。
- 相邻主值相对差小于 `ambiguous_axis_relative_gap` 时标记 `axis_ambiguous`。
- 主轴符号不影响投影跨度；退化轴相关占用特征不参与评分。

### 7.3 特征字段

```json
{
  "observed_spans_m": [1.2, 0.8, 0.6],
  "span_ratios": [1.0, 0.666666666667, 0.5],
  "observed_box_volume_m3": 0.576,
  "principal_value_ratios": [0.62, 0.27, 0.11],
  "radial_histogram": [0.01, 0.04, 0.08, 0.12, 0.15, 0.18, 0.16, 0.11, 0.08, 0.04, 0.02, 0.01],
  "voxel_occupancy": 0.42,
  "point_count": 1234,
  "quality": {
    "status": "usable",
    "reasons": []
  }
}
```

- `observed_spans_m` 是点投影到非退化主轴后的跨度，按降序保存。
- `span_ratios` 使用最大跨度归一化；`observed_box_volume_m3` 是三个观测跨度的乘积，只表示观测包围盒体积，不宣称是封闭 CAD 实体体积。
- 径向距离除以均方根半径后进入固定 12 桶直方图；超出末桶的值计入末桶。
- 体素占用在主轴归一化包围盒内使用固定 `4×4×4` 网格计算。
- `axis_ambiguous` 时仍保留主值和径向特征，但尺寸与占用标为低可信；完全退化时只允许元数据检索。
- 所有公开浮点值使用项目统一的有限值和稳定舍入规则。

### 7.4 确定性要求

相同点集合、算法版本和配置必须生成相同规范 JSON 字节与 SHA-256。点顺序、整体平移和刚性旋转不得改变主值比例与径向直方图；非退化对象的主轴跨度与占用应在容差舍入后保持一致。

## 8. 特征工件

### 8.1 模型特征

```text
models/<model_id>/features/<version_id>/<representation_id>/<feature_id>/
  operation_owner.json
  feature.json
```

`feature_id` 由模型版本、采样表达、几何指纹、特征配置指纹和算法版本确定性派生。

### 8.2 对象特征

```text
reports/model_retrieval_features/<asset_id>/<source_id>/<instance_id>/<feature_id>/
  operation_owner.json
  feature.json
```

对象 `feature_id` 绑定 `source_kind`、对象指纹、类别可信状态和配置指纹。

### 8.3 共同字段

`feature.json` 必须包含：

- `schema_version`
- `feature_id`
- `feature_type`：`model` 或 `object`
- 来源标识与来源内容指纹
- `feature_config_id` 与配置总指纹
- `algorithm_version`
- 第 7 节的特征字段
- `operation_id`、`generated_by`、`generated_at`
- `status=ready`

加载时必须重新验证配置证据、来源证据、内容指纹和审计操作。

## 9. 索引构建与自动采样

### 9.1 生产索引来源

构建器调用 `list_model_assets`，并对每个模型读取经过完整性验证的当前模型发布。没有当前发布的模型进入排除清单，原因是 `model_release_missing`。

### 9.2 采样表达选择

- 只选择采样算法版本、点数和随机种子与特征配置完全相同的已验证 `cad_sampled` 表达。
- 多个表达完全匹配时，它们应具有相同确定性表达编号；出现多个不同编号视为完整性错误。
- 没有匹配表达时，索引构建器使用调用者的可信专家主体启动独立采样子操作。
- 子操作编号、请求编号和幂等键由父索引操作编号与模型/版本/配置指纹确定性派生，并限制在 128 个 ASCII 字符内。
- 自动采样失败只排除该模型，索引构建操作继续；排除项保存稳定错误码和子操作编号。

### 9.3 索引布局

```text
models/feature_indexes/<index_id>/
  operation_owner.json
  entries.jsonl
  exclusions.json
  coverage.json
  index_manifest.json
```

`index_manifest.json` 最后发布，是索引可见性标记。

### 9.4 索引条目

条目按 `(category_id, model_id, version_id)` 排序，包含：

- 模型资产规范元数据与指纹；
- 模型发布编号、版本编号与发布证据指纹；
- 采样表达编号与指纹；
- 特征编号、特征指纹和可评分向量；
- 规范化文本词元；
- 生产或 Challenger 来源声明。

词元使用 Unicode NFKC、`casefold` 和空白/标点分隔。关键字与标签列表中的每个规范化词条同时保留为完整词元，确保中文短语可精确匹配。第一版不做模糊拼写或语言模型扩展。

### 9.5 覆盖率

```text
coverage = indexed_current_releases / eligible_current_releases
```

没有当前发布的模型不属于 `eligible_current_releases`，但单独报告。生产索引覆盖率低于 `production_minimum_coverage` 时可以构建和检查，但不能激活。Challenger 索引允许较低覆盖率，报告必须显示缺失数量和原因。

## 10. 索引发布、当前投影与回滚

### 10.1 发布历史

```text
models/feature_index_releases/<release_id>/release.json
models/current_feature_index.json
```

`action` 只允许：

- `activate`：激活新构建且覆盖率达标的生产索引；
- `rollback`：恢复一个历史发布所引用的索引。

### 10.2 并发比较

首次激活要求 `expected_current_release_id=null`。后续激活或回滚必须提供当前发布编号。锁内实际当前头与预期不一致时返回稳定冲突，不修改发布记录或当前投影。

### 10.3 完整性

激活或回滚前必须重新计算当前模型发布头集合指纹，并与目标索引的来源头指纹精确相等；因此，当模型版本已经变化时，旧索引不能直接恢复为生产索引，必须先回滚相应模型发布或基于新当前版本重建索引。

读取当前生产索引时必须依次验证：

1. 当前投影；
2. 索引发布记录及审计链；
3. 索引清单及所有条目文件指纹；
4. 配置总指纹；
5. 每个条目的模型发布、采样表达和特征证据；
6. 当前模型发布头集合仍与索引来源头集合一致。

文件、指纹或审计证据损坏返回 `model_index_integrity_error`；模型当前发布头已经正常变化返回 `model_index_stale`。不得静默跳过已进入索引的损坏或过期条目。

## 11. 查询对象与元数据提示

检索请求字段：

- `source_kind`
- `asset_id`
- `source_id`
- `instance_id`
- `index_release_id`：生产模式可省略并读取当前头
- `index_id`：实验模式必须显式提供 Challenger 索引编号；生产模式不得提供
- `top_k`：1–50，默认 10
- 可选 `keywords`、`tags`、`manufacturer`、`model_number`
- `hint_source`：提供任何可选提示时必填，只允许 `human` 或 `upstream_system`
- 审计所需主体、操作、请求和幂等标识

提示只属于本次检索运行，不修改 Phase 14 发布或模型资产。所有提示在操作启动前进入请求指纹。

## 12. 混合评分

### 12.1 默认权重

- 类别：0.20
- 关键字与标签：0.15
- 厂商与型号：0.10
- 尺寸：0.25
- 主值比例与径向形状：0.20
- 空间占用：0.10

### 12.2 元数据评分

- 类别映射相等得 1，不相等得 0；未映射则该分量缺失。
- 关键字与标签使用规范词元加权 Jaccard：标签词元权重为 2，关键字词元权重为 1，同一词元同时出现时取最大权重；交集取查询与模型权重的较小值，并集取较大值。
- 厂商完全规范匹配与型号完全规范匹配各占该分量的一半；仅提供一个字段时使用完整分量权重。
- 提示和模型字段为空时，该分量缺失而不是 0。

### 12.3 尺寸评分

对三个降序主轴跨度逐轴计算 `abs(log(model_span / object_span))`。模型跨度小于对象已观测跨度时误差乘 2.0；模型更大时乘 0.75。尺寸分数为 `exp(-三个轴加权误差的算术平均值)`。任一跨度不为正或尺寸质量低可信时该分量不参与评分。

### 12.4 形状与占用评分

- 主值比例和径向直方图均为和为 1 的非负向量，相似度精确定义为 `1 - 0.5 * L1_distance`，并夹到 0–1。
- 两者均可用时，在形状分量内各占一半；只有一个可用时占完整形状分量。
- 空间占用相似度为 `1 - abs(model_occupancy - object_occupancy)`，并夹到 0–1；轴不明确时该分量缺失。

### 12.5 权重重归一化与风险

- 只对双方均有效的分量评分。
- 有效权重除以有效权重总和后再求加权总分。
- 没有任何有效分量时返回 `no_candidate_models`。
- 几何完全退化而仅使用元数据时，报告 `metadata_only` 风险。
- 任意分量降级均在候选解释和运行摘要中列出。

### 12.6 类别过滤与稳定排序

硬过滤条件同时满足：

1. `source_kind=correction_release`；
2. 对象审查证据完整且 `review_state=confirmed`；
3. `classification_source=human_confirmed`；
4. 类别映射存在；
5. 目标类别在索引中有候选。

第 5 项不满足时退化到全索引，并记录原因。候选按 `(-score, model_id, version_id)` 排序，确保同分稳定。

## 13. 检索运行工件

```text
reports/model_retrieval/<asset_id>/<source_id>/<instance_id>/<retrieval_run_id>/
  operation_owner.json
  query_feature.json
  candidates.json
  retrieval_report.json
```

`retrieval_report.json` 最后发布，并包含：

- 查询与对象指纹；
- 索引发布编号、索引编号和全部配置指纹；
- 请求提示及来源；
- 过滤前后候选数；
- 是否类别硬过滤、是否降级及原因；
- Top-K 候选的总分、分量原始分、有效归一化权重和风险；
- 精确扫描耗时与评分候选数；
- 审计操作编号、主体和时间；
- `status=completed`。

运行目录不可覆盖。重放必须返回同一报告；相同业务输入但新操作编号会生成新的运行记录，以保留调用历史。

## 14. 审计事件

至少记录：

- `model_feature.generated`
- `model_feature.reused`
- `model_index.build_started`
- `model_index.sampling_child_started`
- `model_index.model_excluded`
- `model_index.built`
- `model_index.activated`
- `model_index.rolled_back`
- `model_retrieval.input_verified`
- `model_retrieval.category_filter_applied`
- `model_retrieval.category_filter_degraded`
- `model_retrieval.completed`

自动采样子操作必须保存父操作编号。所有事件细节只保存规范字段和内容指纹，不复制无限大小的点数组或索引内容。

## 15. 权限

- `expert`：创建配置、构建模型/对象特征、构建索引、激活、回滚和运行检索。
- `auditor`：读取索引发布、检索报告和验证后的审计快照。
- 请求体中的 `actor`、角色或可信来源字段一律忽略；生产身份只来自认证 token 映射。
- CLI 使用显式 `--actor` 构造 `source=cli` 的专家主体，与现有 Phase 15 CLI 契约一致。
- Phase 15F 引入学习配置后必须增加独立审批；固定确定性 v1 配置暂不要求构建者与发布者职责分离。

## 16. 公共接口

### 16.1 CLI

- `create-model-retrieval-config`
- `build-model-feature-index`
- `release-model-feature-index`
- `list-model-feature-indexes`
- `list-model-feature-index-releases`
- `retrieve-model-candidates`
- `show-model-retrieval`

构建、发布和检索命令必须显式提供操作编号、请求编号、幂等键与主体。领域错误输出 `<code>: <message>` 并返回退出码 2。

### 16.2 API

- `POST /model-matching/retrieval-configs`
- `GET /model-matching/retrieval-configs`
- `POST /model-matching/feature-indexes`
- `GET /model-matching/feature-indexes`
- `POST /model-matching/feature-index-releases`
- `GET /model-matching/feature-index-releases`
- `POST /model-matching/retrievals`
- `GET /model-matching/retrievals/{asset_id}/{source_id}/{instance_id}/{retrieval_run_id}`

API 必须复用现有可信 token 主体、请求体大小上限和结构化 Phase 15 错误响应。

## 17. 稳定错误

- `invalid_retrieval_input`
- `retrieval_object_not_found`
- `object_review_evidence_invalid`
- `feature_config_invalid`
- `feature_integrity_error`
- `feature_not_found`
- `model_index_not_ready`
- `model_index_coverage_rejected`
- `model_index_integrity_error`
- `model_index_stale`
- `model_index_release_conflict`
- `model_index_release_not_found`
- `no_candidate_models`
- `operation_busy`
- `permission_denied`
- `idempotency_conflict`
- `audit_persistence_error`
- `publication_recovery_required`

参数、权限、幂等冲突、完整性错误和无候选必须稳定映射；不得将领域错误降格为通用 500。

## 18. 并发、恢复与文件系统威胁边界

### 18.1 不可信状态

以下均视为不可信：

- JSON、JSONL、点数组和用户提示；
- 目录项、符号链接、Windows reparse point 和非普通文件；
- 超大文件、重复 JSON 键、非有限数字和未知字段；
- 被并发修改或人工篡改的发布、表达、特征、索引和投影。

### 18.2 候选四状态

特征、索引和检索运行均使用：

- `ABSENT`
- `OWNED_RECOVERABLE`
- `VERIFIED_PUBLISHED`
- `UNCERTAIN`

首次操作冻结 `operation_owner.json`，内容文件逐个规范发布，最终清单最后发布。相同操作验证已有字节后原位继续；不同操作不得接管。

### 18.3 禁止的恢复动作

- 不自动递归删除候选目录；
- 不自动 quarantine、rename 或移动不确定目录；
- 不根据经过时间判断所有者已经死亡；
- 不覆盖已验证发布；
- 不把链接后持久化不确定误判成“未发布”。

### 18.4 当前投影

索引发布使用专用内核资源锁。当前投影更新必须在锁内比较预期头、验证目标索引、发布不可变 release，再更新投影并确认父目录持久性。投影可由不可变 release 历史确定性恢复。

## 19. 资源限制与性能

- 单对象最多 2,000,000 点。
- 生产索引最多 100,000 条目；超限返回配置错误，不静默截断。
- `top_k` 最大 50。
- API 沿用现有 Phase 15 请求体上限；提示词元总数和单项长度必须有界。
- 单个配置、清单、特征、覆盖率、排除清单、审查证据或检索报告 JSON 上限为 16 MiB。
- Phase 14 `labels.json` 上限为 512 MiB，且同时受 2,000,000 点数量限制。
- `entries.jsonl` 上限为 1 GiB、100,000 行、每行 64 KiB；检索采用有界流式逐行处理。
- 第一版执行精确扫描；报告候选数、过滤数和耗时，不设置依赖机器性能的脆弱墙钟测试。
- 当真实生产索引超过 50,000 条或持续不满足业务延迟目标时，再评估 SQLite/ANN 后端；公共索引和检索契约不变。

## 20. 测试与验收

### 20.1 Phase 14 契约

- 最终确认、确认后改类、确认后合并/拆分、撤销/重做和恢复的对象级证据。
- 发布工件原子性、不可覆盖和旧发布兼容。
- 对象指纹与点、类别或实例篡改检测。

### 20.2 特征

- 点顺序、平移和刚性旋转下的确定性。
- 部分遮挡、低点数、共线、共面、对称和退化形状。
- 非有限坐标、超限点数、配置篡改和源证据篡改。
- 模型与对象使用相同特征契约。

### 20.3 索引

- 生产只选择当前模型版本。
- 历史版本只进入显式 Challenger。
- 缺失采样自动补齐、子操作可追溯、失败排除和覆盖率门禁。
- 稳定排序、确定性字节、不可变发布和完整性验证。
- 激活、并发预期头冲突、历史查询和回滚。

### 20.4 检索评分

- 各元数据与几何分量独立验证。
- 模型小于对象时的非对称惩罚。
- 缺失分量权重重归一化。
- 类别硬过滤、旧发布软评分、无候选自动降级。
- 同分稳定排序、Top-K 范围和 `metadata_only` 风险。

### 20.5 安全与恢复

- 并发同资源操作只有一个发布者。
- 所有者冻结、内容写入、最终清单、审计完成和当前投影各中断点可幂等恢复。
- 符号链接、reparse point、非普通文件、重复键和超大内容失败关闭。
- 不确定候选不会被删除、移动或接管。

### 20.6 公共接口与端到端

- CLI 与 API 的权限、错误码、幂等重放和请求限制。
- Phase 14 发布对象 → 对象特征 → 当前索引 → 可解释 Top-10 的完整闭环。
- Phase 13A 专家实验输入与生产结果隔离。
- 索引回滚后新检索使用恢复索引，历史检索报告保持不变。
- 全部 Phase 1–15B-1 回归测试通过。

## 21. 完成定义

Phase 15B-2 只有在以下条件全部满足时才算完成：

- 新 Phase 14 发布保存可验证的对象级审查证据，旧发布安全降级；
- 特征工件确定、不可变且同时支持模型与对象；
- 生产/Challenger 索引隔离，生产索引只使用当前模型版本；
- 缺失采样自动补齐且全部自动操作可审计；
- 索引覆盖率门禁、激活、历史和回滚可用；
- Top-K 提供完整评分解释、过滤/降级原因和风险；
- CLI、API、审计、并发恢复和端到端测试通过；
- 文档以中文为主并明确 Phase 15C–15F 非目标；
- 完整仓库测试、语法检查、差异检查和占位符扫描通过。

## 22. 后续阶段接口

Phase 15C 读取 Phase 15B-2 候选的模型采样表达、对象特征配置和候选解释，执行刚性粗配准、多尺度 ICP 与残差门禁。Phase 15D 保存人工选择、拒绝和对象—模型绑定，并引用 `retrieval_run_id`。Phase 15F 才能消费聚合后的真实决策数据进行受控权重优化；任何 Challenger 配置都必须通过独立评估、审批、推广和回滚，不能自动替换生产配置。
