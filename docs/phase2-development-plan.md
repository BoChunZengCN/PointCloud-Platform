# Phase 2 Development Plan

## Goal

Extend the Phase 1 processed LAS/LAZ route with raw FLS ingestion boundaries, Gaussian Splatting publishing boundaries, and a unified Phase 2 viewer entry.

## Module Order

1. P2-M1 FLS ingest adapter
2. P2-M2 Gaussian Splatting adapter
3. P2-M3 Unified Phase 2 viewer
4. P2-M4 Phase 2 status report

## Implemented Boundaries

### P2-M1 FLS Ingest Adapter

- `pc_system.fls_ingest.FlsIngestRequest`
- `build_fls_ingest_plan`
- `write_fls_ingest_plan`
- `execute_fls_ingest_plan`
- CLI commands: `plan-fls-ingest`, `execute-fls-ingest`

The plan command writes:

```text
data/raw/fls/<asset_id>/fls_ingest_plan.json
```

Current boundary:

- Real FLS decoding is delegated to an external converter executable
- The adapter validates that the expected LAS output exists
- Registration mode is recorded in the manifest, defaulting to `external`

### P2-M2 Gaussian Splatting Adapter

- `pc_system.gaussian_splatting.GaussianSplatRequest`
- `build_gaussian_splat_plan`
- `write_gaussian_splat_plan`
- `execute_gaussian_splat_plan`
- CLI commands: `plan-gaussian-splat`, `execute-gaussian-splat`

The plan command writes:

```text
previews/<asset_id>/splats/<name>/gaussian_splat_plan.json
```

The execute command writes `gaussian_splat_config.json`, calls an external training script, and validates the generated model file.

Current boundary:

- Production 3DGS training is delegated to an external script
- Default model output is `point_cloud.ply`
- Training iterations are recorded in the plan

### P2-M3 Unified Phase 2 Viewer

- `pc_system.phase2_viewer.build_phase2_viewer_manifest`
- `publish_phase2_viewer`
- CLI command: `publish-phase2-viewer`

The command writes:

```text
previews/<asset_id>/phase2_viewer_manifest.json
previews/<asset_id>/phase2_viewer.html
```

Current boundary:

- The viewer is a lightweight manifest landing page
- It links Potree metadata, Gaussian Splatting output, and reports
- A richer browser UI can replace the HTML while keeping the manifest

### P2-M4 Phase 2 Status Report

- `pc_system.phase2_status.build_phase2_status_report`
- `write_phase2_status_report`
- CLI command: `phase2-status`

The command writes:

```text
reports/phase2_status.json
reports/phase2_status.md
```

## Phase 2 Completion State

All Phase 2 modules in the raw FLS plus Gaussian Splatting expansion route are implemented and tested. Remaining work should be planned as later production-hardening phases: real FLS converter selection, production 3DGS training integration, richer browser UI, and large-data performance validation.
