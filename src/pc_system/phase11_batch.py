from typing import Any


def build_batch_run_plan(asset_ids: list[str], operations: list[str] | None = None) -> dict[str, Any]:
    """为多个资产生成批处理运行计划，不直接执行外部命令。"""

    resolved_operations = operations or ["analyze", "quality_gate", "segment", "delivery_gate"]
    command_by_operation = {
        "analyze": "analyze-asset",
        "quality_gate": "check-quality-gate",
        "segment": "segment-asset-objects",
        "delivery_gate": "check-project-gate",
    }
    steps = []
    order = 1
    for asset_id in asset_ids:
        for operation in resolved_operations:
            command = command_by_operation.get(operation, operation)
            step = {"order": order, "asset_id": asset_id, "operation": operation, "command": [command, "--asset-id", asset_id]}
            if operation == "delivery_gate":
                step["command"] = [command]
            steps.append(step)
            order += 1
    return {"schema_version": "1.0", "module": "Batch Run Plan", "asset_count": len(asset_ids), "operations": resolved_operations, "steps": steps}
