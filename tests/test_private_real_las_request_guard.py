"""私有真实 LAS 浏览器门禁的公开、无样本反证测试。"""

from types import SimpleNamespace

import pytest

from ci_private.original_las_guard import OriginalLasRequestGuard


def test_guard_rejects_las_request_with_query_and_mixed_case():
    guard = OriginalLasRequestGuard()
    guard.observe(SimpleNamespace(url="http://127.0.0.1/source.LAS?download=1"))

    with pytest.raises(AssertionError, match="source.LAS"):
        guard.assert_none()


def test_guard_rejects_aborted_las_request_without_response():
    guard = OriginalLasRequestGuard()
    request = SimpleNamespace(url="http://127.0.0.1/source.las")
    guard.observe(request)
    # 模拟请求中止：没有响应对象，也不触发 response 事件。

    with pytest.raises(AssertionError, match="source.las"):
        guard.assert_none()


def test_guard_ignores_binary_preview_requests():
    guard = OriginalLasRequestGuard()
    guard.observe(SimpleNamespace(url="http://127.0.0.1/octree.bin?offset=0"))
    guard.assert_none()
