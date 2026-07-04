import json
from pathlib import Path
from uuid import uuid4


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def analysis_with_findings(findings: list[dict]) -> dict:
    return {
        "asset_id": "scan",
        "point_count": 100,
        "rgb_coverage": 0.72,
        "findings": findings,
    }


def test_p8_m1_maps_analysis_findings_to_quality_gate():
    from pc_system.quality_gate import build_quality_gate

    gate = build_quality_gate(
        "scan",
        analysis_with_findings([
            {"code": "low_rgb_coverage", "severity": "warning", "message": "RGB coverage is low."},
            {"code": "high_z_span", "severity": "critical", "message": "Z span is high."},
        ]),
    )

    assert gate["asset_id"] == "scan"
    assert gate["status"] == "blocked"
    assert gate["severity"] == "critical"
    assert gate["finding_count"] == 2
    assert gate["actions"] == [
        "Review RGB colorization before delivery.",
        "Block delivery until high elevation span is reviewed.",
    ]


def test_p8_m2_writes_quality_gate_json_and_markdown():
    from pc_system.quality_gate import build_quality_gate, write_quality_gate_report

    output_dir = case_dir("p8-gate-report")
    gate = build_quality_gate("scan", analysis_with_findings([]))

    outputs = write_quality_gate_report(gate, output_dir)

    assert outputs["json"] == output_dir / "quality_gate.json"
    assert outputs["markdown"] == output_dir / "quality_gate.md"
    assert json.loads(outputs["json"].read_text(encoding="utf-8"))["status"] == "passed"
    assert "Quality Gate: scan" in outputs["markdown"].read_text(encoding="utf-8")


def test_p8_m3_cli_writes_quality_gate_from_analysis_report():
    from pc_system.cli import main

    project = case_dir("p8-cli-gate") / "workspace"
    analysis_dir = project / "reports" / "analysis" / "scan"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "point_cloud_analysis.json").write_text(
        json.dumps(analysis_with_findings([{ "code": "low_rgb_coverage", "severity": "warning", "message": "RGB low." }])),
        encoding="utf-8",
    )

    exit_code = main(["check-quality-gate", "--project-root", str(project), "--asset-id", "scan"])

    output = project / "reports" / "quality_gates" / "scan" / "quality_gate.json"
    gate = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert gate["status"] == "review_required"
    assert gate["severity"] == "warning"


def test_p8_m4_api_reads_quality_gate_report():
    from fastapi.testclient import TestClient

    from pc_system.api import create_app
    from pc_system.quality_gate import build_quality_gate, write_quality_gate_report

    project = case_dir("p8-api-gate") / "workspace"
    gate = build_quality_gate("scan", analysis_with_findings([]))
    write_quality_gate_report(gate, project / "reports" / "quality_gates" / "scan")

    response = TestClient(create_app(project)).get("/quality-gates/scan")

    assert response.status_code == 200
    assert response.json()["asset_id"] == "scan"
    assert response.json()["status"] == "passed"


def test_p8_m5_frontend_has_quality_gate_status_bar_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    css = (root / "frontend" / "app.css").read_text(encoding="utf-8")

    assert "quality-gate-status-bar" in html
    assert "fetchQualityGate" in script
    assert "renderQualityGateStatus" in script
    assert "/quality-gates/" in script
    assert "review_required" in script
    assert ".quality-gate-status-bar" in css


def test_p8_m6_docs_describe_quality_gate_modules():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    doc_path = root / "docs" / "phase8-quality-gates.md"

    assert "P8-M1" in readme
    assert "P8-M6" in readme
    assert "check-quality-gate" in readme
    assert "GET /quality-gates/<asset_id>" in readme
    assert doc_path.exists()
    doc = doc_path.read_text(encoding="utf-8")
    assert "P8-M1" in doc
    assert "P8-M6" in doc
    assert "quality_gate.json" in doc
