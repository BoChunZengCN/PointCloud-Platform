# Phase 3 Development Plan

## Goal

Move the Phase 1 and Phase 2 adapter-based system toward production readiness by validating external tools, preparing end-to-end production runs, and recording deployment readiness.

## Module Order

1. P3-M1 Production tool check
2. P3-M2 Production pipeline plan
3. P3-M3 Production run report
4. P3-M4 Deployment package checklist

## Implemented Boundaries

### P3-M1 Production Tool Check

P3-M1 validates whether configured external tools exist before a production run starts:

- `pc_system.phase3_tool_check.ToolSpec`
- `build_tool_check_report`
- `write_tool_check_report`
- CLI command: `phase3-tool-check`

The command writes:

```text
reports/phase3_tool_check.json
reports/phase3_tool_check.md
```

Supported tool path checks:

- `--fls-converter`
- `--pdal-path`
- `--potree-converter`
- `--gaussian-trainer`
- `--open3d-script`

Current boundary:

- The check validates configured file paths only
- Missing configured required tools mark the report as blocked
- Unconfigured optional tools are reported as `not_configured`
- Version probing and sample execution checks are deferred to later Phase 3 modules

## Next Phase 3 Step

Implement P3-M2 Production Pipeline Plan:

```text
pc-system plan-production-run --project-root <workspace> --asset-id <asset_id>
```

The plan should sequence the already-built Phase 1 and Phase 2 commands into a single auditable production workflow without running heavy external tools automatically.
