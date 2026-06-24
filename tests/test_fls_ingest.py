import json
from pathlib import Path
from uuid import uuid4

from pc_system.fls_ingest import FlsIngestRequest, build_fls_ingest_plan, execute_fls_ingest_plan, write_fls_ingest_plan


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_fls_ingest_plan_records_raw_files_and_output_las():
    plan = build_fls_ingest_plan(
        FlsIngestRequest(
            asset_id="site-a",
            raw_files=[Path("C:/scan/a.fls"), Path("C:/scan/b.fls")],
            output_las=Path("C:/out/site-a.las"),
            registration="external",
        )
    )

    assert plan["asset_id"] == "site-a"
    assert plan["raw_format"] == "fls"
    assert plan["raw_files"] == ["C:/scan/a.fls", "C:/scan/b.fls"]
    assert plan["output_las"] == "C:/out/site-a.las"
    assert plan["registration"] == "external"
    assert plan["status"] == "planned"


def test_execute_fls_ingest_plan_invokes_runner_and_marks_completed():
    workspace = case_dir("fls-ingest")
    output_las = workspace / "site-a.las"
    plan_path = workspace / "fls_ingest_plan.json"
    write_fls_ingest_plan(
        {
            "asset_id": "site-a",
            "raw_format": "fls",
            "raw_files": ["C:/scan/a.fls"],
            "output_las": str(output_las),
            "registration": "external",
            "status": "planned",
        },
        workspace,
    )
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        output_las.write_text("las", encoding="utf-8")
        return 0

    updated = execute_fls_ingest_plan(plan_path, converter_path=Path("C:/tools/fls2las.exe"), runner=fake_runner)

    assert calls == [["C:/tools/fls2las.exe", "--output", str(output_las), "C:/scan/a.fls"]]
    assert updated["status"] == "completed"
    assert updated["execution"]["engine"] == "fls-converter"
