from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN_DIR = ROOT / "frontend" / "design-options"


def test_three_design_options_are_available_for_decision():
    index = (DESIGN_DIR / "index.html").read_text(encoding="utf-8")

    assert "design-a-production-workbench.html" in index
    assert "design-b-project-dashboard.html" in index
    assert "design-c-viewer-first.html" in index
    assert "生产工作台型" in index
    assert "项目驾驶舱型" in index
    assert "查看器优先型" in index


def test_each_design_option_has_distinct_product_positioning():
    option_a = (DESIGN_DIR / "design-a-production-workbench.html").read_text(encoding="utf-8")
    option_b = (DESIGN_DIR / "design-b-project-dashboard.html").read_text(encoding="utf-8")
    option_c = (DESIGN_DIR / "design-c-viewer-first.html").read_text(encoding="utf-8")

    assert "data-design=\"production-workbench\"" in option_a
    assert "data-design=\"project-dashboard\"" in option_b
    assert "data-design=\"viewer-first\"" in option_c
    assert "数据来源" in option_a
    assert "项目健康度" in option_b
    assert "图层" in option_c


def test_design_options_share_accessible_navigation_and_styles():
    stylesheet = (DESIGN_DIR / "design-options.css").read_text(encoding="utf-8")

    for file_name in [
        "index.html",
        "design-a-production-workbench.html",
        "design-b-project-dashboard.html",
        "design-c-viewer-first.html",
    ]:
        html = (DESIGN_DIR / file_name).read_text(encoding="utf-8")
        assert "design-options.css" in html
        assert "aria-label" in html

    assert ".option-nav" in stylesheet
    assert ".point-cloud-stage" in stylesheet
    assert ".viewer-shell" in stylesheet
