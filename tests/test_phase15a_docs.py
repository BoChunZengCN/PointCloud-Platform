from pathlib import Path


ROOT = Path(__file__).parents[1]


def _phase15_row(readme: str) -> str:
    return next(
        (
            line
            for line in readme.splitlines()
            if line.startswith("| Phase 15 ")
        ),
        "",
    )


def test_phase15a_document_covers_operator_and_integrator_contracts():
    document = (ROOT / "docs" / "phase15-model-library.md").read_text(
        encoding="utf-8"
    )
    for term in (
        "STL",
        "OBJ",
        "PLY",
        "mm",
        "cm",
        "model_manifest.json",
        "不可变",
        "operation_id",
        "幂等",
        "哈希链",
        "configured_token",
        "PC_SYSTEM_PRINCIPALS_JSON",
        "X-API-Key",
        "imports/models",
        "model_version_exists",
        "Phase 15B",
    ):
        assert term in document


def test_readme_marks_only_phase15a_foundation_complete():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Phase 15A" in readme
    assert (
        "| Phase 15A | CAD 模型库基础 / CAD model library foundation "
        "| 已完成 / Done |" in readme
    )
    phase15 = _phase15_row(readme)
    assert not phase15 or "已完成 / Done" not in phase15
