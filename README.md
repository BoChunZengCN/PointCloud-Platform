# Point Cloud System

Phase 1 implementation for processed LAS/LAZ point cloud assets.

## Current Scope

This folder is an isolated project for the LAS-first route:

```text
M1 Project skeleton
-> M2 LAS asset metadata
-> M3 QA report
-> M4 Preview manifest / Potree publisher
-> M5 Slice planning / execution
-> M6 Rule segmentation / summary report
-> M7 Module status report
-> Phase 2 FLS ingest / Gaussian Splatting / unified viewer
```

The current implementation works without external point-cloud dependencies. Real LAS/LAZ reading is isolated in `pc_system.las_reader` and requires `laspy` when enabled.

## Commands

Run tests:

```powershell
python -m pytest tests -q -p no:cacheprovider
```

Create a project workspace:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli init --project-root .\workspace
```

Run the Phase 1 demo workflow:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli demo-phase1 --project-root .\workspace --las-path .\sample.las
```

Run the real ingest workflow after installing `laspy`:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli ingest --project-root .\workspace --las-path .\sample.las
```

Create an M5 slice plan from an ingested asset:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-slice `
  --project-root .\workspace `
  --asset-id sample `
  --name room-a `
  --min 0 0 0 `
  --max 10 10 3 `
  --voxel-size 0.05 `
  --output-format ply
```


Publish an ingested asset with PotreeConverter:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli publish-potree `
  --project-root .\workspace `
  --asset-id sample `
  --converter-path C:\tools\PotreeConverter.exe
```
Execute an M5 slice plan:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-slice `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a
```


Execute an M5 slice plan with PDAL:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-slice `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --engine pdal `
  --pdal-path C:\tools\pdal.exe
```

Create an M6 rule segmentation plan:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline
```

Execute an M6 rule segmentation plan:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline
```
Execute an M6 rule segmentation plan with Open3D script:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline `
  --engine open3d `
  --python-path C:\Python\python.exe `
  --script-path .\scripts\open3d_rule_segment.py
```
Create an M6 segmentation summary report:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli report-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline
```
Write the Phase 1 module status report:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli module-status --project-root .\workspace
```
Demo output:

```text
workspace/
  data/
    raw/
    assets/<asset_id>/asset.json
  reports/<asset_id>/quality_report.json
  reports/<asset_id>/quality_report.html
  previews/<asset_id>/preview_manifest.json
  previews/<asset_id>/index.html
  previews/<asset_id>/potree_manifest.json
  previews/<asset_id>/potree/metadata.json
  data/assets/<asset_id>/slices/<slice_name>/slice_plan.json
  data/assets/<asset_id>/slices/<slice_name>/<slice_id>.<format>
  data/assets/<asset_id>/slices/<slice_name>/segments/<name>/rule_segmentation_plan.json
  data/assets/<asset_id>/slices/<slice_name>/segments/<name>/<segmentation_id>-labels.json
  reports/<asset_id>/segments/<slice_name>/<name>/segmentation_summary.json
  reports/<asset_id>/segments/<slice_name>/<name>/segmentation_summary.html
  reports/module_status.json
  reports/module_status.md
  logs/
```


## Phase 2 Commands

Plan FLS raw ingest:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-fls-ingest `
  --project-root .\workspace `
  --asset-id site-a `
  --raw-files C:\scan\a.fls C:\scan\b.fls `
  --output-las C:\out\site-a.las
```

Execute FLS raw ingest:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-fls-ingest `
  --project-root .\workspace `
  --asset-id site-a `
  --converter-path C:\tools\fls2las.exe
```

Plan Gaussian Splatting:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-gaussian-splat `
  --project-root .\workspace `
  --asset-id site-a `
  --name baseline `
  --source-las C:\out\site-a.las `
  --iterations 3000
```

Execute Gaussian Splatting:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-gaussian-splat `
  --project-root .\workspace `
  --asset-id site-a `
  --name baseline `
  --trainer-path C:\tools\train_3dgs.py
```

Publish the Phase 2 viewer:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli publish-phase2-viewer `
  --project-root .\workspace `
  --asset-id site-a `
  --potree-path previews/site-a/potree/metadata.json `
  --splat-path previews/site-a/splats/baseline/point_cloud.ply `
  --report reports/site-a/quality_report.html
```

Write the Phase 2 status report:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli phase2-status --project-root .\workspace
```
## Optional LAS Dependency

Install `laspy` before reading real LAS/LAZ metadata:

```powershell
python -m pip install laspy
```

`pdal`, `open3d`, and `PotreeConverter` are intentionally not hard dependencies. The project calls them through adapter boundaries so the workflow can be tested without installing every point-cloud tool up front.

## Current M5 Boundary

M5 currently creates and executes a `slice_plan.json` manifest. It records crop bounds, optional voxel size, output format, and target file name.

The default executor is a placeholder adapter. It writes a placeholder output file and updates `slice_plan.json` from `planned` to `completed`. A PDAL adapter boundary is available through `--engine pdal`. It writes `pdal_pipeline.json` and calls `pdal pipeline <pipeline.json>`. It requires a local PDAL executable path when PDAL is not on PATH.

## Current M4 Potree Boundary

M4 now includes a Potree publisher adapter:

- `pc_system.potree_publisher.PotreePublishRequest`
- `publish_potree`
- CLI command: `publish-potree`

The adapter calls an external `PotreeConverter` executable, writes `previews/<asset_id>/potree_manifest.json`, and expects Potree output under `previews/<asset_id>/potree/`. The project does not bundle PotreeConverter; pass its local executable path with `--converter-path`.


## Current M6/M7 Boundary

M6 provides a rule segmentation manifest, placeholder execution path, Open3D script adapter, bundled reference script, and summary reports:

- `pc_system.rule_segmentation.RuleSegmentationRequest`
- `build_rule_segmentation_plan`
- `execute_rule_segmentation_plan`
- `scripts/open3d_rule_segment.py`
- CLI commands: `plan-rule-segment`, `execute-rule-segment`, `report-rule-segment`

The default rules are `ground`, `plane`, `cluster`, and `noise`. The default executor writes an empty labels JSON and marks the plan as completed. `execute-rule-segment --engine open3d` writes `open3d_rule_segmentation_config.json`, calls `scripts/open3d_rule_segment.py` or another compatible Python script, and validates the generated labels JSON. The summary command reads labels JSON and writes auditable JSON/HTML count reports.

M7 adds `module-status`, which writes the Phase 1 completion report to `reports/module_status.json` and `reports/module_status.md`.

## Phase 1 Completion

All Phase 1 modules for the processed LAS/LAZ route are implemented and covered by tests. External tools such as `laspy`, `pdal`, `open3d`, and `PotreeConverter` remain optional adapter dependencies rather than required install-time dependencies.


## Phase 3 Commands

Check production tool paths:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli phase3-tool-check `
  --project-root .\workspace `
  --fls-converter C:\tools\fls2las.exe `
  --pdal-path C:\tools\pdal.exe `
  --potree-converter C:\tools\PotreeConverter.exe `
  --gaussian-trainer C:\tools\train_3dgs.py `
  --open3d-script .\scripts\open3d_rule_segment.py
```
## Phase 2 Completion

Phase 2 adds FLS raw ingest manifests, Gaussian Splatting manifests, a unified viewer manifest, and a Phase 2 status report. Real FLS conversion and production 3DGS training remain external adapter dependencies so the core workflow stays testable without proprietary or GPU-heavy tools installed.

## Phase 3 Progress

Phase 3 has started with P3-M1 production tool checks. The current implementation writes `reports/phase3_tool_check.json` and `reports/phase3_tool_check.md`. Next modules should add production run planning, production run reports, and deployment packaging checks.
