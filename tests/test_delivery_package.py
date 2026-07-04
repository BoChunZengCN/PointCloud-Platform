import json
import zipfile
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main
from pc_system.delivery_package import build_delivery_package, export_delivery_package


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_file(path: Path, content: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_passed_quality_gate(project: Path, asset_id: str = "scan") -> None:
    write_file(
        project / "reports" / "quality_gates" / asset_id / "quality_gate.json",
        json.dumps({"asset_id": asset_id, "status": "passed", "severity": "info", "finding_count": 0, "actions": []}),
    )


def sample_registry(asset_id: str = "scan") -> dict:
    return {
        "schema_version": "1.0",
        "asset_count": 1,
        "assets": [
            {
                "asset_id": asset_id,
                "metadata_path": f"data/assets/{asset_id}/asset.json",
                "viewer_paths": {
                    "viewer_url": f"previews/{asset_id}/phase2_viewer.html",
                    "manifest_path": f"previews/{asset_id}/phase2_viewer_manifest.json",
                    "potree_manifest_path": f"previews/{asset_id}/potree_manifest.json",
                    "report_path": f"reports/production_runs/{asset_id}/production_run_report.md",
                },
                "report_paths": {
                    "quality_report": f"reports/{asset_id}/quality_report.html",
                    "production_plan": f"reports/production_runs/{asset_id}/production_run_plan.md",
                    "production_report": f"reports/production_runs/{asset_id}/production_run_report.md",
                },
            }
        ],
    }


def test_build_delivery_package_collects_ready_and_missing_outputs():
    project = case_dir("delivery-package") / "workspace"
    write_file(project / "data" / "assets" / "scan" / "asset.json", "{}")
    write_file(project / "previews" / "scan" / "phase2_viewer.html", "<html></html>")
    registry = sample_registry()

    package = build_delivery_package(project, registry, "scan")

    assert package["module"] == "Delivery Package Export"
    assert package["asset_id"] == "scan"
    assert package["status"] == "partial"
    assert package["summary"]["ready"] == 2
    assert package["summary"]["missing"] > 0
    ready_items = [item for item in package["items"] if item["status"] == "ready"]
    assert ready_items[0]["delivery_path"].startswith("files/")
    assert any(item["name"] == "phase2_viewer_html" for item in ready_items)


def test_export_delivery_package_copies_files_and_writes_manifests():
    project = case_dir("delivery-export") / "workspace"
    registry = sample_registry()
    write_file(project / "data" / "assets" / "scan" / "asset.json", "{\"asset_id\":\"scan\"}")
    write_file(project / "previews" / "scan" / "phase2_viewer.html", "<html></html>")

    outputs = export_delivery_package(project, registry, "scan", project / "delivery" / "scan")

    manifest = json.loads(outputs["json"].read_text(encoding="utf-8"))
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert outputs["json"].name == "delivery_manifest.json"
    assert outputs["markdown"].name == "delivery_manifest.md"
    assert manifest["summary"]["ready"] == 2
    assert (outputs["root"] / "files" / "data" / "assets" / "scan" / "asset.json").exists()
    assert (outputs["root"] / "files" / "previews" / "scan" / "phase2_viewer.html").exists()
    assert "# Delivery Package" in markdown
    assert "| phase2_viewer_html | ready | previews/scan/phase2_viewer.html |" in markdown




def test_export_delivery_package_can_write_zip_archive():
    project = case_dir("delivery-export-zip") / "workspace"
    registry = sample_registry()
    write_file(project / "data" / "assets" / "scan" / "asset.json", "{\"asset_id\":\"scan\"}")
    write_file(project / "previews" / "scan" / "phase2_viewer.html", "<html></html>")

    outputs = export_delivery_package(project, registry, "scan", project / "delivery" / "scan", make_zip=True)

    assert outputs["zip"].name == "scan.zip"
    assert outputs["zip"].exists()
    with zipfile.ZipFile(outputs["zip"]) as archive:
        names = set(archive.namelist())
    assert "delivery_manifest.json" in names
    assert "delivery_manifest.md" in names
    assert "files/data/assets/scan/asset.json" in names
    assert "files/previews/scan/phase2_viewer.html" in names

def test_delivery_package_does_not_copy_paths_outside_project_root():
    project = case_dir("delivery-path-safety") / "workspace"
    outside = project.parent / "outside.html"
    write_file(outside, "secret")
    registry = sample_registry()
    registry["assets"][0]["viewer_paths"]["viewer_url"] = "../outside.html"

    package = build_delivery_package(project, registry, "scan")

    viewer_item = next(item for item in package["items"] if item["name"] == "phase2_viewer_html")
    assert viewer_item["status"] == "missing"
    assert viewer_item["delivery_path"] == ""

def test_cli_export_delivery_package_reads_registry_and_writes_package():
    project = case_dir("cli-delivery-export") / "workspace"
    registry = sample_registry()
    write_file(project / "data" / "assets" / "asset_index.json", json.dumps(registry))
    write_file(project / "data" / "assets" / "scan" / "asset.json", "{\"asset_id\":\"scan\"}")
    write_file(project / "previews" / "scan" / "phase2_viewer.html", "<html></html>")
    write_passed_quality_gate(project)

    exit_code = main([
        "export-delivery-package",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
    ])

    manifest_path = project / "delivery" / "scan" / "delivery_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["asset_id"] == "scan"
    assert (manifest_path.parent / "delivery_manifest.md").exists()


def test_cli_export_delivery_package_can_write_zip_archive():
    project = case_dir("cli-delivery-export-zip") / "workspace"
    registry = sample_registry()
    write_file(project / "data" / "assets" / "asset_index.json", json.dumps(registry))
    write_file(project / "data" / "assets" / "scan" / "asset.json", "{\"asset_id\":\"scan\"}")
    write_passed_quality_gate(project)
    write_file(project / "previews" / "scan" / "phase2_viewer.html", "<html></html>")
    write_passed_quality_gate(project)

    exit_code = main([
        "export-delivery-package",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--zip",
    ])

    assert exit_code == 0
    assert (project / "delivery" / "scan.zip").exists()


