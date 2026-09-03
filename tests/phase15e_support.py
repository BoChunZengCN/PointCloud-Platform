"""扫描参考模板的真实点式文件与操作身份夹具。"""

from pc_system.model_library import create_model_asset
from pc_system.model_matching_identity import Principal

EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")


def scan_asset(root, model_id="scan-pump", **overrides):
    values = dict(model_id=model_id, display_name="扫描泵", category_id="pump", manufacturer="示例厂商",
                  model_number="P-1", keywords=["pump"], tags=["参考"], source_family="scanned_reference",
                  principal=EXPERT, operation_id=f"asset-{model_id}", request_id=f"req-{model_id}", idempotency_key=f"idem-{model_id}")
    values.update(overrides)
    return create_model_asset(root, **values)


def scan_source(path, *, count=64):
    points = [(x, y, z) for x in range(4) for y in range(4) for z in range(4)][:count]
    header = f"ply\nformat ascii 1.0\nelement vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
    path.write_text(header + "".join(f"{x} {y} {z}\n" for x, y, z in points), encoding="ascii")
    return path


def import_request(path, *, version_id="v1", model_id="scan-pump", **overrides):
    values = dict(model_id=model_id, version_id=version_id, source_path=path, declared_unit="m", license_name="自有扫描授权",
                  provenance={"source": "夹具扫描", "preparation": "单对象裁剪", "scan_time": None}, principal=EXPERT,
                  operation_id=f"import-{model_id}-{version_id}", request_id=f"req-{model_id}-{version_id}", idempotency_key=f"idem-{model_id}-{version_id}")
    values.update(overrides)
    return values
