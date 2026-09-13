# Phase 15E：实物参考点云模板实施计划

> **执行要求：** 使用 `superpowers:executing-plans` 在本会话按任务执行，遵守项目“一名实施者、独立设计复审与最终复审”的效率协议。任务状态与验证证据留在本文，避免压缩上下文后重复工作。

**目标：** 不依赖 CAD 完成扫描模板导入、核验、发布、检索、刚性配准和人工绑定。

**架构：** 扫描专用解析/标准化/核验，共用模型资产、发布和下游匹配链。保留全部旧 CAD 工件；新类型以显式契约分派，不通过目录猜测来源。不新增跨模块事务。

**技术栈：** Python 3.11+、NumPy、现有文件工件/内核锁/审计服务、LAS/LAZ 可选读取依赖、FastAPI、原生 HTML/JS、pytest 与 Playwright。

**规格：** [中文设计规格](../specs/2026-09-03-phase15e-scanned-reference-templates-design.md)。

## 全局约束

- 单个原始文件 128 MiB、解码点数 1,000,000、单次导入总工件 512 MiB、累计保留导入工件 2 GiB、最多两个并行解码工作进程；120 秒解析与标准化时限。
- `mm`、`cm`、`m` 转米；包围盒中心为局部原点；轴不旋转；派生坐标 12 位小数；刚性矩阵不允许缩放。
- `scan-quality-v1`：唯一点至少 64；协方差特征值降序；`lambda2/lambda1 <= 1e-8` 拒绝；`lambda3/lambda1 <= 1e-8` 或重复比例至少 20% 提醒核验。
- 文件原样留存；去重与 SHA-256 确定性子集仅作用于派生点集；不插值、不复制凑点。
- 专家核验只允许一份不可变最终记录；自动硬失败不可强行通过；发布模板与启用索引分别执行。
- 保留旧 CAD 及旧绑定契约；本期不做 CAD 关联/双表达切换、训练、清理历史或发布到 GitHub。
- 所有生产变更先观察失败测试再实现。每任务运行聚焦测试，阶段结束运行一次全仓；同类重要缺陷最多两轮。
- 新增文档与注释以中文为主。只暂存精确文件；独立工作树使用根仓库现有 `.venv`，测试通过 `tests/conftest.py` 指向当前工作树源码。

## 执行与接口记录

基线 `5385ade`。分支 `codex/phase15e-scanned-reference`，目标工作树 `.worktrees/phase15e-scanned-reference`。

设计复审重点为源冻结/配额、提交恢复、旧发布读取、新核验与索引证据。最终复审再检查实际差异；不安排每个小编辑的额外审查。

依赖顺序：任务 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9。任务 1 纯几何算法不依赖持久化设计结论；任务 3 开始前必须解决设计复审的重要问题。

## 任务 1：确定性坐标标准化、选点与质量报告

**文件：** 新增 `src/pc_system/reference_geometry.py`、`tests/test_phase15e_reference_geometry.py`。

**接口：**

```python
normalize_reference_points(points, *, declared_unit: str, maximum_points: int = 1_000_000) -> dict
# 返回 points、input_point_count、point_count、duplicate_fraction、coordinate_unit、
# source_origin_m、source_m_to_reference_4x4、source_bounds_m、dimensions_m、geometry_fingerprint。
select_reference_points(points, *, point_count: int, random_seed: int) -> list[list[float]]
assess_reference_quality(normalized: dict) -> dict
# 返回 status、rejection_codes、warning_codes、metrics、quality_policy_version。
```

- [x] 红灯：无模块时确认新增测试失败；写 mm/cm/m 手算坐标、点序不变性、重复点、非有限值/布尔值/溢出/非法上限、低点数、平面、共线和真实子集测试。
- [x] 绿灯：纯函数实现验证、稳定中心和规范指纹；点距统计使用确定性至多 4,096 点，分块计算最近邻避免创建完整三维差张量。
- [x] 验证：`python -m pytest tests/test_phase15e_reference_geometry.py tests/test_phase15b2_features.py -q`，52 项通过（新增 40 项）；compileall 与差异检查通过。
- [x] 提交：`feat: add deterministic scanned reference geometry`，精确暂存本任务两文件及计划证据。

验收示例（预期由手算给出）：

```python
value = normalize_reference_points([[0, 0, 0], [1000, 2000, 3000]], declared_unit="mm")
assert value["points"] == [[-0.5, -1.0, -1.5], [0.5, 1.0, 1.5]]
assert value["source_origin_m"] == [0.5, 1.0, 1.5]
assert value["source_m_to_reference_4x4"][0] == [1.0, 0.0, 0.0, -0.5]
```

## 任务 2：真实格式读取与有界解析进程

**文件：** 新增 `src/pc_system/reference_reader.py`、`src/pc_system/reference_worker.py`、`src/pc_system/reference_laz.py`、`tests/test_phase15e_reference_reader.py`；调整 `pyproject.toml` 的扫描可选依赖。提前同步 `.github/workflows/test.yml` 安装该依赖，避免新增真实格式测试在 CI 缺包。

**接口：** `read_reference_file(path: Path, *, declared_unit: str, maximum_points: int) -> dict` 返回标准化结果、质量报告、读取器/格式/单位元数据；`decode_reference_file(path: Path, *, declared_unit: str, timeout_seconds: float = 120, maximum_points: int = 1_000_000) -> dict` 在工作进程执行，输出有界且不使用 pickle 解析外部内容。

- [x] 红灯：真实 LAS/LAZ、ASCII/大小端 PLY；缺字段、非空 faces、列表顶点、假声明点数、截断、超限、依赖缺失、单位冲突、地理 CRS、超时。新增 LAZ 独立点数证据用例先观察到 8 项预期失败。
- [x] 绿灯：LAS 流式计数和真实解码；LAZ 收窄到已验证子集；PLY 严格属性/字节/点数校验；检查后再分配；父进程超时终止自身子进程。解码槽位由任务 3 的项目级锁管理，不在纯读取函数内引入全局数据库。
- [x] 验证：`python -m pytest tests/test_phase15e_reference_reader.py tests/test_phase15e_reference_geometry.py tests/test_las_reader.py -q`，81 项通过（3.17 秒）。独立复审的块表/EVLR 重叠问题已按有界字节流修复并闭环。
- [x] 提交：`67e54fe`，`feat: read bounded scanned reference files`。

```python
decoded = read_reference_file(ply_path, declared_unit="m", maximum_points=64)
assert decoded["normalized"]["point_count"] == 64
assert decoded["reader"]["format"] == "ply"
with pytest.raises(ModelMatchingError, match="limit"):
    read_reference_file(ply_path, declared_unit="m", maximum_points=63)
```

## 任务 3：扫描资产、源冻结与不可变版本导入

**文件：** 新增 `src/pc_system/reference_import.py`、`src/pc_system/reference_store.py`、`tests/test_phase15e_reference_import.py`、`tests/phase15e_support.py`；调整 `model_library.py`、`model_import.py` 的类型分派与清单读取；同步两份功能盘点中的当前进度。

**接口：** `create_model_asset(..., source_family="cad_mesh")` 保持旧省略参数行为；`import_reference_version(root, *, model_id, version_id, source_path, declared_unit, license_name, provenance, principal, operation_id, request_id, idempotency_key, supersedes_version_id=None) -> dict`；`load_reference_version(root, model_id, version_id) -> dict`；`list_reference_versions(root, model_id) -> list[dict]`。

**实施冻结与恢复边界：** 同一版本锁覆盖所有者、源冻结、派生工件和审计完成；只复用原操作身份。项目配额锁下对每个未完成版本预留最多 512 MiB，已提交/明确拒绝的版本按实际留存计费，暂存源也计入 2 GiB。源复制采用独占创建的尝试文件，复制完成并同步后再发布 `source_frozen.json`；中断残留不删除、不复用为冻结源。确定性解析拒绝保存不可变拒绝记录并审计失败；I/O 或发布不确定保留 running 供原操作恢复。读取同时核对所有者、各工件 SHA-256、最后提交和 completed 审计；不创建第二份版本目录或跨模块事务。

- [x] 红灯：扫描资产无 CAD 导入；旧 CAD 仍为 1.0；双向类型拒绝；原文件被改/丢失后的同操作恢复；配额争用、两解码槽位；损坏提交拒绝读取。最初 19 项因功能缺失失败，后续加入身份重放和复审回归。
- [x] 绿灯：冻结输入并在内核锁内预留保守字节配额；同操作目录保存源副本，完成复制后持久化 `source_frozen.json`；完整源指纹已固定后不重新接受改变的源；不完整源副本不能被当作已冻结内容。配额统计扫描暂存、进行中与失败目录，不能复用只统计 CAD 临时目录的旧逻辑。
- [x] 绿灯：扫描清单 2.0、所有者/最后提交清单/审计一致读取；标准化与质量工件不可覆盖；无自动删除；旧 `load_model_version` 明确分派新类型且旧版本仍原校验。扫描公开读取必须验证完整提交与 completed 审计，不能继承 CAD 对 running 工件的内部兼容读法。
- [x] 验证：`python -m pytest tests/test_phase15e_reference_import.py tests/test_phase15a_model_library.py tests/test_phase15a_model_import.py -q`，137 项通过（新增 28 项，16.79 秒）；compileall 和差异检查通过。
- [x] 提交：`feat: import immutable scanned reference versions`（包含本检查点记录，以 Git 历史中的该功能提交为准）。

```python
version = import_reference_version(root, **import_request)
assert version["source_kind"] == "scanned_reference"
assert version["coordinate_unit"] == "m"
assert import_reference_version(root, **import_request) == version
```

## 任务 4：专家核验、模板发布与回滚

**文件：** 新增 `src/pc_system/reference_review.py`、`tests/test_phase15e_reference_review.py`、`tests/test_phase15e_reference_release.py`；修改 `model_release.py`、共享测试夹具 `tests/phase15e_support.py` 及本计划、两份功能盘点。经接缝检查，无需修改 `reference_store.py` 或 `model_release_state.py`。

**接口：** `review_reference_version(root, *, model_id, version_id, decision, reason, acknowledgements, principal, operation_id, request_id, idempotency_key) -> dict`；`load_reference_review(root, model_id, version_id) -> dict | None`；发布仍调用 `release_model_version`。

**任务 4 实施冻结：** 核验包位于 `models/{model_id}/reviews/{version_id}`，不向已完成导入目录追加文件；核验 ID 采用原操作 ID。`approved` 必须确认 `single_object`、`metadata_and_rights`、`coverage_limitations` 三项基础声明及自动报告全部风险码；`rejected` 必须填写原因，但不要求作出通过声明。原因 1–1,000 字符；确认码规范去重排序，未知码拒绝。每版本一个不可变最终核验包，固定原版本清单和质量指纹，使用核验版本锁、owner→review→commit→completed 审计；归属不确定保留 running，仅原身份恢复。新发布记录 1.1 增加 `review_id` 和 `review_fingerprint`，生成、恢复及读取均验证已完成的 approved 核验。旧 CAD 发布 1.0 和发布头状态机不重写；不自动发布索引、不改变绑定、不增加页面。验收包括权限/风险门禁、并发首写、审计中断恢复、证据篡改、扫描回滚及旧 CAD 回归。

- [x] 红灯：最初 21 项因缺少核验及发布门禁失败；覆盖非专家拒绝、硬失败不能通过、缺风险确认拒绝、一次最终决定、并发核验、响应丢失恢复及未通过不能 activate/rollback。另补上游证据暂损和空目录中断的失败回归。
- [x] 绿灯：固定版本/质量指纹的不可变核验；扫描发布 1.1 冻结核验指纹；旧 CAD 1.0 保持；复用现有发布所有者与头比较，不向旧 manifest 注入字段。
- [x] 验证：`python -m pytest tests/test_phase15e_reference_review.py tests/test_phase15e_reference_release.py tests/test_phase15b1_model_release.py -q -p no:cacheprovider --tb=short`，50 项通过（新增 27 项，24.05 秒）；独立复审定向恢复回归 4 项通过。
- [x] 提交就绪：`feat: review and release scanned reference versions`；功能提交包含本文验证证据，提交 SHA 以 Git 历史为准。

```python
with pytest.raises(ModelMatchingError) as missing_review:
    release_model_version(root, **release_request)
assert missing_review.value.code == "reference_review_required"
review_reference_version(root, **approved_review_request)
assert release_model_version(root, **release_request)["version_id"] == "v1"
```

失败请求如已形成失败终态，后续发布测试必须使用新的操作身份；示例中的 `release_request` 在每个独立调用场景中构造。

## 任务 5：统一表达、特征配置与检索索引

**文件：** 新增 `src/pc_system/model_representation.py`、`src/pc_system/reference_representation.py`、`tests/test_phase15e_reference_index.py`；修改 `model_sampling.py`、`model_retrieval_config.py`、`model_feature_store.py`、`model_feature_index.py`、`model_index_release.py`。

**接口：** `load_model_representation(root, model_id, version_id, representation_id) -> dict`；`load_representation_points(root, representation: dict) -> list[list[float]]`；`build_reference_representation(root, *, model_id, version_id, config, principal, operation_id, request_id, idempotency_key) -> dict`。分派依据受验证资产/版本来源，不能靠试读目录。

**恢复状态机定稿：** 先校验路径身份和生成配置、派生确定性表达 ID，随后立即创建审计子操作；只有子操作存在后才读取版本包和核验证据。失败重放返回原错误，完成重放只从同一已验证工件快照加载，运行重放在表达锁内按 owner 状态继续发布。不同 owner 仅当其生产操作已完成且全部工件/审计一致时复用；运行中或归属不确定一律 `operation_busy`。点集通过文件句柄按上限加 1 字节读取，校验、SHA-256 与消费共用该缓冲；上限覆盖 500,000 个有限双精度坐标的规范 JSON。三个统一读取入口均先验证模型、版本和表达标识。索引覆盖率只有在子操作可验证存在时才携带 `child_operation_id`；四类致命错误保持父子操作可恢复，不转为普通排除。

- [x] 红灯：已核验扫描进入生产/历史 Challenger；未核验拒绝；同源配置确定性；旧配置遇扫描明确不支持；CAD+扫描不同资产共索引；缺类型/篡改类型拒绝。独立复审后又补充工件发布前后中断、事件/完成响应丢失、并发所有权、致命审计错误、确定性生成失败、损坏表达、受限字节快照和路径身份回归；集中状态机改写前 7 项定向失败，统一入口路径回归另有 1 项失败。
- [x] 绿灯：配置 1.1 带 `scanned_sampling`；模型特征/索引 1.1 固定类型与所有指纹；保留旧 1.0 计算及读取分支；索引生成仍按既有覆盖率报告。扫描表达按真实子操作记录覆盖率排除；运行中外部所有者不可接管，已完成所有者复用前必须通过完整表达校验；致命子操作错误保持父操作可恢复。
- [x] 验证：`tests/test_phase15e_reference_index.py` 29 项通过（41.38 秒）；PowerShell 枚举全部 `test_phase15b2_*.py` 后 109 项通过（112.02 秒）。1 条既有 Starlette/httpx 弃用警告保留为依赖维护事项。
- [x] 提交：功能提交 `84a3eab`（`feat: index scanned reference representations`）；恢复与复审修复提交 `fix: recover scanned reference index publication`，最终 SHA 以 Git 历史为准。

```python
entries = read_index_entries(root, "index-scans")
assert {item["representation_type"] for item in entries} == {"cad_sampled", "scanned_reference"}
assert {item["model_id"] for item in entries} == {"cad-pump", "scan-pump"}
```

## 任务 6：扫描候选、刚性配准与历史绑定

**文件：** 修改 `model_retrieval.py`、`model_registration_input.py`、`model_registration.py`、`model_match_decision.py` 的新报告读取接缝；新增 `tests/test_phase15e_reference_matching.py`。

- [x] 红灯：新 1.2 候选冻结来源；扫描输入不走网格路径；1.1 配准报告矩阵保持局部到对象方向；旧候选/报告与绑定继续读取；模板更新不改变旧绑定。初始 4 项用例为 3 项失败、1 项通过；复审补充的真实旧索引链回归先失败 1 项。
- [x] 绿灯：显式传播/校验类型与坐标证据；不改变质量门槛；只改新报告必须的字段读取，不新增绑定 schema。仅检索 1.2 传播合法类型并生成配准 1.1；旧检索 1.1 不补类型并生成配准 1.0。
- [x] 验证：Task 6 核心 5 项通过；连同 Phase 15C 配准和 Phase 15D 决策/绑定接缝共 114 项通过（182.05 秒）；定向编译与 `git diff --check` 通过。全仓及浏览器门禁仍由任务 9 统一执行。
- [x] 提交：功能提交 `3fafe90`（`feat: match and bind scanned reference models`）；兼容性修复提交 `fix: preserve legacy model matching contracts`，最终 SHA 以 Git 历史为准。

```python
frozen = load_registration_input(root, **candidate_request)
assert frozen["candidate_evidence"]["representation_type"] == "scanned_reference"
assert frozen["coordinate_unit"] == "m"
assert old_binding_after_new_release == old_binding_before_new_release
```

## 任务 7：扫描管理 API、CLI 与安全状态投影

**文件：** 新增 `src/pc_system/reference_catalog.py`、`tests/test_phase15e_api.py`、`tests/test_phase15e_cli.py`；修改 `api.py`、`cli.py`、`cli_parser.py`、实际使用的请求字段定义。

**接口：** `list_reference_catalog(root, *, principal, status=None, cursor=None, limit=50) -> dict` 返回清单和游标；详情返回质量/核验/发布/索引四个独立状态。新增四个规格端点与四个 `model-reference-*` 命令，发布仍走已有接口。

- [ ] 红灯：扫描资产创建、受控暂存路径、分页游标绑定筛选、专家写、审计只读、普通人员摘要、旧公开端点不泄露扫描来源正文；所有入口相同领域结果。
- [ ] 绿灯：API/CLI 只校验和调用，不复制算法；状态投影只读；端点/命令支持同操作重试；冲突与中文说明一一对应。
- [ ] 验证：`python -m pytest tests/test_phase15e_api.py tests/test_phase15e_cli.py -q` 加已有 API/CLI 直接受影响测试。
- [ ] 提交：`feat: expose scanned reference management APIs and CLI`。

```python
response = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=auditor_headers)
assert response.status_code == 200
assert response.json()["publication_status"] == "unpublished"
assert response.json()["index_status"] == "not_indexed"
```

## 任务 8：扫描模板管理页面与来源标识

**文件：** 新增 `frontend/model-reference-library.html`、`.js`、`.css`；修改 `frontend/model-matching-lab.html`、`model-matching-lab.js`、`model-decisions.js` 的入口/类型展示；新增 `tests/browser/test_phase15e_reference_library.py`，扩展 `tests/browser/conftest.py`。

- [ ] 红灯：真实浏览器导入已暂存 PLY、检查投影/尺寸、通过核验、发布、索引未更新提示、筛选/历史、审计只读、重复提交禁用、响应不确定重试。
- [ ] 绿灯：原生页面三步主流程、三个正交投影；只取服务端有界预览；令牌内存态、来源文本安全渲染；不建复杂新前端框架。
- [ ] 验证：`python -m pytest tests/browser/test_phase15e_reference_library.py -q --browser chromium --browser-channel chrome`；记录本机实际浏览器命令，CI 使用 Chromium。
- [ ] 提交：`feat: add scanned reference library workbench`。

```python
expect(page.get_by_role("heading", name="扫描参考模板")).to_be_visible()
expect(page.get_by_text("模板已发布，索引需要更新", exact=True)).to_be_visible()
expect(page.get_by_role("button", name="确认通过")).to_be_disabled()
```

## 任务 9：全链验收、旧数据回归与阶段资料

**文件：** 新增 `tests/test_phase15e_integration.py`、`docs/phase15e-scanned-reference-templates.md`；更新 `.github/workflows/test.yml`、`README.md`、两份功能盘点、本文进度。

- [ ] 写独立扫描正例与不同型号负例的验收入口；没有真实用户扫描时，工程夹具单独标记，不伪造业务真实验收结论。
- [ ] 全链回归核验、发布、索引、检索、配准、绑定/回滚，以及历史文件指纹不变；确认没有 `cad_mesh` 占位文件。
- [ ] 全仓门禁：`python -m pytest tests --ignore=tests/browser -q -p no:cacheprovider`；真实浏览器集单独一次；执行 `compileall` 和 `git diff --check`。
- [ ] 独立最终复审：范围为 `5385ade..HEAD`，只返回严重/重要问题与验证证据；小问题列后续债务；必要修复最多一个意图明确的提交。
- [ ] 提交：`feat: complete scanned reference template workflow`。报告工程验收与真实样本验收的分别状态；未经新授权不推送、不合并、不打标签。

## 验证证据与设计复审结论

- 2026-09-03：开发授权已收到；源工作区只有本阶段三份文档变更，未混入其他代码修改。
- 独立设计复审：无严重/重要矛盾；三个实施接缝已写入任务 3/4：扫描公开读取只接受 completed 审计；冻结源完成标记与完整配额统计；发布 1.1 的读取和恢复均核验人工证据。旧 CAD 不改写。
- 文档/身份基线：`python -m pytest tests/test_phase15a_identity.py tests/test_phase15d_docs.py -q`，17 项通过；未重复运行全仓门禁。
- 任务 1 红灯：40 项因缺少几何模块失败；绿灯：新增 40 项与旧特征 12 项共 52 项通过，1.40 秒。未改变任何旧算法、发布或绑定逻辑。
- 任务 1 已提交：`8892b5e`；规格/计划基线提交 `e973d0b`。
- 任务 2 初版：真实 LAS/LAZ、三种 PLY 编码、单位/CRS、截断/计数/上限、真实子进程与超时，26 项读取测试通过；与几何和旧 LAS 测试共 68 项通过。读取依赖已安装至项目共享 `.venv`（laspy 2.7.0、lazrs 0.8.2、pyproj 3.7.2）。初版当时尚未提交。
- 任务 2 最终复审：块表不能借用 EVLR 字节；真实重叠文件先红，改用受限字节流后 81 项通过，复审定向 5 项通过。依赖显式限定 `lazrs>=0.8.2,<0.9`，CI 两条测试任务安装扫描依赖。
- 任务 3 独立复审：3 项重要问题（误把索引系统目录当资产、损坏 running owner 误终态、配额释放观察竞态）均有失败回归并在一轮修复中闭环；复审者独立运行对应 3 项通过（1.10 秒）。没有改写原审计架构。
- 当前检查点：任务 1–6 已实现并完成聚焦验证及独立复审；任务 7–9 尚未实施。下一步为扫描管理 API、CLI 与安全状态投影。未运行本阶段浏览器验收，未收到真实重复扫描/不同型号负例，不宣称生产业务验收完成。
- 本轮提交就绪门禁：`python -m pytest tests --ignore=tests/browser -q -p no:cacheprovider --tb=short`，1,255 项通过、1 项跳过，366.44 秒；仅运行一次。1 条既有 Starlette/httpx 弃用警告记录为依赖维护事项，本轮不扩大修复。静态编译和暂存差异检查通过。Task 2 提交 `67e54fe`，Task 3 单独功能提交；未推送、未合并、未发布。
- 任务 4 独立设计复审通过；最终复审发现 1 项重要问题：所有者写入前中断产生的空目录阻止重试。新增回归先红，将“保留恢复资格”与“目录确有工件”分离后通过；复审定向 4 项通过（2.20 秒），该问题已关闭。核验/发布/回滚实现未改写审计或发布头状态机，未扩大到页面与匹配链。
- 任务 4 提交就绪门禁：全仓非浏览器测试一次运行，`1,282 passed, 1 skipped, 1 warning`，378.24 秒；27 项新增测试全部通过。既有 Starlette/httpx 弃用警告保留为依赖维护事项；语法编译、差异检查通过。仅本地提交，不推送、不合并、不发布；没有浏览器或真实业务样本验收结论。
- 任务 5 初版提交 `84a3eab`；独立复审发现 2 项严重和 4 项重要问题，集中在已验证字节快照、发布恢复、致命错误传播、覆盖率子操作身份及恢复测试。恢复修复覆盖所有表达工件发布边界、审计事件/完成响应丢失、并发复用和历史 Challenger；聚焦 22 项及旧 Phase 15B2 共 109 项通过。测试临时目录与执行报告保持本地忽略状态，不纳入产品提交。
- 任务 5 第二次独立复审确认真正的完成态响应丢失仍未闭环，并指出读取上限在分配后检查、联合入口路径身份未校验、源证据失败发生在子操作创建前。按“停止补丁堆叠”协议先冻结上述恢复状态机，再集中改写：完成重放严格加载原表达；源证据读取移至审计启动后；点集通过文件句柄最多读取 64 MiB 加 1 字节；统一入口提前校验三个路径标识。对应 8 项失败测试均已观察红灯并转绿，Task 5 共 29 项通过。
- 任务 5 最终定向复审：上述完成态重放、有界读取与最大合法负载、三条路径身份入口、真实子操作覆盖率证据及显式来源分派均裁定 `ADDRESSED`；未发现新的严重或重要问题，结论 `Ready: Yes`。本任务不重复运行全仓门禁；全仓非浏览器与浏览器验收仍按任务 9 执行。
- 任务 6 初版提交 `3fafe90`；独立复审发现 1 项重要兼容性问题：旧 1.0 索引条目已有类型字段，导致旧检索 1.1 被补字段并把配准报告升级为 1.1。修复改为由契约 schema 分派：仅新检索 1.2 传播合法类型并驱动配准 1.1，旧检索 1.1 保持无类型并驱动配准 1.0；真实旧索引→检索→冻结→配准回归完成红绿验证。
- 任务 6 最终定向复审：原重要问题裁定 `ADDRESSED`，未发现新的严重或重要问题，结论 `Ready: Yes`。主流程重新运行 Task 6、Phase 15C 配准与 Phase 15D 决策/绑定聚焦集，`114 passed in 182.05s`；定向 `compileall` 与 `git diff --check` 通过。没有修改质量阈值、绑定 1.0、API、CLI 或页面。

### 任务 2 边界调整：LAZ 点数的独立完整性保证

后续定向复核发现：固定块的旧式 LAZ 并不独立保存最后一块的实际点数。真实内存文件探针中，64 点文件的块表显示固定容量 50,000，直接请求解码 63、64、65 点均未报错；不能以解码返回数量证明完整性。

新增回归 `test_fixed_chunk_laz_underreported_count_cannot_be_claimed_independently_verified` 使用实际 125 点三维 LAZ，仅将文件头声明改为 124。最初读取器未报错，测试确定性失败；收窄支持范围后通过，保留该回归。

独立复审裁定为重要规格保证缺口：规格第 4 节要求声明与实际一致、不能仅依赖文件头，第 12 节包含假点数验收。旧式固定块缺口不应泛化为所有 LAZ：新式分层块及具有独立点数信息的变体需分别验证。

用户在收到推荐边界后授权“继续执行开发”：首版支持 LAS/PLY；LAZ 只开放已证明可交叉校验点数的子集，其余明确拒绝并提示先转换为 LAS。具体子集见规格第 4.1 节。代价是部分合法 LAZ 不能直接导入，导入、版本、审计架构无需重构。

依据：[laspy 官方读取源码](https://laspy.readthedocs.io/en/latest/_modules/laspy/lasreader.html)、[LAZ 格式规范第 11.6–11.7 节](https://portal.ogc.org/files/?artifact_id=110135&version=2)，以及真实文件回归。点格式 6/7/8、分层点数/层字节/块表破坏、50,064 点跨块末块均已有回归；不以“请求多读一点”作为独立证据。本期未推送或合并。
