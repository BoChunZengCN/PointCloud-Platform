# Phase 12 Production Reliability / 生产可靠性闭环

Phase 12 closes the highest-risk gaps found during the production-readiness review.

Phase 12 修复生产就绪审查中识别出的高风险缺口，使现有 Phase 1–11 工作流具备更可靠的安全、执行和审计边界。

## Modules / 功能模块

| Module | Status | Description |
| --- | --- | --- |
| P12-M1 Identifier safety | Done | Validates asset/job/step identifiers before using them in workspace paths. |
| P12-M2 Production API protection | Done | Requires an API key whenever the API runs in production mode. |
| P12-M3 Executable plan contract | Done | Ensures generated production and batch commands satisfy the CLI parser contract. |
| P12-M4 Delivery audit | Done | Persists the evaluated delivery gate decision in CLI-generated manifests. |
| P12-M5 Artifact truth | Done | Records real artifact existence and uses it in the dashboard. |
| P12-M6 Analysis reliability | Done | Adds representative sampling, spatial-hash clustering, and parameter validation. |
| P12-M7 Reproducible verification | Done | Adds test dependencies, Phase 12 regression coverage, and GitHub Actions. |

## Production API / 生产 API

Production mode now requires a key:

```powershell
$env:PYTHONPATH="src"
python -m pc_system.cli serve-api --project-root workspace --mode production --api-key <secret>
```

Starting production mode without `--api-key` fails closed.

Production plans accept explicit tool paths through `--potree-converter`, `--pdal-path`,
`--python-path`, and `--open3d-script`; every generated command includes `--project-root`.

## Verification / 验证

```powershell
python -m pip install -e ".[test]"
python -m pytest tests -q -p no:cacheprovider
```
