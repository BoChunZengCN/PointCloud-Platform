# Phase 15B-1 版本化模型发布与确定性采样

## 1. 功能范围

Phase 15B-1 在 Phase 15A 不可变 CAD 模型库之上提供：

- 已导入模型版本的激活、历史发布查询和受审计回滚；
- STL、OBJ、PLY 网格的确定性表面采样；
- 同一版本下多套不可变 `cad_sampled` 点表达；
- 相同源版本、点数、随机种子和算法版本的确定性复用；
- 发布、采样、复用和回滚操作的完整审计链。

本阶段不包含描述子、Top-K 检索、刚性配准、对象绑定、采样 API 或前端页面。这些能力分别属于 Phase 15B-2、15C 和后续阶段。

## 2. 不可变数据与当前发布

模型版本和采样表达发布后不可覆盖。激活或回滚只推进当前发布投影，不修改历史版本和历史采样数据。

```text
models/<model_id>/
├── versions/<version_id>/
│   ├── model_manifest.json
│   └── source_geometry.json
├── releases/<release_id>/release.json
├── current_release.json
└── representations/<version_id>/cad_sampled/<representation_id>/
    ├── operation_owner.json
    ├── sampled_points.json
    └── representation.json
```

`representation.json` 是采样表达的最终可见性标记。读取和列表操作会校验表达内容、源版本证据和审计证据；缺失、畸形或被篡改的数据不会作为有效结果返回。

## 3. 发布与回滚

激活版本：

```powershell
python -m pc_system.cli release-model-version `
  --project-root .\workspace `
  --model-id pump-a `
  --version-id v2 `
  --release-id release-002 `
  --action activate `
  --expected-current-release-id release-001 `
  --reason "批准 v2 投产" `
  --actor alice `
  --operation-id op-release-002 `
  --request-id req-release-002 `
  --idempotency-key idem-release-002
```

回滚到历史版本：

```powershell
python -m pc_system.cli release-model-version `
  --project-root .\workspace `
  --model-id pump-a `
  --version-id v1 `
  --release-id release-003 `
  --action rollback `
  --expected-current-release-id release-002 `
  --rollback-of-release-id release-001 `
  --reason "回滚到已验证版本" `
  --actor alice `
  --operation-id op-release-003 `
  --request-id req-release-003 `
  --idempotency-key idem-release-003
```

查询历史发布：

```powershell
python -m pc_system.cli list-model-releases `
  --project-root .\workspace `
  --model-id pump-a
```

`--expected-current-release-id` 用于并发保护。当前发布已变化时，调用会失败，不会覆盖另一位用户刚完成的发布。

## 4. 生成与查询采样表达

点数和随机种子必须显式提供：

```powershell
python -m pc_system.cli sample-model-version `
  --project-root .\workspace `
  --model-id pump-a `
  --version-id v2 `
  --point-count 100000 `
  --random-seed 20260828 `
  --actor alice `
  --operation-id op-sample-v2-001 `
  --request-id req-sample-v2-001 `
  --idempotency-key idem-sample-v2-001
```

成功后标准输出仅包含已验证的最终 `representation.json` 路径。使用新操作编号提交相同配置会返回同一路径，并记录 `model_sampling.representation_reused`，不会生成重复表达；使用原操作编号和原幂等键重放，则记录 `operation.replayed`。

查询某个版本的所有有效采样表达：

```powershell
python -m pc_system.cli list-model-representations `
  --project-root .\workspace `
  --model-id pump-a `
  --version-id v2
```

输出为 JSON 数组，包含表达编号、点数、随机种子、算法版本、内容指纹和源版本证据。

## 5. 错误、恢复与审计

CLI 的领域校验错误返回退出码 `2`，格式为 `<错误码>: <说明>`。主要稳定错误包括：

- `invalid_sampling_config`：点数或随机种子无效；
- `model_version_integrity_error`：源版本证据缺失或被篡改；
- `model_representation_integrity_error`：采样表达不完整或校验失败；
- `operation_busy`：同一资源正在被其他操作安全处理；
- `publication_recovery_required`：发布已进入需要幂等重试恢复的状态。

发生 `operation_busy` 或 `publication_recovery_required` 时，应使用原来的操作编号、请求编号和幂等键重试，不要手工删除、移动或覆盖候选目录。

每次发布、回滚、采样和复用均通过 Phase 15 审计账本记录可信主体、请求指纹、事件链和终态。生产操作必须使用唯一且可追溯的标识；实验采样应使用独立工作区或独立模型版本，不能覆盖生产表达。

## 6. 当前集成边界

Phase 15B-1 暂不开放采样 HTTP API。调用方应使用上述 CLI 或直接调用经过验证的领域函数；后续 API 必须复用同一权限、幂等、不可变发布和审计边界，不能另建旁路。

下一步 Phase 15B-2 将基于这些不可变采样表达生成版本化几何描述子，并与关键字、类别、厂商、型号和标签共同形成可解释的 Top-K 候选检索。
