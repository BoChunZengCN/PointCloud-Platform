# Phase 1 Development Plan

## Goal

Build the first working system slice for already-processed color LAS/LAZ files.

## Module Order

1. M1 Project skeleton
2. M2 LAS asset metadata
3. M3 QA report
4. M4 Preview manifest / Potree publisher
5. M5 Slice planning / execution
6. M6 Rule segmentation / summary report
7. M7 Module status report

## Implemented Boundaries

### M1 Project Skeleton

- `pc_system.config.ProjectConfig`
- Standard folders: `data/raw`, `data/assets`, `reports`, `previews`, `logs`
- CLI command: `init`

### M2 LAS Asset Metadata

- `pc_system.asset.LasAssetInfo`
- `build_asset_metadata`
- `write_asset_metadata`
- `pc_system.las_reader.read_las_info`
- `laspy` is optional and isolated behind `LasReaderDependencyError`
- CLI command: `ingest`

### M3 QA Report

- `build_quality_report`
- `write_quality_report`
- JSON and HTML output
- Checks: point count, RGB, classification, CRS

### M4 Preview Manifest and Potree Publisher

- `publish_preview`
- JSON preview manifest
- Minimal HTML preview landing page
- `pc_system.potree_publisher.PotreePublishRequest`
- `publish_potree`
- CLI command: `publish-potree`

Current boundary:

- PotreeConverter is not bundled
- The caller must pass the local converter executable path
- Tests inject a fake runner so the CLI workflow is covered without the external binary

### M5 Slice Planning / Execution

- `pc_system.slice_plan.SliceRequest`
- `build_slice_plan`
- `write_slice_plan`
- `pc_system.slice_executor.SliceExecutionResult`
- `execute_slice_plan`
- `pc_system.pdal_slice_adapter.pdal_slice_adapter`
- CLI commands: `plan-slice`, `execute-slice`

The command creates and executes:

```text
data/assets/<asset_id>/slices/<slice_name>/slice_plan.json
```

Current boundary:

- Placeholder executor is available for dependency-free workflow tests
- PDAL adapter writes `pdal_pipeline.json` and calls `pdal pipeline <pipeline.json>`
- PDAL is not bundled

### M6 Rule Segmentation / Summary Report

M6 has rule segmentation planning, placeholder execution, Open3D adapter boundary, a bundled reference script, and summary reporting:

- `pc_system.rule_segmentation.RuleSegmentationRequest`
- `build_rule_segmentation_plan`
- `write_rule_segmentation_plan`
- `execute_rule_segmentation_plan`
- `pc_system.open3d_rule_segmentation_adapter.open3d_rule_segmentation_adapter`
- `scripts/open3d_rule_segment.py`
- `pc_system.segmentation_summary.build_segmentation_summary`
- CLI commands: `plan-rule-segment`, `execute-rule-segment`, `report-rule-segment`

The plan command writes:

```text
data/assets/<asset_id>/slices/<slice_name>/segments/<name>/rule_segmentation_plan.json
```

The Open3D engine writes `open3d_rule_segmentation_config.json`, calls `scripts/open3d_rule_segment.py` or another compatible script, validates labels output, and records `open3d-rule-segmentation` execution metadata.

Current boundary:

- Default rule labels: `ground`, `plane`, `cluster`, `noise`
- Reference script supports JSON points and simple ASCII PLY input
- Reference script implements simple `noise`, `ground`, and `cluster` labels
- Rich production segmentation can replace the script while keeping the adapter contract

### M7 Module Status Report

M7 writes the Phase 1 completion report:

- `pc_system.module_status.build_module_status_report`
- `write_module_status_report`
- CLI command: `module-status`

The command writes:

```text
reports/module_status.json
reports/module_status.md
```

## Phase 1 Completion State

All Phase 1 modules in the processed LAS/LAZ route are implemented and tested. Remaining work should be planned as Phase 2, such as richer Open3D segmentation algorithms, Gaussian splatting conversion, FLS raw-data ingestion, or a browser UI.
