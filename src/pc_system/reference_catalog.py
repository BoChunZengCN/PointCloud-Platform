"""扫描参考版本的只读目录投影。"""

import base64
import hashlib
import json
from pathlib import Path

from pc_system.model_feature_index import read_index_entries
from pc_system.model_index_release import load_current_model_feature_index_release
from pc_system.model_library import list_model_assets, load_model_asset
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import list_model_releases, load_current_model_release
from pc_system.reference_review import load_reference_review
from pc_system.reference_store import load_bundle, list_reference_versions
from pc_system.reference_geometry import select_reference_points


_STATUSES = frozenset({"all", "pending_review", "publishable", "published", "rejected"})
_BUSINESS_FIELDS = frozenset({
    "model_id", "display_name", "version_id", "source_kind", "quality_status",
    "review_status", "publication_status", "index_status", "risk_summary",
})


def _invalid(message: str) -> ModelMatchingError:
    return ModelMatchingError("invalid_reference_catalog_request", message)


def _visibility(principal: Principal) -> str:
    require_any_role(principal, {"operator", "expert", "auditor"})
    return "professional" if principal.roles.intersection({"expert", "auditor"}) else "business"


def _viewer_role(principal: Principal) -> str:
    """角色只从已验证 principal 投影，不能由客户端字段决定。"""
    return "expert" if "expert" in principal.roles else "auditor" if "auditor" in principal.roles else "operator"


def _cursor_filters(*, model_id: str | None, status: str, visibility: str) -> str:
    value = {"model_id": model_id, "status": status, "visibility": visibility}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _decode_cursor(cursor: str | None, *, filters: str) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        if type(cursor) is not str or len(cursor) > 2048:
            raise ValueError("cursor length")
        decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if set(decoded) != {"filters", "after"} or decoded["filters"] != filters:
            raise ValueError("cursor filters")
        after = decoded["after"]
        if type(after) is not list or len(after) != 2 or any(type(item) is not str for item in after):
            raise ValueError("cursor sort key")
        return after[0], after[1]
    except (TypeError, ValueError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise _invalid("Reference catalog cursor is invalid or belongs to different filters.") from exc


def _encode_cursor(*, filters: str, item: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(
        {"filters": filters, "after": [item["model_id"], item["version_id"]]},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).decode("ascii")


def _release_status(root: Path, model_id: str, version_id: str) -> tuple[str, bool]:
    current = load_current_model_release(root, model_id)
    releases = list_model_releases(root, model_id)
    if current is not None and current["version_id"] == version_id:
        return "current", True
    if any(release["version_id"] == version_id for release in releases):
        return "historical", False
    return "unpublished", False


def _index_status(root: Path, model_id: str, version_id: str, *, current: bool) -> str:
    if not current:
        return "not_indexed"
    try:
        release = load_current_model_feature_index_release(root)
    except ModelMatchingError as exc:
        if exc.code == "model_index_stale":
            return "update_required"
        raise
    if release is None:
        return "update_required"
    covered = any(
        item.get("model_id") == model_id and item.get("version_id") == version_id
        for item in read_index_entries(root, release["index_id"])
    )
    return "current" if covered else "update_required"


def _review_status(review: dict | None) -> str:
    if review is None:
        return "pending"
    return {"approved": "approved", "rejected": "rejected"}[review["decision"]]


def _matches_status(row: dict, status: str) -> bool:
    if status == "all":
        return True
    quality_status = row["quality_status"]
    review_status = row["review_status"]
    publication_status = row["publication_status"]
    if status == "rejected":
        return quality_status == "rejected" or review_status == "rejected"
    if status == "published":
        return publication_status in {"current", "historical"}
    if status == "publishable":
        return review_status == "approved" and publication_status != "current"
    return quality_status != "rejected" and review_status == "pending"


def _risk_summary(*, quality_status: str, review_status: str, index_status: str) -> list[str]:
    risks = []
    if quality_status == "rejected":
        risks.append("quality_rejected")
    if review_status == "rejected":
        risks.append("review_rejected")
    elif review_status == "pending":
        risks.append("review_pending")
    if index_status == "update_required":
        risks.append("index_update_required")
    return risks


def _projection(root: Path, asset: dict, manifest: dict, *, visibility: str) -> dict:
    model_id, version_id = manifest["model_id"], manifest["version_id"]
    review = load_reference_review(root, model_id, version_id)
    publication_status, is_current = _release_status(root, model_id, version_id)
    result = {
        "model_id": model_id,
        "display_name": asset["display_name"],
        "version_id": version_id,
        "source_kind": "scanned_reference",
        "quality_status": manifest["quality_status"],
        "review_status": _review_status(review),
        "publication_status": publication_status,
        "index_status": _index_status(root, model_id, version_id, current=is_current),
    }
    result["risk_summary"] = _risk_summary(**{
        key: result[key] for key in ("quality_status", "review_status", "index_status")
    })
    if visibility == "professional":
        bundle = load_bundle(root, model_id, version_id)
        points = select_reference_points(bundle["normalized"]["points"], point_count=4096, random_seed=0)
        result.update({
            "dimensions_m": bundle["normalized"]["dimensions_m"],
            "preview": {
                "schema_version": "1.0", "coordinate_unit": "m", "algorithm": "sha256_point_subset_v1",
                "random_seed": 0, "source_point_count": bundle["normalized"]["point_count"],
                "point_count": len(points), "points": points,
            },
            "source": {
                "format": manifest["source_format"],
                "path": manifest["source_path"],
                "fingerprint": manifest["source_fingerprint"],
                "bytes": manifest["source_bytes"],
            },
            "license": manifest["license"],
            "provenance": manifest["provenance"],
            "quality": bundle["quality"],
            "review": review,
            "manifest_fingerprint": bundle["commit"]["manifest_fingerprint"],
        })
    return result


def _assets(root: Path, model_id: str | None) -> list[dict]:
    if model_id is not None:
        asset = load_model_asset(root, model_id)
        if asset.get("source_family") != "scanned_reference":
            raise ModelMatchingError("reference_catalog_not_found", "Scanned reference asset does not exist.")
        return [asset]
    return [asset for asset in list_model_assets(root) if asset.get("source_family") == "scanned_reference"]


def list_reference_catalog(root, *, principal: Principal, status: str | None = None, cursor: str | None = None,
                           limit: int = 50, model_id: str | None = None) -> dict:
    """读取扫描目录，不产生核验、发布、索引或审计副作用。"""
    if type(status) is not type(None) and (type(status) is not str or status not in _STATUSES):
        raise _invalid("Reference catalog status filter is invalid.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _invalid("Reference catalog page limit must be an integer from 1 to 100.")
    visibility = _visibility(principal)
    normalized_status = "all" if status in {None, "all"} else status
    filters = _cursor_filters(model_id=model_id, status=normalized_status, visibility=visibility)
    after = _decode_cursor(cursor, filters=filters)
    root = Path(root)
    rows = []
    for asset in _assets(root, model_id):
        for manifest in list_reference_versions(root, asset["model_id"]):
            row = _projection(root, asset, manifest, visibility=visibility)
            if _matches_status(row, normalized_status):
                rows.append(row)
    rows.sort(key=lambda item: (item["model_id"], item["version_id"]))
    if after is not None:
        rows = [item for item in rows if (item["model_id"], item["version_id"]) > after]
    page = rows[:limit]
    return {"viewer_role": _viewer_role(principal), "items": page,
            "next_cursor": _encode_cursor(filters=filters, item=page[-1]) if len(rows) > limit else None}


def load_reference_catalog_version(root, model_id: str, version_id: str, *, principal: Principal) -> dict:
    visibility = _visibility(principal)
    asset = _assets(Path(root), model_id)[0]
    manifest = load_bundle(root, model_id, version_id)["manifest"]
    return {"viewer_role": _viewer_role(principal),
            **_projection(Path(root), asset, manifest, visibility=visibility)}


def crop_reference_catalog_entry(value: dict) -> dict:
    """用于旧公开模型端点的扫描版本安全摘要。"""
    return {key: value[key] for key in _BUSINESS_FIELDS}
