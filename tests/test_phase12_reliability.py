import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.asset import LasAssetInfo, build_asset_metadata
from pc_system.asset_registry import build_asset_registry
from pc_system.cli import main
from pc_system.cli_parser import build_parser
from pc_system.identifiers import validate_identifier
from pc_system.phase11_batch import build_batch_run_plan
from pc_system.phase11_project_gate import build_project_gate
from pc_system.point_cloud_analysis import analyze_point_records
from pc_system.production_pipeline import build_production_run_plan


def sample_plan(asset_id: str = "scan") -> dict:
    return {
        "asset_id": asset_id,
        "steps": [{"step_id": "ingest", "phase": "Phase 1", "name": "Ingest"}],
    }


def sample_metadata(asset_id: str = "scan") -> dict:
    return {
        "asset_id": asset_id,
        "file": {"path": "C:/data/scan.las", "name": "scan.las"},
        "las": {"point_count": 10, "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}},
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_p12_rejects_path_like_identifiers(tmp_path: Path):
    plan_path = tmp_path / "reports" / "production_runs" / "scan" / "production_run_plan.json"
    write_json(plan_path, sample_plan())
    client = TestClient(create_app(tmp_path))

    response = client.post("/runs/scan/jobs", json={"job_id": "..\\outside"})

    assert response.status_code == 400
    assert not (tmp_path / "reports" / "outside.json").exists()
    with pytest.raises(ValueError, match="Invalid asset_id"):
        validate_identifier("../scan", "asset_id")
    info = LasAssetInfo(1, {"min": [0, 0, 0], "max": [0, 0, 0]}, False, False, False, [1, 1, 1], [0, 0, 0], "0")
    with pytest.raises(ValueError, match="Invalid asset_id"):
        build_asset_metadata(tmp_path / "..las", info)


def test_p12_requires_api_key_in_production(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PC_SYSTEM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="required in production"):
        create_app(tmp_path, run_mode="production", api_key=None)


def test_p12_production_plan_commands_are_parseable():
    parser = build_parser()
    plan = build_production_run_plan(sample_metadata(), project_root=Path("workspace"))

    for step in plan["steps"]:
        parsed = parser.parse_args(step["command"][1:])
        assert parsed.project_root == Path("workspace")


def test_p12_cli_delivery_manifest_records_actual_gate_decision(tmp_path: Path):
    metadata = tmp_path / "data" / "assets" / "scan" / "asset.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}", encoding="utf-8")
    registry = build_asset_registry([sample_metadata()], project_root=tmp_path)
    write_json(tmp_path / "data" / "assets" / "asset_index.json", registry)
    write_json(tmp_path / "reports" / "quality_gates" / "scan" / "quality_gate.json", {"asset_id": "scan", "status": "passed"})

    exit_code = main(["export-delivery-package", "--project-root", str(tmp_path), "--asset-id", "scan"])

    manifest = json.loads((tmp_path / "delivery" / "scan" / "delivery_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert manifest["delivery_gate_decision"]["reason"] == "passed"


def test_p12_registry_reports_real_artifact_status(tmp_path: Path):
    metadata = sample_metadata()
    registry = build_asset_registry([metadata], project_root=tmp_path)
    assert registry["assets"][0]["artifact_status"]["reports"]["quality_report"] is False

    report = tmp_path / "reports" / "scan" / "quality_report.html"
    report.parent.mkdir(parents=True)
    report.write_text("ok", encoding="utf-8")
    registry = build_asset_registry([metadata], project_root=tmp_path)
    assert registry["assets"][0]["artifact_status"]["reports"]["quality_report"] is True


def test_p12_empty_project_and_missing_gate_do_not_pass():
    empty = build_project_gate({"assets": []}, {})
    missing = build_project_gate(
        {"assets": [{"asset_id": "a"}, {"asset_id": "b"}]},
        {"a": {"status": "review_required"}},
    )
    assert empty["status"] == "missing"
    assert missing["status"] == "missing"


def test_p12_rejects_invalid_grid_size():
    with pytest.raises(ValueError, match="grid_cell_size"):
        analyze_point_records("scan", [{"x": 0, "y": 0, "z": 0}], grid_cell_size=0)


def test_p12_batch_commands_are_parseable_and_project_gate_is_global():
    parser = build_parser()
    plan = build_batch_run_plan(["scan-a", "scan-b"], project_root="workspace")
    assert sum(step["operation"] == "delivery_gate" for step in plan["steps"]) == 1
    for step in plan["steps"]:
        parser.parse_args(step["command"][1:])
