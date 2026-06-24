import json
from pathlib import Path
from uuid import uuid4

from pc_system.phase2_viewer import build_phase2_viewer_manifest, publish_phase2_viewer


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_phase2_viewer_manifest_combines_potree_splat_and_reports():
    manifest = build_phase2_viewer_manifest(
        asset_id="site-a",
        potree_path=Path("previews/site-a/potree/metadata.json"),
        splat_path=Path("previews/site-a/splats/point_cloud.ply"),
        reports=[Path("reports/site-a/quality_report.html")],
    )

    assert manifest["asset_id"] == "site-a"
    assert manifest["views"]["potree"] == "previews/site-a/potree/metadata.json"
    assert manifest["views"]["gaussian_splat"] == "previews/site-a/splats/point_cloud.ply"
    assert manifest["reports"] == ["reports/site-a/quality_report.html"]


def test_publish_phase2_viewer_writes_manifest_and_html():
    workspace = case_dir("phase2-viewer")
    outputs = publish_phase2_viewer(
        build_phase2_viewer_manifest(
            asset_id="site-a",
            potree_path=Path("potree/metadata.json"),
            splat_path=Path("splats/point_cloud.ply"),
            reports=[],
        ),
        workspace,
    )

    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    html = outputs["html"].read_text(encoding="utf-8")
    assert manifest["asset_id"] == "site-a"
    assert "splats/point_cloud.ply" in html
