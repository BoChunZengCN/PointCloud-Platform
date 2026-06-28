# Phase 4 Development Plan

## Goal

Close the production execution loop after Phase 3 by turning production run plans into trackable job state files that can later drive API and frontend progress views.

## Module Order

1. P4-M1 Job lifecycle CLI
2. P4-M2 Read-only job status API and dashboard

## Implemented Boundaries

### P4-M1 Job Lifecycle CLI

P4-M1 creates and updates local job state files from an existing Phase 3 production run plan:

```text
pc-system create-production-job --project-root <workspace> --asset-id <asset_id> [--job-id <job_id>]
pc-system update-job-step --project-root <workspace> --asset-id <asset_id> --job-id <job_id> --step-id <step_id> --status <status> [--message <message>]
```

The commands write:

```text
reports/jobs/<asset_id>/<job_id>.json
```

Current boundary:

- `create-production-job` reads `reports/production_runs/<asset_id>/production_run_plan.json`.
- `update-job-step` supports `planned`, `running`, `completed`, `failed`, and `blocked`.
- Each update recalculates the job summary and overall job status.
- Missing plans or missing job files return exit code `2`.
- Real command execution remains outside this module; P4-M1 records execution state only.


### P4-M2 Read-only Job Status API and Dashboard

P4-M2 exposes the local job files created by P4-M1 through the existing FastAPI layer and displays the latest production job state in the project dashboard.

API behavior:

```text
GET /runs/<asset_id>/jobs
```

The endpoint now returns:

- `asset_id`: requested asset id.
- `jobs`: sorted local job JSON payloads.
- `job_count`: number of job files found.
- `latest_job`: the last sorted job payload, or `null` when no job exists.
- `status_summary`: job-level status counts for dashboard badges.

Frontend behavior:

- `frontend/index.html` includes a Phase 4 production job panel.
- `frontend/app.js` calls `/runs/<asset_id>/jobs` when the dashboard is connected to the API.
- Missing API or missing jobs do not break the dashboard; the panel shows a clear empty state.

Current boundary:

- This module is read-only.
- It does not start, pause, retry, or cancel jobs.
- Job execution and write API controls remain later Phase 4 modules.

## Phase 4 Current State

P4-M1 and P4-M2 are implemented and covered by tests. Next Phase 4 work can add controlled write APIs or a real execution adapter while keeping the local job JSON contract stable.

