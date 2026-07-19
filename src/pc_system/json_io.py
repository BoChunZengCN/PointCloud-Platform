import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json(data: dict[str, Any], path: Path) -> Path:
    """统一的 JSON 写出函数。

    所有 JSON 文件都使用 UTF-8 和缩进格式，方便后续人工检查和调试。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary_path = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return path
