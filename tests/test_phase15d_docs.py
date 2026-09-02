from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase15d_operator_document_covers_approved_boundaries():
    text = (ROOT / "docs/phase15d-human-decisions-bindings.md").read_text(encoding="utf-8")
    for fragment in ("# Phase 15D 人工决策", "operator", "expert", "auditor", "待处理", "已处理", "已陈旧",
                     "第一提交者获胜", "commit.json", "idempotency_key", "将创建新版本，不修改旧绑定",
                     "model-decisions.html", "model-matching-lab.html", "不训练", "Phase 15E", "Phase 16"):
        assert fragment in text


def test_phase15d_inventory_and_readme_link_the_delivery():
    for name in ("README.md", "docs/current-development-inventory.md", "docs/system-function-module-inventory.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "phase15d-human-decisions-bindings.md" in text
        assert "Phase 15D" in text and "Phase 15E" in text
