# Phase 15A 审计启动原位恢复实施计划

**状态：** 已完成

> **执行要求：** 使用 `superpowers:executing-plans` 逐项执行；生产代码必须遵循测试驱动开发，完成前使用 `superpowers:verification-before-completion` 验证。

**目标：** 将模型匹配审计操作的启动失败处理从“移动/隔离/删除未启动目录”改为“在原操作目录内恢复或终态化”，保证操作编号永久可追溯，并在并发、崩溃与存储故障下保持单一幂等赢家。

**架构：** `operation.json` 是启动初始化信封，`events.jsonl` 是生命周期事实。每个操作只使用现有的操作级初始化锁串行化恢复；幂等索引继续使用原子的“不覆盖发布”。重试时，系统验证已有初始化信封与当前请求是否一致，然后根据索引可见性追加一次 `operation.started`，或追加一次 `operation.start_failed`。任何不一致、损坏或生命周期冲突均失败关闭且不改动现有证据。

**技术栈：** Python 3.12、pytest、文件系统原子硬链接、JSON/JSONL 审计账本、现有跨平台内核字节锁。

**规格依据：** `docs/superpowers/specs/2026-08-24-phase15a-in-place-audit-start-recovery-design.md`

**范围约束：** 生产代码只修改 `src/pc_system/model_matching_audit.py`；主要契约测试修改 `tests/test_phase15a_audit.py`。若聚焦集成测试发现 API 仍断言已废弃的空目录冲突契约，可只更新对应的 `tests/test_phase15a_api.py` 集成断言。不混入 CLI 路径修复、功能清单、项目记忆或其他文档提交；不重写 Git 历史。

---

## 任务 1：用行为测试固定原位恢复契约

**文件：**

- 修改：`tests/test_phase15a_audit.py`

### 步骤 1：替换废弃的隔离/删除测试

删除只验证 `_discard_unstarted_operation`、私有 `.d` 隔离目录、目录重命名和隔离容量的测试。它们保护的是已被规格废弃的内部实现，不应继续阻止安全架构收敛。

新增或调整下列面向公开行为的测试：

- `test_retry_recovers_matching_projection_before_index_once`
  - 首次在初始化信封写入后模拟进程中断；
  - 同一 `operation_id`、同一请求重试；
  - 断言目录未移动、索引生成、仅一个 `operation.started`。
- `test_retry_recovers_own_visible_index_before_started_once`
  - 构造“索引已指向自身但事件尚未写入”；
  - 同一操作重试后只追加一次启动事件；再次重试不重复生命周期事件。
- `test_losing_idempotency_claim_is_terminalized_in_place`
  - 并发竞争中另一操作赢得索引；
  - 断言失败操作仍位于规范目录，只有一个 `operation.start_failed`，错误码为 `idempotency_race_lost`；
  - 返回赢家的幂等重放结果。
- `test_index_publication_failure_is_terminalized_in_place`
  - 索引发布确定失败；
  - 断言失败操作原位保留并只有一个 `operation.start_failed`，错误码为 `audit_persistence_error`。
- `test_unconfirmed_publication_preserves_running_candidate_for_retry`
  - 索引可见但目录持久性确认失败；
  - 首次返回 `audit_persistence_error` 且不写终态；
  - 同一操作重试后只写一个 `operation.started`。
- `test_existing_operation_envelope_mismatch_fails_without_mutation`
  - 使用相同 `operation_id` 但不同请求信封重试；
  - 断言 `operation.json` 与 `events.jsonl` 字节不变，并返回完整性错误。
- `test_same_idempotency_key_has_one_started_winner_and_terminal_losers`
  - 并发启动多个不同操作；
  - 断言一个启动赢家，其余操作各自原位终态化，无全局隔离锁错误。

### 步骤 2：运行新增测试并确认 RED

运行：

```powershell
uv run pytest -q tests/test_phase15a_audit.py -k "retry_recovers_matching_projection_before_index_once or retry_recovers_own_visible_index_before_started_once or losing_idempotency_claim_is_terminalized_in_place or index_publication_failure_is_terminalized_in_place or unconfirmed_publication_preserves_running_candidate_for_retry or existing_operation_envelope_mismatch_fails_without_mutation or same_idempotency_key_has_one_started_winner_and_terminal_losers" --basetemp "$env:TEMP\pc-phase15a-red"
```

预期：测试因当前实现仍删除/隔离失败操作、拒绝已有操作目录或未恢复自身索引而失败；测试本身能够正常收集并运行。

## 任务 2：实现启动信封复用、原位终态化与幂等恢复

**文件：**

- 修改：`src/pc_system/model_matching_audit.py`

### 步骤 1：实现初始化信封构建与验证

增加小型私有辅助函数，职责保持单一：

- 构造新操作的初始化信封；
- 验证已有 `operation.json` 是普通、合法、状态为 `running`、事件为空的初始化信封；
- 比较请求绑定字段：操作编号、操作类型、主体、角色、主体来源、请求编号、幂等键哈希和请求指纹；
- 对已有信封保留原 `initializer_owner_token` 与 `started_at`，不因重试生成新证据；
- 已有终态 `operation.start_failed` 返回稳定失败；任何不匹配、损坏或冲突返回 `audit_integrity_error`，且不改写文件。

### 步骤 2：重写 `_start_operation` 的状态机

在操作级初始化锁内执行：

1. 创建不存在的规范操作目录；目录已存在时原位验证。
2. `operation.json` 不存在且目录为空时写入初始化信封；已有匹配信封时复用。
3. 读取幂等索引：
   - 指向自身且绑定一致：追加一次 `operation.started`；
   - 指向其他赢家：对当前操作追加一次 `operation.start_failed`（`idempotency_race_lost`），然后重放或拒绝赢家；
   - 不存在：尝试原子发布。
4. 发布结果：
   - `published_confirmed`：追加一次 `operation.started`；
   - `published_unconfirmed`：保持运行候选不变，返回 `audit_persistence_error`；
   - `not_published + FileExistsError`：重新读取赢家索引，原位终态化失败操作，再重放/拒绝；
   - 其他确定失败：原位写入 `operation.start_failed`（`audit_persistence_error`），返回持久化错误。
5. 启动事件追加失败继续沿用既有审计终态恢复，但不得移动或删除目录。

删除不再使用的隔离实现与依赖：`_DiscardedTree`、`_discard_unstarted_operation`、`_discard_empty_operation_root`、私有 `.d` 隔离目录、跨平台目录重命名和相关 `ctypes`/`secrets`/`shutil`/`stat`/`sys` 导入。

### 步骤 3：运行新增测试并确认 GREEN

运行与任务 1 步骤 2 相同的定向命令。

预期：全部通过。

### 步骤 4：运行审计模块回归

运行：

```powershell
uv run pytest -q tests/test_phase15a_audit.py --basetemp "$env:TEMP\pc-phase15a-audit"
```

根据失败结果只修正与新状态机直接相关的旧契约；不得恢复隔离或删除行为。

## 任务 3：集成验证、复审与精确暂存

**文件：**

- 验证：`src/pc_system/model_matching_audit.py`
- 验证：`tests/test_phase15a_audit.py`

### 步骤 1：运行 Phase 15A 聚焦集成测试

运行：

```powershell
uv run pytest -q tests/test_phase15a_audit.py tests/test_phase15a_model_library.py tests/test_phase15a_import.py tests/test_phase15a_api.py tests/test_phase15a_cli.py --basetemp "$env:TEMP\pc-phase15a-focused"
```

预期：全部通过；CLI 路径修复保持独立工作区改动，不纳入本次代码范围。

### 步骤 2：执行一次完整就绪验证

运行：

```powershell
uv run pytest -q --basetemp "$env:TEMP\pc-phase15a-full"
```

预期：全套测试通过。只有用户随后要求推送时，才在推送前再运行一次完整验证。

### 步骤 3：独立复审与有限修正

使用 `superpowers:requesting-code-review` 请求一名独立复审者，仅审查本计划范围及批准规格。最多进行一轮针对性修正和一轮复验；超出范围的问题记录为后续任务。

### 步骤 4：检查范围并精确暂存

运行：

```powershell
git diff -- src/pc_system/model_matching_audit.py tests/test_phase15a_audit.py
git diff --check -- src/pc_system/model_matching_audit.py tests/test_phase15a_audit.py
git add -- src/pc_system/model_matching_audit.py tests/test_phase15a_audit.py
git diff --cached --check
```

不得暂存 `src/pc_system/commands/phase15.py`、`tests/test_phase15a_cli.py`、项目功能清单、项目记忆或未跟踪的 pytest 临时目录。
