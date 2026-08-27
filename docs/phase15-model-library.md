# Phase 15A CAD 模型库操作与集成说明

Phase 15A 提供可审计、不可覆盖的 CAD 模型资产与模型版本基础。它解决“模型如何安全入库、如何追溯、如何被后续检索和配准消费”，不宣称已经完成点云对象自动匹配。

## 1. 范围与后续边界

Phase 15A 已包含：

- 模型资产目录及稳定 `model_id`；
- STL、OBJ、PLY 网格导入；
- `mm`、`cm`、`m` 单位校验并统一换算为米；
- 不可变模型版本、文件指纹和几何摘要；
- CLI、API、可信身份、幂等操作和哈希链审计；
- 导入中断、失败记录与受控重试。

后续能力保持独立：Phase 15B 实现确定性采样、标准化特征和混合候选检索；Phase 15C 实现刚性粗配准、ICP 精配准与质量门禁；Phase 15D 实现人工决策、模型绑定和双界面；Phase 15E 支持实物参考点云模板；Phase 15F 支持受控参数优化。未经验证的结果不会自动替换生产配置。

## 2. 安装与格式

核心包保持轻量。真实 STL、OBJ、PLY 网格读取需要模型可选依赖：

```powershell
pip install -e ".[models]"
```

源文件扩展名必须与支持格式一致。系统保留原始源文件字节，对源文件、`source_geometry.json` 和 `model_manifest.json` 计算 SHA-256 指纹。未知格式、未知单位、空网格、非有限坐标或不合法面索引会被拒绝。

单位换算规则：

| 声明单位 | 换算到米 |
| --- | ---: |
| `mm` | `0.001` |
| `cm` | `0.01` |
| `m` | `1.0` |

`source_geometry.json` 保存米制边界（可据此计算尺寸）、顶点数和面数；`model_manifest.json` 同时保留声明单位、换算比例、来源、许可和工件指纹。

## 3. 操作员 CLI

以下示例使用非敏感演示标识。`operation_id`、`request_id` 和 `idempotency_key` 必须稳定且满足标识符规则。

创建模型资产：

```powershell
python -m pc_system.cli create-model-asset `
  --project-root workspace `
  --model-id pump-a `
  --display-name "Pump A" `
  --category-id pump `
  --manufacturer Acme `
  --model-number A-100 `
  --keyword centrifugal `
  --tag pump `
  --actor alice `
  --operation-id op-model-001 `
  --request-id request-model-001 `
  --idempotency-key idem-model-001
```

准备来源说明并导入版本：

```powershell
'{"supplier":"Acme","source":"approved-cad"}' |
  Set-Content -Encoding UTF8 provenance.json

python -m pc_system.cli import-model `
  --project-root workspace `
  --model-id pump-a `
  --version-id v1 `
  --source imports/models/pump-a.obj `
  --unit mm `
  --license internal `
  --provenance provenance.json `
  --actor alice `
  --operation-id op-import-001 `
  --request-id request-import-001 `
  --idempotency-key idem-import-001
```

CLI 只接受 JSON 对象形式的 provenance。无效 JSON、非 UTF-8 内容或不安全路径会返回退出码 2，并用稳定错误码记录拒绝；原始敏感路径和输入不会写入审计。

## 4. API 集成

可用接口：

| 方法与路径 | 角色 | 用途 |
| --- | --- | --- |
| `GET /model-library` | 公开读取 | 列出模型资产摘要 |
| `POST /model-library/models` | `expert` | 创建模型资产 |
| `GET /model-library/models/{model_id}` | 公开读取 | 读取资产和版本 |
| `POST /model-library/models/{model_id}/versions` | `expert` | 导入模型版本 |
| `GET /audit/operations/{operation_id}` | `auditor` | 读取验证后的操作快照 |

API 导入不接受任意服务器路径。集成方必须先把来源文件放入项目的 `imports/models` 目录，再在请求中提交相对 `staged_source`。服务会拒绝绝对路径、目录穿越、符号链接和重解析点逃逸。

生产模式身份由服务器配置绑定，而不是相信请求正文或角色请求头。将令牌到主体的映射写入环境变量 `PC_SYSTEM_PRINCIPALS_JSON`，并另外为现有生产写接口配置通用写入密钥：

```powershell
$env:PC_SYSTEM_PRINCIPALS_JSON='{"<phase15-api-token>":{"actor_id":"model-integrator","roles":["expert","auditor"]}}'
$env:PC_SYSTEM_WRITE_KEY='<general-write-api-key>'

python -m pc_system.cli serve-api `
  --project-root workspace `
  --host 127.0.0.1 `
  --port 8000 `
  --mode production `
  --api-key $env:PC_SYSTEM_WRITE_KEY
```

Phase 15 请求把绑定表中的令牌放在 `X-API-Key` 请求头中。生产模式忽略 `X-Actor-ID` 和 `X-Actor-Roles`，防止调用方伪造身份；这两个请求头只允许在开发模式中提供测试主体。示例占位符必须替换，并通过安全配置渠道注入，不能提交到版本库。持久审计只记录 `configured_token` 来源，不记录令牌原文。

## 5. 不可变存储布局

```text
models/<model_id>/
  model_asset.json
  versions/<version_id>/
    model_manifest.json
    source_geometry.json
    source/model.<ext>

reports/model_matching_operations/<operation_id>/
  operation.json
  events.jsonl
```

资产和已发布版本均不可覆盖。同一 `version_id` 再次发布会返回 `model_version_exists`。新版本可显式声明 `supersedes_version_id`，但旧版本仍保留。禁止通过删除旧目录实现更新。

## 6. 操作、幂等和审计

每个写操作必须先建立审计信封，再处理业务数据：

1. `operation.json` 冻结主体、请求、幂等键哈希和请求指纹；
2. `events.jsonl` 以事件哈希和前一事件哈希形成哈希链；
3. 同一幂等键与相同请求返回既有结果；
4. 同一幂等键与不同请求返回 `idempotency_conflict`；
5. 成功操作以 `operation.completed` 结束，确定失败以 `operation.failed` 或 `operation.start_failed` 结束。

审计启动采用原位恢复：系统不会在请求路径中自动 quarantine、rename 或递归删除失败操作目录。若创建目录、写入信封、发布幂等索引或写入首事件之间发生中断，同一操作可在锁内恢复；竞争失败者在自己的规范目录中留下 `idempotency_race_lost` 证据。

读取审计应使用验证后的快照接口。缺失、重排、篡改或跨操作移植事件会导致 `audit_integrity_error`，不会被静默修复。

## 7. 稳定错误与恢复

| 错误码 | 含义与处理 |
| --- | --- |
| `invalid_model_asset` | 修正模型资产标识符、类别或目录字段后，使用新的 `operation_id`、`request_id` 和 `idempotency_key` 重试 |
| `invalid_model_format` | 将源文件转换为受支持的 STL、OBJ 或 PLY 格式，再使用三项新标识重试 |
| `invalid_model_version` | 修正版本标识符、单位、来源或请求结构后，使用三项新标识重试 |
| `invalid_model_geometry` | 修复网格坐标、面索引或空几何后，使用三项新标识重试 |
| `model_version_exists` | 不覆盖旧版本；使用新的 `version_id` 或读取既有版本 |
| `idempotency_conflict` | 不复用已经绑定到其他请求的幂等键 |
| `operation_busy` | 等待当前持有者完成后，使用完全相同的请求和三项标识重试 |
| `publication_recovery_required` | 版本可能已可见；使用完全相同的请求和三项标识重试，让系统核对发布与审计 |
| `audit_integrity_error` | 停止自动处理，保存现场并交由审计人员核查 |
| `audit_persistence_error` | 检查磁盘、权限与文件系统能力后重试 |

发布前失败不会生成最终版本目录。为避免跨平台误删，已验证归属的 staging 可能原位保留并记录 `model_version.cleanup_deferred`；清理由独立维护流程处理，不属于业务请求自动动作。若目录发布后、审计完成前发生中断，最终版本可能已经可见，系统返回 `publication_recovery_required`；必须使用完全相同的请求重试，让系统核对并收敛发布与审计状态。

## 8. 后续检索和配准如何消费

Phase 15B 从模型资产目录和每个不可变版本读取检索输入：

- `model_asset.json` 提供类别、制造商、型号、关键词和标签等目录元数据；
- `model_manifest.json` 提供来源、许可、单位、版本关系、工件指纹，以及不可变源网格的相对路径；
- 确定性采样读取 `model_manifest.json` 引用的不可变源网格；`source_geometry.json` 仅提供米制边界（可据此计算尺寸）、顶点数、面数和换算校验元数据；
- 特征索引记录所使用的模型版本、算法版本和配置指纹；
- Phase 15C 配准结果必须绑定同一模型版本，不能只绑定可变名称。

因此，模型检索、刚性配准和对象绑定都可追溯到确切源文件与审计操作。
