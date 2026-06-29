# Phase 5 Production Hardening

## Goal

Phase 5 turns the local workflow platform into a safer production-facing service. It keeps the current file-backed architecture, while adding write protection, explicit run modes, a service startup command, frontend connection visibility, consistency checks, and deployment documentation.

## Modules

1. P5-M1 API write protection
2. P5-M2 Configured run modes
3. P5-M3 API service CLI
4. P5-M4 Frontend API health status
5. P5-M5 Workspace consistency report
6. P5-M6 Minimal production deployment docs

## P5-M1 API Write Protection

Write routes are protected when `PC_SYSTEM_API_KEY` or `api_key` is configured. Clients must send:

```text
x-api-key: <key>
```

Protected routes:

```text
POST /runs/<asset_id>/jobs
PATCH /runs/<asset_id>/jobs/<job_id>/steps/<step_id>
```

Read routes stay open for dashboard and viewer use.

## P5-M2 Configured Run Modes

The API supports `development` and `production` modes through `PC_SYSTEM_RUN_MODE`. Health output includes `run_mode`, `write_protection`, and `cors_origins`. Production mode disables wildcard CORS in the app configuration.

## P5-M3 API Service CLI

Use `serve-api` to start the FastAPI service with a workspace-bound environment:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli serve-api `
  --project-root .\workspace `
  --host 127.0.0.1 `
  --port 8000 `
  --mode production `
  --api-key <secret>
```

`--dry-run` validates configuration without starting the server.

## P5-M4 Frontend API Health Status

The dashboard reads `/health` and displays API online/offline state, run mode, and write protection. If `window.PC_SYSTEM_API_KEY` is set, write operations send it as `x-api-key`.

## P5-M5 Workspace Consistency Report

Use `check-consistency` to verify that asset registry, production plan, job files, and job event logs exist for an asset:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli check-consistency `
  --project-root .\workspace `
  --asset-id sample
```

Outputs:

```text
reports/consistency/<asset_id>/consistency_report.json
reports/consistency/<asset_id>/consistency_report.md
```

## P5-M6 Deployment Notes

For minimal local production use:

- Set `PC_SYSTEM_PROJECT_ROOT` to the workspace directory.
- Set `PC_SYSTEM_RUN_MODE=production`.
- Set `PC_SYSTEM_API_KEY` and keep it out of source control.
- Bind the API to localhost unless a reverse proxy handles TLS and access control.
- Back up `reports/jobs`, `reports/consistency`, and delivery outputs.
