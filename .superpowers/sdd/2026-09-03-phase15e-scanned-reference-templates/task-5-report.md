# 任务 5 报告：统一表达、特征配置与检索索引

## 实现

- 新增统一表达读取入口，按已核验模型资产的 `source_family` 分派 CAD 与扫描表达，拒绝未知来源；不以目录探测决定来源。
- 新增扫描参考表达发布：复用 `load_bundle`、`approved_review_evidence`、内核锁、操作审计和不可变精确发布；表达固定源版本、质量、核验、生成配置和几何指纹，点集采用 `sha256_point_subset_v1`。
- 特征配置 1.1 保留 CAD `sampling`，增加独立 `scanned_sampling`；1.0 保持原有 CAD 计算/读取，扫描源在旧配置下以 `feature_config_invalid` 进入覆盖率排除。
- 特征、索引和索引发布支持 1.1，索引行固定 `representation_type`、表达/源几何/清单以及扫描质量/核验指纹；读取时重新验证表达与特征证据。生产与 Challenger 仍沿现有发布来源和覆盖率报告流程。

## 文件

- 新增：`src/pc_system/model_representation.py`、`src/pc_system/reference_representation.py`、`tests/test_phase15e_reference_index.py`。
- 修改：`model_sampling.py`、`model_retrieval_config.py`、`model_feature_store.py`、`model_feature_index.py`、`model_index_release.py`、本台账。

## RED / GREEN 与验证

- RED：Task 5 测试 5 项失败，原因是 1.1 配置结构不支持、扫描表达模块缺失、旧 CAD 选样路径错误地处理扫描版本。
- GREEN：`tests/test_phase15e_reference_index.py`：`7 passed in 8.72s`。
- 15B-2：分段 `109 passed`；CLI/API 子集有既存 `Starlette`、`anyio` 弃用警告。
- `uv run python -m compileall -q src` 成功；`git diff --check` 无输出。

## 自审

- 生产索引未自动构建或启用；没有修改候选、配准、绑定、API、CLI 或 UI。
- 新旧 schema 分支均由加载时配置/工件证据约束，篡改或缺失表达类型会在索引读取时失败关闭。
- `model_sampling.py` 的变更只为 CAD 读取增加经资产来源的失败关闭门禁；`model_index_release.py` 只把发布记录 schema 与已验证索引 schema 绑定。

## 问题与后续

- 本机 Windows 测试运行时对默认临时目录无访问权，因此使用短工作区临时根分段执行。`uv.lock`、`.venv` 及测试临时目录均不是 Task 5 产物，不会暂存；受权限影响的 `.test-tmp-red/`、`.test-tmp-green/` 若不能安全删除，将在交付时保留为环境清理事项。
