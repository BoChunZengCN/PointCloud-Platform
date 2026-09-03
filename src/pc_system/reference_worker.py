"""扫描解析子进程协议；输出 JSON，不接受反序列化可执行对象。"""

import json
from pathlib import Path
import sys

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.reference_reader import MAX_RESULT_BYTES, read_reference_file


def main() -> int:
    try:
        if len(sys.argv) != 4:
            raise ModelMatchingError("reference_format_invalid", "扫描解析参数无效。")
        result = read_reference_file(Path(sys.argv[1]), declared_unit=sys.argv[2], maximum_points=int(sys.argv[3]))
        payload = json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_RESULT_BYTES:
            raise ModelMatchingError("reference_input_limit", "扫描解析输出超过 limit 限制。")
        sys.stdout.buffer.write(payload)
        return 0
    except ModelMatchingError as exc:
        error = {"ok": False, "code": exc.code, "message": str(exc)}
    except Exception:
        error = {"ok": False, "code": "reference_format_invalid", "message": "扫描解析未能完成。"}
    sys.stdout.buffer.write(json.dumps(error, ensure_ascii=False).encode("utf-8"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
