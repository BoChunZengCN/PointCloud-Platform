# Phase 15C 刚性配准操作说明

Phase 15C 把 Phase 15B-2 的候选模型与已审查对象点云进行刚性对齐，输出可审计的三态质量报告。本阶段不会自动创建对象—模型绑定；人工确认与不可变绑定属于 Phase 15D。

## 安装可选引擎

核心系统不强制安装 Open3D。生产配准节点使用：

```powershell
uv sync --extra registration
```

Open3D 缺失、低于 0.19 或达到 1.0 以上时，执行返回 `registration_engine_unavailable`，审计操作保留诊断性失败报告。

## 配置与执行

配准配置是不可变版本，所有距离单位为米、角度单位为弧度：

```powershell
pc-system publish-model-registration-config --project-root workspace --config-id registration-v1 --config registration-v1.json --actor alice --operation-id op-config-1 --request-id req-config-1 --idempotency-key idem-config-1
pc-system register-model-candidate --project-root workspace --registration-id registration-1 --asset-id scan-a --source-id release-a --instance-id object-a --retrieval-run-id retrieval-1 --candidate-rank 1 --config-id registration-v1 --actor alice --operation-id op-registration-1 --request-id req-registration-1 --idempotency-key idem-registration-1
pc-system show-model-registration --project-root workspace --asset-id scan-a --source-id release-a --instance-id object-a --registration-id registration-1
```

API 对应提供 `POST/GET /model-matching/registration-configs`、`POST /model-matching/registrations` 和配准报告 GET 资源。

## 三态质量结论

- `passed`：双向覆盖、残差、尺寸和姿态区分度满足配置。
- `review_required`：部分遮挡、阈值缓冲区、精配准轻微退化或对称姿态歧义，需要人工确认。
- `rejected`：覆盖、残差或尺寸明显不合格，或没有合法粗/精配准结果。

`rejected` 是成功完成计算后的质量结论，API 仍返回 200；引擎故障才返回 503。

## 证据、恢复与审计

正式运行只接受检索契约 1.1。旧 1.0 报告必须重新执行检索，系统不会从当前索引猜测缺失表达。对象指纹变化返回 `object_fingerprint_stale`。

运行目录保存输入快照、初始假设、粗配准、精配准、残差、预览和正式报告。正式报告最后发布；若工件可见但审计终态未确认，返回 `publication_recovery_required`，使用完全相同的请求与幂等键重试即可原位验证并完成。审计可通过 `/audit/operations/{operation_id}` 查询。

`rigid_transform_4x4` 的唯一方向是“模型坐标 → 对象点云坐标”。预览工件只用于显示，不是质量计算或后续绑定的权威输入。

## 阶段边界

Phase 15C 不修改点云分割结果、不推广配置、不训练模型，也不自动形成 `model_binding`。Phase 15D 将在三态报告之上增加人工确认、换候选、拒绝、历史查询和不可变绑定。
