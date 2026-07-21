# Phase 14 点云分割纠正闭环

Phase 14 把 Phase 13A 的自动分割结果变成可确认、可纠正、可恢复、可审查和不可变发布的标签版本。默认工作方式不是从零标注：算法先完成分割，人员只处理系统建议或肉眼确认的问题。

## 工作流

```text
completed Phase 13A run
  -> draft correction session
  -> confirm / merge / split / relabel / mark_noise
  -> undo / redo / restore
  -> in_review
  -> published immutable release
  -> derived Phase 13B benchmark
  -> versioned segmentation_feedback dataset
```

纠正范围严格等于 Phase 13A 运行实际处理的有界点集。原始 LAS/LAZ、Phase 13A 运行及历史发布版本均不会被修改。

## 创建会话

```powershell
python -m pc_system.cli create-segmentation-correction `
  --project-root workspace `
  --asset-id scan `
  --run-id seg-run-001 `
  --session-id correction-001 `
  --sample-id scan-001 `
  --actor alice
```

可通过 `--benchmark-id` 叠加已有 Phase 13B 标签；未标注点继续使用自动分割结果。通过 `--baseline-release-id` 可以从历史发布版本创建新草稿。恢复不会修改旧版本，新发布版本用 `supersedes_release_id` 建立谱系。

每个点均保留精确 `source_point_index`。浏览器可以投影、抽样显示或改变观察方向，但发给服务端的选择必须是 API 返回的精确索引。

## 纠正操作

事件文件使用 append-only JSONL。操作名称是稳定的英文标识：

| 操作 | 事件类型 | 规则 |
| --- | --- | --- |
| 确认 | `confirm` | 标记一个或多个对象经人工确认，不接受系统建议自动代替确认。 |
| 合并 | `merge` | 至少选择两个活动对象，并指定其中一个作为目标。 |
| 拆分 | `split` | 选择单一对象的非空真子集；系统生成确定性新实例 ID。 |
| 修改类别 | `relabel` | 对活动对象设置经过标识符校验的类别。 |
| 标为噪点 | `mark_noise` | 将精确点索引改为噪点。 |
| 恢复噪点 | `restore_from_noise` | 将当前噪点恢复到一个活动对象。 |
| 撤销 | `undo` | 追加撤销事件，不删除历史。 |
| 重做 | `redo` | 追加重做事件。 |
| 恢复 | `restore` | 支持全部、点集合或对象集合恢复到不可变基线。 |

操作文件示例：

```json
{
  "type": "split",
  "instance_id": "obj-001",
  "source_point_indices": [21, 22, 23]
}
```

```powershell
python -m pc_system.cli apply-segmentation-correction `
  --project-root workspace `
  --asset-id scan `
  --session-id correction-001 `
  --actor alice `
  --expected-revision 0 `
  --client-request-id split-request-001 `
  --operation split.json
```

`expected_revision` 提供乐观并发控制。陈旧修订返回 HTTP 409；同一样本的活动编辑者锁冲突返回 HTTP 423。重复的 `client_request_id` 对同一人员是幂等的。

## 浏览器工作台

打开：

```text
frontend/correction.html?asset_id=scan&session_id=correction-001&api=http://127.0.0.1:8000
```

`correction.html` 包含：

- 左侧系统建议队列；
- 中间原生 Canvas 点视图、俯视/前视/侧视和对象/框选/套索/画笔入口；
- 右侧对象优先的确认、合并、拆分、改类和噪点操作；
- 底部 undo、redo、restore 与提交审查。

系统建议与“人工已确认”状态分开显示。常用对象操作保持一至两步，高级点工具折叠显示。

## 审查与发布

```powershell
python -m pc_system.cli submit-segmentation-correction `
  --project-root workspace `
  --asset-id scan `
  --session-id correction-001 `
  --actor alice `
  --expected-revision 4
```

发布配置：

```json
{
  "release_id": "scan-labels-v1",
  "reviewer": "bob",
  "expected_revision": 5,
  "benchmark_split": "development",
  "license": "internal"
}
```

```powershell
python -m pc_system.cli publish-segmentation-correction `
  --project-root workspace `
  --asset-id scan `
  --session-id correction-001 `
  --publication publication.json
```

只有 `in_review` 会话可以发布。发布状态是 `published`，发布目录是 immutable：相同 `release_id`、派生 benchmark ID 或反馈数据 ID 均不能覆盖。

## 产物

草稿：

```text
reports/segmentation_corrections/<asset>/<session>/
  correction_session.json
  baseline_labels.json
  events.jsonl
  draft_labels.json
  draft_objects.json
  review_queue.json
  correction_diff.json
```

发布：

```text
reports/segmentation_correction_releases/<asset>/<release>/
  correction_release.json
  labels.json
  objects.json
  correction_diff.json
  provenance.json
  publication_tasks.json
  training_policy.json
```

反馈：

```text
datasets/segmentation_feedback/<release>/
  feedback_manifest.json
  before_labels.json
  after_labels.json
  operations.jsonl
```

服务还会创建 `benchmarks/<release>-benchmark/`，可由 Phase 13B 读取和评估。

## 中断恢复

事件先持久化，再物化草稿。若进程在两者之间中断，下次读取会话时会从 `baseline_labels.json` 与 `events.jsonl` 确定性重放，重建草稿、对象、队列和差异，并记录 `recovered_from_event_log`。

必要发布文件先写入同盘短 staging 目录。任一必要文件失败时清理 staging，不留下正式发布、benchmark 或反馈目录。发布完成后的下游评估或检索状态记录在 `publication_tasks.json`，下游失败不会使已发布标签失效。

## API

主要读取接口：

```text
GET /segmentation-corrections/<asset_id>
GET /segmentation-corrections/<asset_id>/<session_id>
GET /segmentation-corrections/<asset_id>/<session_id>/points
GET /segmentation-corrections/<asset_id>/<session_id>/objects
GET /segmentation-corrections/<asset_id>/<session_id>/queue
GET /segmentation-corrections/<asset_id>/<session_id>/events
GET /segmentation-correction-releases/<asset_id>
GET /segmentation-correction-releases/<asset_id>/<release_id>
```

所有写接口使用既有 `X-API-Key` 保护：

```text
POST /segmentation-corrections/<asset_id>
POST /segmentation-corrections/<asset_id>/<session_id>/events
POST /segmentation-corrections/<asset_id>/<session_id>/submit
POST /segmentation-corrections/<asset_id>/<session_id>/return
POST /segmentation-corrections/<asset_id>/<session_id>/publish
POST /segmentation-correction-releases/<asset_id>/<release_id>/retry
```

生产模式必须配置 `PC_SYSTEM_API_KEY`。

## 自学习边界

Phase 14 生成后续自训练需要的纠正前后标签、操作、来源指纹、审查者、数据许可与训练资格，但不会启动训练或自动替换生产模型。

- `development`：在许可兼容且发布有效时为 `eligible`。
- `validation`：`evaluation-only`。
- `golden_regression`：`evaluation-only`，绝对禁止作为训练或参数检索输入。

未来训练阶段只能消费显式 `eligible` 的发布版本，并继续执行 Champion/Challenger 评估、漂移检查、回归门禁和受控晋升。人员纠正是高价值反馈，但不能绕过独立验证直接“自我进化”到生产。
