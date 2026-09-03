# SDD ledger — plan: docs/superpowers/plans/2026-09-03-phase15e-scanned-reference-templates.md

Task 1: complete (commit 8892b5e, prior-session review clean)
Task 2: complete (commit 67e54fe, prior-session review clean)
Task 3: complete (commit 059bd42, prior-session review clean)
Task 4: complete (commit 6ae2dad, prior-session review clean)

## 任务接缝预检

| 任务关系 | 生产者 → 消费者 | 结论 |
|---|---|---|
| 1 → 2、3 | 标准化/质量纯函数 → 读取器与导入 | 已由任务 2、3 聚焦与全仓回归验证，无冲突。 |
| 2 → 3 | 有界真实格式读取 → 冻结导入 | 已完成，无冲突。 |
| 3 → 4、5、7 | 扫描版本与来源类型 → 核验、统一表达、公共入口 | 任务 5 必须从受验证版本分派，不能探测目录。 |
| 4 → 5、7 | approved 核验与发布 1.1 → 索引准入与状态投影 | 生产只接当前发布，Challenger 可显式接历史发布；两者都必须已核验。 |
| 5 → 6 | `representation_type` 与统一点读取 → 候选/配准 | 任务 5 固定表达和特征/索引证据，任务 6 才升级候选及配准报告。 |
| 5 → 9 | 配置/特征/索引 1.1 → 全链验收 | 本任务只做聚焦验证；阶段全仓和业务样本结论留任务 9。 |
| 6 → 9 | 匹配/绑定兼容 → 全链验收 | 无当前冲突。 |
| 7 → 8、9 | API/CLI 状态投影 → 页面和全链验收 | 页面不复制领域算法。 |
| 8 → 9 | 页面流程 → 浏览器验收 | 无当前冲突。 |
| 2、3、4、7、8 → 9 | CI/资料/API/页面共享文件 → 阶段收尾 | 任务 9 只汇总、验收，不重写已批准语义。 |

| 任务 | 自洽检查 |
|---|---|
| 1 | 纯几何接口、文件与测试一致，已完成。 |
| 2 | 真实读取与受限进程边界一致，已完成。 |
| 3 | 冻结、配额、不可变提交与读取门禁一致，已完成。 |
| 4 | 一次最终核验、发布 1.1、旧 CAD 1.0 兼容一致，已完成。 |
| 5 | 新统一读取入口、配置/特征/索引 1.1 与旧 1.0 分支一致；不自动发布索引。 |
| 6 | 只传播来源证据，不改变刚性门禁或绑定 schema。 |
| 7 | 扫描专用入口复用领域服务，权限与响应裁剪一致。 |
| 8 | 三步页面只消费有界预览与状态投影。 |
| 9 | 阶段验收明确区分工程夹具和真实业务样本。 |

Ruling: 官方 SDD 脚本在当前 Windows 环境没有 Bash，且 `task-brief` 只识别英文 `Task N`、无法识别项目中文“任务 N”；为保持中文资料要求，工作区、台账与任务简报按相同路径和内容契约手工创建，不改写项目计划标题。若此裁定错误，成本仅是 SDD 临时材料无法由脚本重建，不影响生产工件或 Git 历史。

Task 5: complete（待独立复审；base 6ae2dad）

### 任务 5 验证证据

- RED：`uv run --extra test pytest tests/test_phase15e_reference_index.py -q --basetemp ... -p no:cacheprovider`，5 项按预期失败于缺失 1.1 配置支持、缺失扫描表达模块及旧 CAD 路径误分派。
- GREEN：同一命令在补充缺类型与索引发布 1.1 回归后为 `7 passed in 8.72s`。
- 15B-2 聚焦回归：按 Windows 运行时上限分段执行，共 `109 passed`（42 + 31 + 15 + 14 + 7）；CLI/API 段保留第三方 `Starlette`/`anyio` 弃用警告。
- 静态检查：`uv run python -m compileall -q src` 成功；`git diff --check` 无输出。
