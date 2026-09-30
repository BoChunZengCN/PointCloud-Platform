"""真实生产模式 API 与静态工作台的本地浏览器验收夹具。"""

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect

from pc_system.api import create_app
from phase15c_support import DeterministicRegistrationEngine
from phase15d_support import prepare_decision_case
from phase15e_support import EXPERT, import_request, scan_asset, scan_source


expect.set_options(timeout=15000)


@pytest.fixture
def browser_server(tmp_path, request):
    mode = getattr(request, "param", "passed")
    if mode.startswith("reference") and mode != "reference-labels":
        staging = tmp_path / "imports" / "models"
        staging.mkdir(parents=True)
        scan_source(staging / "browser-scan.ply")
        (staging / "warning-scan.ply").write_text(
            "ply\nformat ascii 1.0\nelement vertex 64\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
            + "".join(f"{x} {y} 0\n" for x in range(8) for y in range(8)),
            encoding="ascii",
        )
        from pc_system.reference_import import import_reference_version
        scan_asset(tmp_path, model_id="scan-readonly")
        import_reference_version(tmp_path, **import_request(staging / "browser-scan.ply", model_id="scan-readonly", version_id="v1"))
        if mode == "reference-history":
            from pc_system.model_release import release_model_version
            from pc_system.reference_review import review_reference_version
            for version_id in ("v2", "v3", "v4"):
                request = import_request(
                    staging / "browser-scan.ply",
                    model_id="scan-readonly",
                    version_id=version_id,
                    operation_id=f"import-history-{version_id}",
                    request_id=f"req-history-{version_id}",
                    idempotency_key=f"idem-history-{version_id}",
                )
                request["license_name"] = f"历史许可 {version_id}"
                request["provenance"] = {"source": f"历史来源 {version_id}", "preprocessing": f"历史预处理 {version_id}"}
                import_reference_version(
                    tmp_path,
                    **request,
                )
            for sequence, version_id in enumerate(("v1", "v2"), 1):
                review_reference_version(
                    tmp_path,
                    model_id="scan-readonly",
                    version_id=version_id,
                    decision="approved",
                    reason=f"历史核验原因 {version_id}",
                    acknowledgements=["single_object", "metadata_and_rights", "coverage_limitations"],
                    principal=EXPERT,
                    operation_id=f"review-history-{version_id}",
                    request_id=f"req-review-history-{version_id}",
                    idempotency_key=f"idem-review-history-{version_id}",
                )
                release_model_version(
                    tmp_path,
                    model_id="scan-readonly",
                    version_id=version_id,
                    release_id=f"release-history-{version_id}",
                    action="activate",
                    expected_current_release_id=None if sequence == 1 else "release-history-v1",
                    rollback_of_release_id=None,
                    reason="历史夹具发布",
                    principal=EXPERT,
                    operation_id=f"release-history-{version_id}",
                    request_id=f"req-release-history-{version_id}",
                    idempotency_key=f"idem-release-history-{version_id}",
                )
            review_reference_version(
                tmp_path,
                model_id="scan-readonly",
                version_id="v4",
                decision="rejected",
                reason="历史夹具拒绝",
                acknowledgements=[],
                principal=EXPERT,
                operation_id="review-history-v4",
                request_id="req-review-history-v4",
                idempotency_key="idem-review-history-v4",
            )
        if mode in {"reference-pagination", "reference-pagination-slow"}:
            for sequence in range(50):
                version_id = f"v{sequence:03d}"
                import_reference_version(
                    tmp_path,
                    **import_request(
                        staging / "browser-scan.ply",
                        model_id="scan-readonly",
                        version_id=version_id,
                        operation_id=f"import-page-{version_id}",
                        request_id=f"req-page-{version_id}",
                        idempotency_key=f"idem-page-{version_id}",
                    ),
                )
    case = None if mode in {"empty", "reference", "reference-history", "reference-slow", "reference-pagination", "reference-pagination-slow"} else prepare_decision_case(
        tmp_path, mode="passed" if mode in {"slow", "reference-labels"} else mode
    )
    app = create_app(tmp_path, run_mode="production", api_key="phase15d-test-service-key",
        principal_bindings={role + "-token": {"actor_id": role + "-browser", "roles": [role]}
                            for role in ("operator", "expert", "auditor")},
        registration_engine_resolver=lambda _: DeterministicRegistrationEngine("passed"))
    if mode == "slow":
        @app.middleware("http")
        async def latency(request, call_next):
            if request.url.path == "/model-matching/decision-items":
                await asyncio.sleep(1)
            return await call_next(request)
    if mode == "reference-slow":
        @app.middleware("http")
        async def reference_latency(request, call_next):
            if request.method == "POST":
                await asyncio.sleep(1)
            return await call_next(request)
    if mode == "reference-pagination-slow":
        @app.middleware("http")
        async def reference_catalog_latency(request, call_next):
            if request.method == "GET" and request.url.path.endswith("/scanned-versions"):
                await asyncio.sleep(1)
            return await call_next(request)
    app.mount("/workbench", StaticFiles(directory=Path(__file__).resolve().parents[2] / "frontend"), name="workbench")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    errors = []
    def run():
        try:
            server.run(sockets=[sock])
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started and not errors, f"HTTP 服务启动失败：{errors}"
        yield {"url":f"http://127.0.0.1:{sock.getsockname()[1]}", "project_root":tmp_path,
               "case_id":None if case is None else case.request_fields["case_id"], "case":case}
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), "HTTP 测试服务未按时停止"
        assert not errors, f"HTTP 测试服务异常：{errors}"


@pytest.fixture
def open_workbench(new_context, browser_server):
    contexts, errors = [], []
    def open_page(role="operator", professional=False):
        context = new_context(extra_http_headers={"Authorization":"Bearer " + role + "-token"}, viewport={"width":1440,"height":1050})
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(browser_server["url"] + "/workbench/" + ("model-matching-lab.html" if professional else "model-decisions.html"))
        return page
    yield open_page
    for context in contexts:
        context.close()
    assert errors == [], f"页面 JavaScript 异常：{errors}"


@pytest.fixture
def open_reference_library(new_context, browser_server):
    contexts, errors = [], []
    def open_page(role="expert", model_id=None):
        context = new_context(viewport={"width":1440,"height":1050})
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        suffix = "" if model_id is None else "?model=" + model_id
        page.goto(browser_server["url"] + "/workbench/model-reference-library.html" + suffix)
        return page
    yield open_page
    for context in contexts:
        context.close()
    assert errors == [], f"页面 JavaScript 异常：{errors}"
