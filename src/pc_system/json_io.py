import json
from pathlib import Path
from typing import Any


def write_json(data: dict[str, Any], path: Path) -> Path:
    """统一的 JSON 写出函数。

    所有 JSON 文件都使用 UTF-8 和缩进格式，方便后续人工检查和调试。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
