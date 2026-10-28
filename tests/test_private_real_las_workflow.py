"""私有样本 CI 工作流的可审计安全边界。"""

import re
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/private-real-las.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_private_fixture_runs_only_on_trusted_main_events():
    source = _workflow()
    assert re.search(r"(?m)^on:\s*$", source)
    assert re.search(r"(?m)^\s+push:\s*$", source)
    assert re.search(r"(?m)^\s+branches:\s*\[main\]\s*$", source)
    assert re.search(r"(?m)^\s+workflow_dispatch:\s*$", source)
    assert "pull_request" not in source
    assert "pull_request_target" not in source
    assert "github.ref == 'refs/heads/main'" in source
    assert "runs-on: windows-latest" in source


def test_private_fixture_fails_closed_and_never_uploads_artifacts():
    source = _workflow()
    assert "environment: private-real-las" in source
    assert "PRIVATE_FIXTURE_READ_TOKEN" in source
    assert source.count("secrets.PRIVATE_FIXTURE_READ_TOKEN") == 1
    assert "IsNullOrWhiteSpace($env:GH_TOKEN)" in source
    assert "upload-artifact" not in source
    assert "actions/cache" not in source
    assert "--tracing off --screenshot off" in source


def test_private_fixture_verification_precedes_complete_conversion_and_browser():
    source = _workflow()
    verify = source.index("verify_private_real_las.py")
    convert = source.index("prepare-full-preview")
    browse = source.index("ci_private/test_real_las_browser.py")
    assert verify < convert < browse
    assert "real-las-v1" in source
    assert "filtered-points-v1.las" in source
    assert "8b4a70194fa85ceafa51e017b58e455cf3539edc9ab212b2c92b4a1f2b15e887" in source.lower()
    assert "c140a8bce0ce129de762fbc0fd3ba7c21120ffd36d423dd2755abb39f32aebe5" in source.lower()
