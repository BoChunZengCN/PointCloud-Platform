from typing import Any


MESSAGE_BY_REASON = {
    "passed": "Quality gate passed; delivery export is allowed.",
    "review_required": "Quality gate requires review before delivery export.",
    "blocked": "Quality gate is blocked; delivery export is not allowed.",
    "missing": "Quality gate report is missing; delivery export requires a gate report.",
}


def evaluate_delivery_gate(gate: dict[str, Any] | None, allow_review_required: bool = False) -> dict[str, Any]:
    """判断交付导出是否可继续，返回 CLI/API/前端可复用的决策。"""

    if gate is None:
        return {"allowed": False, "reason": "missing", "message": MESSAGE_BY_REASON["missing"]}
    status = gate.get("status", "missing")
    if status == "passed":
        return {"allowed": True, "reason": "passed", "message": MESSAGE_BY_REASON["passed"]}
    if status == "review_required":
        allowed = bool(allow_review_required)
        return {"allowed": allowed, "reason": "review_required", "message": MESSAGE_BY_REASON["review_required"]}
    if status == "blocked":
        return {"allowed": False, "reason": "blocked", "message": MESSAGE_BY_REASON["blocked"]}
    return {"allowed": False, "reason": "missing", "message": MESSAGE_BY_REASON["missing"]}
