from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


FieldKind = Literal["identifier", "text", "term_list"]
_FIELD_KINDS = frozenset({"identifier", "text", "term_list"})
_REQUEST_ERRORS = frozenset(
    {"invalid_mapping", "missing_field", "unknown_field"}
)


def _is_nonempty_ascii(value: object) -> bool:
    return type(value) is str and bool(value) and value.isascii()


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind

    def __post_init__(self) -> None:
        if not _is_nonempty_ascii(self.name):
            raise ValueError(
                "FieldSpec name must be a non-empty ASCII string."
            )
        if type(self.kind) is not str or self.kind not in _FIELD_KINDS:
            raise ValueError("FieldSpec kind is unsupported.")


@dataclass(frozen=True)
class FreezeLimits:
    max_depth: int = 16
    max_nodes: int = 4096
    max_text_bytes: int = 1_048_576
    max_collection_items: int = 4096

    def __post_init__(self) -> None:
        values = (
            self.max_depth,
            self.max_nodes,
            self.max_text_bytes,
            self.max_collection_items,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError(
                "Freeze limits must be non-negative integers."
            )


@dataclass(frozen=True)
class RequestSchema:
    schema_id: str
    schema_version: str
    fields: tuple[FieldSpec, ...]

    def __post_init__(self) -> None:
        if not _is_nonempty_ascii(self.schema_id):
            raise ValueError(
                "Request schema ID must be a non-empty ASCII string."
            )
        if not _is_nonempty_ascii(self.schema_version):
            raise ValueError(
                "Request schema version must be a non-empty ASCII string."
            )
        if type(self.fields) is not tuple or any(
            type(field) is not FieldSpec for field in self.fields
        ):
            raise ValueError(
                "Request schema fields must be a tuple of FieldSpec values."
            )
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("Request schema field names must be unique.")


class FrozenRequestValueError(ValueError):
    def __init__(self, field_name: str, reason: str):
        super().__init__(f"{field_name}: {reason}")
        self.field_name = field_name
        self.reason = reason


@dataclass(frozen=True)
class _MetadataRecord:
    status: str
    text_hex: str | None = None
    error_type: _TypeIdentityRecord | None = None

    def to_payload(self) -> dict:
        payload = {"status": self.status}
        if self.text_hex is not None:
            payload["text_hex"] = self.text_hex
        if self.error_type is not None:
            payload["error_type"] = self.error_type.to_payload()
        return payload


@dataclass(frozen=True)
class _TypeIdentityRecord:
    module: _MetadataRecord
    qualname: _MetadataRecord

    def to_payload(self) -> dict:
        return {
            "module": self.module.to_payload(),
            "qualname": self.qualname.to_payload(),
        }


@dataclass(frozen=True)
class _CaptureRecord:
    status: str
    text: str | None = None
    terms: tuple[str, ...] | None = None
    text_hex: str | None = None
    term_hexes: tuple[str, ...] | None = None

    def to_payload(self) -> dict:
        payload = {"status": self.status}
        if self.text_hex is not None:
            payload["text_utf8_surrogatepass_hex"] = self.text_hex
        if self.term_hexes is not None:
            payload["items"] = [
                {"text_utf8_surrogatepass_hex": text_hex}
                for text_hex in self.term_hexes
            ]
        return payload


@dataclass(frozen=True)
class _FrozenField:
    name: str
    kind: FieldKind
    raw_type: _TypeIdentityRecord | None
    capture: _CaptureRecord

    def raw_type_payload(self) -> dict:
        if self.raw_type is None:
            return {
                "module": {"status": "unavailable"},
                "qualname": {"status": "unavailable"},
            }
        return self.raw_type.to_payload()


@dataclass
class _CaptureBudget:
    limits: FreezeLimits
    nodes: int = 0
    text_bytes: int = 0
    text_limit_exceeded: bool = False

    def consume_node(self, depth: int) -> bool:
        self.nodes += 1
        return (
            depth <= self.limits.max_depth
            and self.nodes <= self.limits.max_nodes
        )

    def consume_text(self, encoded: bytes) -> bool:
        next_total = self.text_bytes + len(encoded)
        self.text_bytes = next_total
        if next_total > self.limits.max_text_bytes:
            self.text_limit_exceeded = True
            return False
        return True

    def allows_text_lower_bound(self, character_count: int) -> bool:
        remaining = self.limits.max_text_bytes - self.text_bytes
        if character_count <= remaining:
            return True
        self.text_limit_exceeded = True
        return False


@dataclass(frozen=True, init=False)
class FrozenRequest:
    _schema_id: str
    _schema_version: str
    _request_errors: tuple[str, ...]
    _fields: tuple[_FrozenField, ...]

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise ValueError(
            "FrozenRequest cannot be constructed directly."
        )

    def __post_init__(self) -> None:
        if not _is_nonempty_ascii(self._schema_id):
            raise ValueError(
                "Frozen request schema ID must be non-empty ASCII."
            )
        if not _is_nonempty_ascii(self._schema_version):
            raise ValueError(
                "Frozen request schema version must be non-empty ASCII."
            )
        if type(self._request_errors) is not tuple or any(
            type(reason) is not str or reason not in _REQUEST_ERRORS
            for reason in self._request_errors
        ):
            raise ValueError(
                "Frozen request errors must be a tuple of safe reasons."
            )
        if type(self._fields) is not tuple or any(
            type(field) is not _FrozenField for field in self._fields
        ):
            raise ValueError(
                "Frozen request fields must be a tuple of frozen fields."
            )

    def to_audit_payload(self) -> dict:
        return {
            "canonicalizer_schema": "1.0",
            "request_schema_id": self._schema_id,
            "request_schema_version": self._schema_version,
            "request_errors": list(self._request_errors),
            "fields": [
                {
                    "name": field.name,
                    "kind": field.kind,
                    "raw_type": field.raw_type_payload(),
                    "capture": field.capture.to_payload(),
                }
                for field in self._fields
            ],
        }

    def require_identifier_text(self, field_name: str) -> str:
        capture = self._require_capture(field_name, "identifier")
        if capture.text is None:
            raise FrozenRequestValueError(field_name, "invalid_type")
        return capture.text

    def require_text(self, field_name: str) -> str:
        capture = self._require_capture(field_name, "text")
        if capture.text is None:
            raise FrozenRequestValueError(field_name, "conversion_error")
        return capture.text

    def require_term_texts(self, field_name: str) -> tuple[str, ...]:
        capture = self._require_capture(field_name, "term_list")
        if capture.terms is None:
            raise FrozenRequestValueError(field_name, "conversion_error")
        return capture.terms

    def _require_capture(
        self, field_name: str, expected_kind: FieldKind
    ) -> _CaptureRecord:
        safe_field_name = (
            field_name if type(field_name) is str else "<invalid-field>"
        )
        if self._request_errors:
            raise FrozenRequestValueError(
                safe_field_name, self._request_errors[0]
            )
        if type(field_name) is not str:
            raise FrozenRequestValueError(
                safe_field_name, "unknown_field"
            )
        field = next(
            (item for item in self._fields if item.name == field_name),
            None,
        )
        if field is None:
            raise FrozenRequestValueError(field_name, "unknown_field")
        if field.kind != expected_kind:
            raise FrozenRequestValueError(
                field_name, "invalid_field_kind"
            )
        if field.capture.status != "ok":
            raise FrozenRequestValueError(
                field_name, field.capture.status
            )
        return field.capture


def _new_frozen_request(
    *,
    schema_id: str,
    schema_version: str,
    request_errors: tuple[str, ...],
    fields: tuple[_FrozenField, ...],
) -> FrozenRequest:
    frozen = object.__new__(FrozenRequest)
    object.__setattr__(frozen, "_schema_id", schema_id)
    object.__setattr__(frozen, "_schema_version", schema_version)
    object.__setattr__(frozen, "_request_errors", request_errors)
    object.__setattr__(frozen, "_fields", fields)
    frozen.__post_init__()
    return frozen


def _leaf_metadata(
    owner: object, name: str, budget: _CaptureBudget
) -> _MetadataRecord:
    try:
        raw = getattr(owner, name)
    except Exception:
        return _MetadataRecord(status="error")
    if type(raw) is not str:
        return _MetadataRecord(status="error")
    captured = _capture_encoded_text(raw, budget)
    if captured is None:
        if budget.text_limit_exceeded:
            return _MetadataRecord(status="limit_exceeded")
        return _MetadataRecord(status="error")
    _, text_hex = captured
    return _MetadataRecord(status="ok", text_hex=text_hex)


def _exception_type_identity(
    value: object, budget: _CaptureBudget
) -> _TypeIdentityRecord:
    value_type = type(value)
    return _TypeIdentityRecord(
        module=_leaf_metadata(value_type, "__module__", budget),
        qualname=_leaf_metadata(value_type, "__qualname__", budget),
    )


def _metadata(
    owner: object, name: str, budget: _CaptureBudget
) -> _MetadataRecord:
    try:
        raw = getattr(owner, name)
    except Exception as exc:
        return _MetadataRecord(
            status="error",
            error_type=_exception_type_identity(exc, budget),
        )
    if type(raw) is not str:
        return _MetadataRecord(status="error")
    captured = _capture_encoded_text(raw, budget)
    if captured is None:
        if budget.text_limit_exceeded:
            return _MetadataRecord(status="limit_exceeded")
        return _MetadataRecord(status="error")
    _, text_hex = captured
    return _MetadataRecord(status="ok", text_hex=text_hex)


def _type_identity(
    value: object, budget: _CaptureBudget
) -> _TypeIdentityRecord:
    value_type = type(value)
    return _TypeIdentityRecord(
        module=_metadata(value_type, "__module__", budget),
        qualname=_metadata(value_type, "__qualname__", budget),
    )


def _capture_encoded_text(
    text: str, budget: _CaptureBudget
) -> tuple[str, str] | None:
    if type(text) is not str:
        return None
    if not budget.allows_text_lower_bound(len(text)):
        return None
    try:
        encoded = str.encode(text, "utf-8", "surrogatepass")
    except Exception:
        return None
    if not budget.consume_text(encoded):
        return None
    try:
        text_hex = bytes.hex(encoded)
    except Exception:
        return None
    return text, text_hex


def _exact_builtin_text(captured: str) -> str | None:
    try:
        exact_text = str.__str__(captured)
    except Exception:
        return None
    return exact_text if type(exact_text) is str else None


def _capture_scalar_text(
    value: object, budget: _CaptureBudget
) -> _CaptureRecord:
    try:
        captured = str(value)
    except Exception:
        return _CaptureRecord(status="conversion_error")
    text = _exact_builtin_text(captured)
    if text is None:
        return _CaptureRecord(status="conversion_error")
    captured = _capture_encoded_text(text, budget)
    if captured is None:
        return _CaptureRecord(status="limit_exceeded")
    business_text, text_hex = captured
    return _CaptureRecord(
        status="ok", text=business_text, text_hex=text_hex
    )


def _capture_identifier(
    value: object, budget: _CaptureBudget
) -> _CaptureRecord:
    if type(value) is not str:
        return _CaptureRecord(status="invalid_type")
    captured = _capture_encoded_text(value, budget)
    if captured is None:
        return _CaptureRecord(status="limit_exceeded")
    business_text, text_hex = captured
    return _CaptureRecord(
        status="ok", text=business_text, text_hex=text_hex
    )


def _capture_terms(
    value: object, budget: _CaptureBudget
) -> _CaptureRecord:
    if type(value) is not list:
        return _CaptureRecord(status="invalid_type")
    try:
        item_count = len(value)
    except Exception:
        return _CaptureRecord(status="conversion_error")
    if item_count > budget.limits.max_collection_items:
        return _CaptureRecord(status="limit_exceeded")
    try:
        copied_items = value[:]
    except Exception:
        return _CaptureRecord(status="conversion_error")

    terms: list[str] = []
    term_hexes: list[str] = []
    for item in copied_items:
        if not budget.consume_node(depth=2):
            return _CaptureRecord(status="limit_exceeded")
        try:
            captured = str(item)
        except Exception:
            return _CaptureRecord(status="conversion_error")
        text = _exact_builtin_text(captured)
        if text is None:
            return _CaptureRecord(status="conversion_error")
        captured = _capture_encoded_text(text, budget)
        if captured is None:
            return _CaptureRecord(status="limit_exceeded")
        business_text, text_hex = captured
        terms.append(business_text)
        term_hexes.append(text_hex)
    return _CaptureRecord(
        status="ok",
        terms=tuple(terms),
        term_hexes=tuple(term_hexes),
    )


def _freeze_field(
    spec: FieldSpec, value: object, budget: _CaptureBudget
) -> _FrozenField:
    if not budget.consume_node(depth=1):
        return _FrozenField(
            name=spec.name,
            kind=spec.kind,
            raw_type=None,
            capture=_CaptureRecord(status="limit_exceeded"),
        )
    raw_type = _type_identity(value, budget)
    if budget.text_limit_exceeded:
        capture = _CaptureRecord(status="limit_exceeded")
    elif spec.kind == "identifier":
        capture = _capture_identifier(value, budget)
    elif spec.kind == "text":
        capture = _capture_scalar_text(value, budget)
    else:
        capture = _capture_terms(value, budget)
    return _FrozenField(
        name=spec.name,
        kind=spec.kind,
        raw_type=raw_type,
        capture=capture,
    )


def _freeze_missing_field(
    spec: FieldSpec, budget: _CaptureBudget
) -> _FrozenField:
    within_budget = budget.consume_node(depth=1)
    return _FrozenField(
        name=spec.name,
        kind=spec.kind,
        raw_type=None,
        capture=_CaptureRecord(
            status="missing_field" if within_budget else "limit_exceeded"
        ),
    )


def _invalid_mapping_request(schema: RequestSchema) -> FrozenRequest:
    return _new_frozen_request(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        request_errors=("invalid_mapping",),
        fields=(),
    )


def freeze_request(
    schema: RequestSchema,
    values: Mapping[str, object],
    *,
    limits: FreezeLimits = FreezeLimits(),
) -> FrozenRequest:
    if type(schema) is not RequestSchema:
        raise TypeError("schema must be an exact RequestSchema.")
    if type(limits) is not FreezeLimits:
        raise TypeError("limits must be an exact FreezeLimits.")
    if type(values) is not dict:
        return _invalid_mapping_request(schema)

    supplied: dict[str, object] = {}
    unknown_field = False
    schema_names = tuple(field.name for field in schema.fields)
    try:
        for key, value in dict.items(values):
            if type(key) is not str or key not in schema_names:
                unknown_field = True
            else:
                supplied[key] = value
    except Exception:
        return _invalid_mapping_request(schema)

    missing_field = any(
        field.name not in supplied for field in schema.fields
    )
    request_errors = tuple(
        reason
        for reason, present in (
            ("unknown_field", unknown_field),
            ("missing_field", missing_field),
        )
        if present
    )
    budget = _CaptureBudget(limits)
    frozen_fields = tuple(
        _freeze_missing_field(field, budget)
        if field.name not in supplied
        else _freeze_field(field, supplied[field.name], budget)
        for field in schema.fields
    )
    return _new_frozen_request(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        request_errors=request_errors,
        fields=frozen_fields,
    )


__all__ = [
    "FieldSpec",
    "FreezeLimits",
    "FrozenRequest",
    "FrozenRequestValueError",
    "RequestSchema",
    "freeze_request",
]
