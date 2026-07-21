from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_phase14_docs_cover_operation_recovery_and_training_boundaries():
    document = (
        ROOT / "docs" / "phase14-segmentation-correction-loop.md"
    ).read_text(encoding="utf-8")
    for term in (
        "correction.html",
        "source_point_index",
        "expected_revision",
        "confirm",
        "merge",
        "split",
        "relabel",
        "mark_noise",
        "undo",
        "redo",
        "restore",
        "published",
        "immutable",
        "segmentation_feedback",
        "golden_regression",
        "evaluation-only",
        "Champion/Challenger",
    ):
        assert term in document


def test_readme_and_inventory_expose_phase14():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    inventory = (
        ROOT / "docs" / "system-function-module-inventory.md"
    ).read_text(encoding="utf-8")

    assert "Phase 14" in readme
    assert "create-segmentation-correction" in readme
    assert "Phase 14" in inventory
    assert "P14-M" in inventory
