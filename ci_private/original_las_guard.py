"""无浏览器依赖的原始 LAS 请求门禁。"""

from urllib.parse import unquote, urlsplit


class OriginalLasRequestGuard:
    """按请求事件检查原始 LAS，包括失败或中止的请求。"""

    def __init__(self):
        self.paths = []

    def observe(self, request):
        path = unquote(urlsplit(request.url).path)
        if path.casefold().endswith(".las"):
            self.paths.append(path)

    def assert_none(self):
        assert not self.paths, f"浏览器请求了原始 LAS：{', '.join(self.paths)}"
