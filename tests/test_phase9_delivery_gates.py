import json
from pathlib import Path
from uuid import uuid4


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def quality_gate(status: str) -> dict:
    return {
        "asset_id": "scan",
        "status": status,
        "severity": "critical" if status == "blocked" else "warning",
        "finding_count": 1 if status != "passed" else 0,
        "actions": ["Review quality gate."],
    }


def test_p9_m1_evaluates_delivery_gate_policy():
    from pc_system.delivery_gate import evaluate_delivery_gate

    assert evaluate_delivery_gate(quality_gate("passed"))["allowed"] is True

    review = evaluate_delivery_gate(quality_gate("review_required"))
    assert review["allowed"] is False
    assert review["reason"] == "review_required"
    assert evaluate_delivery_gate(quality_gate("review_required"), allow_review_required=True)["allowed"] is True

    blocked = evaluate_delivery_gate(quality_gate("blocked"), allow_review_required=True)
    assert blocked["allowed"] is False
    assert blocked["reason"] == "blocked"


def write_asset_registry(project: Path, asset_id: str = "scan") -> None:
    registry_dir = project / "data" / "assets"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "asset_index.json").write_text(
        json.dumps({
            "schema_version": "1.0",
            "asset_count": 1,
            "assets": [{"asset_id": asset_id, "metadata_path": f"data/assets/{asset_id}/asset.json", "report_paths": {}, "viewer_paths": {}}],
        }),
        encoding="utf-8",
    )


def write_quality_gate(project: Path, status: str, asset_id: str = "scan") -> None:
    gate_dir = project / "reports" / "quality_gates" / asset_id
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "quality_gate.json").write_text(json.dumps(quality_gate(status)), encoding="utf-8")


def test_p9_m2_export_delivery_blocks_blocked_quality_gate():
    from pc_system.cli import main

    project = case_dir("p9-blocked-export") / "workspace"
    write_asset_registry(project)
    write_quality_gate(project, "blocked")

    exit_code = main(["export-delivery-package", "--project-root", str(project), "--asset-id", "scan"])

    assert exit_code == 2
    assert not (project / "delivery" / "scan" / "delivery_manifest.json").exists()


def test_p9_m3_export_delivery_blocks_review_required_without_override():
    from pc_system.cli import main

    project = case_dir("p9-review-export-block") / "workspace"
    write_asset_registry(project)
    write_quality_gate(project, "review_required")

    exit_code = main(["export-delivery-package", "--project-root", str(project), "--asset-id", "scan"])

    assert exit_code == 2
    assert not (project / "delivery" / "scan" / "delivery_manifest.json").exists()


def test_p9_m3_export_delivery_allows_review_required_with_override():
    from pc_system.cli import main

    project = case_dir("p9-review-export-allow") / "workspace"
    write_asset_registry(project)
    write_quality_gate(project, "review_required")

    exit_code = main([
        "export-delivery-package",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--allow-review-required",
    ])

    assert exit_code == 0
    assert (project / "delivery" / "scan" / "delivery_manifest.json").exists()


def test_p9_m4_deployment_checklist_includes_quality_gate_item():
    from pc_system.deployment_checklist import build_deployment_checklist

    project = case_dir("p9-deployment-gate") / "workspace"
    gate_path = project / "reports" / "quality_gates" / "scan" / "quality_gate.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(json.dumps(quality_gate("blocked")), encoding="utf-8")

    checklist = build_deployment_checklist(project, "scan", exists=lambda path: path == gate_path)

    gate_items = [item for item in checklist["items"] if item["name"] == "quality_gate"]
    assert gate_items == [{"name": "quality_gate", "required": True, "path": "reports/quality_gates/scan/quality_gate.json", "status": "blocked"}]
    assert checklist["status"] == "blocked"
    assert checklist["ready"] is False


def test_p9_m5_frontend_has_delivery_gate_notice_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    css = (root / "frontend" / "app.css").read_text(encoding="utf-8")

    assert "delivery-gate-notice" in html
    assert "renderDeliveryGateNotice" in script
    assert "allow-review-required" in script
    assert "blocked" in script
    assert ".delivery-gate-notice" in css


def test_p9_m6_docs_describe_delivery_gate_modules():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    doc_path = root / "docs" / "phase9-delivery-gates.md"

    assert "P9-M1" in readme
    assert "P9-M6" in readme
    assert "--allow-review-required" in readme
    assert "blocked" in readme
    assert doc_path.exists()
    doc = doc_path.read_text(encoding="utf-8")
    assert "P9-M1" in doc
    assert "P9-M6" in doc
    assert "export-delivery-package" in doc
