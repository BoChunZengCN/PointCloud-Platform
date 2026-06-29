# Phase 4 Development Plan

## Goal

Close the production execution loop after Phase 3 by turning production run plans into trackable job state files that can later drive API and frontend progress views.

## Module Order

1. P4-M1 Job lifecycle CLI
2. P4-M2 Read-only job status API and dashboard
3. P4-M3 Controlled Job Write API
4. P4-EX1 Frontend job operation panel
5. P4-EX2 Job audit event log
6. P4-EX3 Job detail API
7. P4-EX4 Retry, block, and fail semantics
8. P4-EX5 Lightweight execution adapter
9. P4-EX6 Async queue interface reservation

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


### P4-M3 Controlled Job Write API

P4-M3 adds controlled write endpoints on top of the same local job JSON files used by P4-M1 and P4-M2. The goal is to let future frontend controls or automation create and update job state without shelling out to the CLI.

API behavior:

```text
POST /runs/<asset_id>/jobs
PATCH /runs/<asset_id>/jobs/<job_id>/steps/<step_id>
```

`POST /runs/<asset_id>/jobs` reads `reports/production_runs/<asset_id>/production_run_plan.json`, creates a job, writes it to `reports/jobs/<asset_id>/<job_id>.json`, and returns the job payload.

`PATCH /runs/<asset_id>/jobs/<job_id>/steps/<step_id>` accepts `status` and optional `message`, updates the requested step, recalculates the job summary, writes the JSON file, and returns the updated job payload.

Current boundary:

- This is a controlled write API, not a real executor.
- It supports state updates only for `planned`, `running`, `completed`, `failed`, and `blocked`.
- Missing plans, missing jobs, or unknown steps return clear API errors.
- Background execution, retry policies, cancellation, and audit logs remain later Phase 4 modules.


## Phase 4 Extensions

### P4-EX1 Frontend Job Operation Panel

The dashboard now includes a compact job operation panel. When the data source is the FastAPI service, operators can create a production job and update the latest job step status from the dashboard. Non-API data sources keep the controls disabled to avoid implying writes against static sample data.

### P4-EX2 Job Audit Event Log

Job creation and step status updates append JSONL audit events next to the job file:

```text
reports/jobs/<asset_id>/<job_id>.events.jsonl
```

Events include `created_at`, `job_id`, `action`, `step_id`, `old_status`, `new_status`, `message`, and `actor`.

### P4-EX3 Job Detail API

A single job detail endpoint exposes the job payload and audit events:

```text
GET /runs/<asset_id>/jobs/<job_id>
```

The response shape is `{ asset_id, job, events }`.

### P4-EX4 Retry, Block, and Fail Semantics

Job steps now track `attempt_count`, `last_error`, and `updated_at`. Moving a failed or blocked step back to `running` increments `attempt_count`; failed steps preserve the error message in `last_error`.

### P4-EX5 Lightweight Execution Adapter

`execute_job_steps()` provides a local execution adapter boundary. It advances pending steps through `running` to `completed`, writes audit events, and persists the job. It deliberately does not execute arbitrary shell commands.

### P4-EX6 Async Queue Interface Reservation

`enqueue_job()` and `list_job_queue()` provide a JSONL-backed queue contract for future Celery/RQ/Arq replacement:

```text
job_queue.jsonl
```

The current implementation records queued jobs only; distributed workers remain future production work.

## Phase 4 Current State

P4-M1 through P4-M3 and P4-EX1 through P4-EX6 are implemented and covered by tests. Remaining production work is now mostly hardening: authentication, real worker execution, distributed queues, and deployment controls.



