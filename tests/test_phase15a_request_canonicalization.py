import json

import pytest

from pc_system.request_canonicalization import (
    FieldSpec,
    FreezeLimits,
    FrozenRequest,
    FrozenRequestValueError,
    RequestSchema,
    _CaptureRecord,
    _FrozenField,
    freeze_request,
)


SCHEMA = RequestSchema(
    schema_id="model_asset.create",
    schema_version="1.0",
    fields=(
        FieldSpec("model_id", "identifier"),
        FieldSpec("display_name", "text"),
        FieldSpec("keywords", "term_list"),
    ),
)


class StatefulText:
    def __init__(self, *values):
        self.values = values
        self.calls = 0

    def __str__(self):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


class ExplodingClass:
    @property
    def __class__(self):
        raise RuntimeError("class access exploded")

    def __str__(self):
        return "Pump A"


def request_values(display_name="Pump A", keywords=None):
    return {
        "model_id": "pump-a",
        "display_name": display_name,
        "keywords": ["pump"] if keywords is None else keywords,
    }


def test_freeze_captures_business_text_once():
    value = StatefulText("Pump A", "Pump B")

    frozen = freeze_request(SCHEMA, request_values(display_name=value))

    assert value.calls == 1
    assert frozen.require_text("display_name") == "Pump A"
    assert frozen.require_text("display_name") == "Pump A"
    assert value.calls == 1


def test_freeze_never_reads_instance_class_property():
    value = ExplodingClass()

    frozen = freeze_request(SCHEMA, request_values(display_name=value))

    assert frozen.require_text("display_name") == "Pump A"


def test_audit_payload_is_ascii_json_and_defensive_copy():
    value_type = type("SurrogateType", (), {"__str__": lambda self: "Pump A"})
    value_type.__module__ = "\ud800"
    frozen = freeze_request(
        SCHEMA, request_values(display_name=value_type())
    )

    first = frozen.to_audit_payload()
    encoded = json.dumps(
        first, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    first["fields"].clear()

    assert encoded
    assert frozen.require_text("display_name") == "Pump A"
    assert frozen.to_audit_payload()["fields"]


class ExplodingText:
    def __str__(self):
        raise RuntimeError("text conversion exploded")


class ExplodingList(list):
    def __iter__(self):
        raise RuntimeError("list iteration exploded")


class ForgedEncodingText(str):
    def encode(self, *args, **kwargs):
        return b"forged"

    def __str__(self):
        return self


class ForgedEncodingValue:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __str__(self):
        self.calls += 1
        return ForgedEncodingText(self.value)


def test_scalar_text_becomes_exact_string_before_audit_encoding():
    first_value = ForgedEncodingValue("Pump A")
    second_value = ForgedEncodingValue("Pump B")

    first = freeze_request(
        SCHEMA, request_values(display_name=first_value)
    )
    second = freeze_request(
        SCHEMA, request_values(display_name=second_value)
    )
    first_text = first.require_text("display_name")
    second_text = second.require_text("display_name")

    assert first_value.calls == 1
    assert second_value.calls == 1
    assert type(first_text) is str
    assert type(second_text) is str
    assert first_text == "Pump A"
    assert second_text == "Pump B"
    assert first.to_audit_payload() != second.to_audit_payload()


def test_term_text_becomes_exact_string_before_audit_encoding():
    first_value = ForgedEncodingValue("Pump")
    second_value = ForgedEncodingValue("Valve")

    first = freeze_request(
        SCHEMA, request_values(keywords=[first_value])
    )
    second = freeze_request(
        SCHEMA, request_values(keywords=[second_value])
    )
    first_terms = first.require_term_texts("keywords")
    second_terms = second.require_term_texts("keywords")

    assert first_value.calls == 1
    assert second_value.calls == 1
    assert type(first_terms[0]) is str
    assert type(second_terms[0]) is str
    assert first_terms == ("Pump",)
    assert second_terms == ("Valve",)
    assert first.to_audit_payload() != second.to_audit_payload()


def test_identifier_requires_exact_string_after_freeze():
    frozen = freeze_request(
        SCHEMA,
        request_values() | {"model_id": StatefulText("pump-a")},
    )

    with pytest.raises(FrozenRequestValueError) as exc_info:
        frozen.require_identifier_text("model_id")

    assert exc_info.value.field_name == "model_id"
    assert exc_info.value.reason == "invalid_type"


def test_conversion_failure_is_captured_not_raised():
    frozen = freeze_request(
        SCHEMA, request_values(display_name=ExplodingText())
    )

    with pytest.raises(FrozenRequestValueError) as exc_info:
        frozen.require_text("display_name")

    assert exc_info.value.reason == "conversion_error"


def test_exact_list_is_frozen_and_original_mutation_is_ignored():
    terms = [StatefulText("Pump", "Changed")]
    frozen = freeze_request(SCHEMA, request_values(keywords=terms))
    terms.clear()

    assert frozen.require_term_texts("keywords") == ("Pump",)


def test_list_subclass_is_rejected_without_iteration():
    frozen = freeze_request(
        SCHEMA, request_values(keywords=ExplodingList(["pump"]))
    )

    with pytest.raises(FrozenRequestValueError) as exc_info:
        frozen.require_term_texts("keywords")

    assert exc_info.value.reason == "invalid_type"


@pytest.mark.parametrize(
    ("limits", "accessor", "field_name"),
    [
        (FreezeLimits(max_depth=0), "text", "display_name"),
        (FreezeLimits(max_nodes=1), "text", "display_name"),
        (FreezeLimits(max_text_bytes=2), "text", "display_name"),
        (FreezeLimits(max_collection_items=0), "terms", "keywords"),
    ],
)
def test_resource_limits_become_frozen_errors(
    limits, accessor, field_name
):
    frozen = freeze_request(SCHEMA, request_values(), limits=limits)

    with pytest.raises(FrozenRequestValueError) as exc_info:
        if accessor == "text":
            frozen.require_text(field_name)
        else:
            frozen.require_term_texts(field_name)

    assert exc_info.value.reason == "limit_exceeded"


def test_missing_and_unknown_fields_are_rejected_after_freeze():
    missing = freeze_request(
        SCHEMA, {"model_id": "pump-a", "display_name": "Pump A"}
    )
    unknown = freeze_request(
        SCHEMA, request_values() | {"unexpected": "value"}
    )

    with pytest.raises(FrozenRequestValueError) as missing_error:
        missing.require_term_texts("keywords")
    with pytest.raises(FrozenRequestValueError) as unknown_error:
        unknown.require_text("display_name")

    assert missing_error.value.reason == "missing_field"
    assert unknown_error.value.reason == "unknown_field"


def test_missing_field_still_consumes_field_node_budget():
    frozen = freeze_request(
        SCHEMA,
        {"display_name": "Pump A", "keywords": []},
        limits=FreezeLimits(max_nodes=1),
    )

    fields = {
        field["name"]: field for field in frozen.to_audit_payload()["fields"]
    }

    assert fields["display_name"]["capture"]["status"] == "limit_exceeded"


@pytest.mark.parametrize("invalid", ["", "mödels", "\ud800"])
def test_schema_metadata_requires_nonempty_ascii(invalid):
    with pytest.raises(ValueError):
        RequestSchema(invalid, "1.0", ())
    with pytest.raises(ValueError):
        RequestSchema("schema", invalid, ())
    with pytest.raises(ValueError):
        FieldSpec(invalid, "text")


@pytest.mark.parametrize(
    ("schema_id", "schema_version", "request_errors", "fields"),
    [
        ("\ud800", "1.0", (), ()),
        ("schema", "", (), ()),
        ("schema", "1.0", [], ()),
        ("schema", "1.0", ("\ud800",), ()),
        ("schema", "1.0", (), []),
        ("schema", "1.0", (), ("not-a-frozen-field",)),
    ],
)
def test_frozen_request_constructor_rejects_unsafe_state(
    schema_id, schema_version, request_errors, fields
):
    with pytest.raises(ValueError):
        FrozenRequest(
            schema_id,
            schema_version,
            request_errors,
            fields,
        )


def test_frozen_request_constructor_rejects_nested_mutable_state():
    terms = ["Pump"]
    term_hexes = ["50756d70"]
    capture = _CaptureRecord(
        status="ok",
        terms=terms,
        term_hexes=term_hexes,
    )
    field = _FrozenField(
        name="keywords",
        kind="term_list",
        raw_type=None,
        capture=capture,
    )

    with pytest.raises(
        ValueError, match="cannot be constructed directly"
    ):
        FrozenRequest("schema", "1.0", (), (field,))


class ExplodingMetadata(type):
    def __getattribute__(cls, name):
        if name in {"__module__", "__qualname__"}:
            raise MetadataExplosion("metadata exploded")
        return super().__getattribute__(name)


class MetadataExplosion(Exception, metaclass=ExplodingMetadata):
    pass


class MetadataTarget(metaclass=ExplodingMetadata):
    def __str__(self):
        return "Pump A"


_METADATA_RETURN_VALUE = None


class ReturningMetadata(type):
    def __getattribute__(cls, name):
        if name == "__module__":
            return _METADATA_RETURN_VALUE
        return super().__getattribute__(name)


class MetadataReturnsRequestValue(metaclass=ReturningMetadata):
    def __init__(self):
        self.calls = 0

    def __str__(self):
        self.calls += 1
        return "Pump A"


def test_metadata_never_converts_non_exact_string_value():
    global _METADATA_RETURN_VALUE
    value = MetadataReturnsRequestValue()
    _METADATA_RETURN_VALUE = value
    try:
        frozen = freeze_request(
            SCHEMA, request_values(display_name=value)
        )
    finally:
        _METADATA_RETURN_VALUE = None

    payload = frozen.to_audit_payload()
    display_name = next(
        field for field in payload["fields"] if field["name"] == "display_name"
    )

    assert value.calls == 1
    assert display_name["raw_type"]["module"]["status"] == "error"
    assert frozen.require_text("display_name") == "Pump A"


def test_type_metadata_failure_is_total_and_non_recursive():
    frozen = freeze_request(
        SCHEMA, request_values(display_name=MetadataTarget())
    )

    payload = frozen.to_audit_payload()
    display_name = next(
        field for field in payload["fields"] if field["name"] == "display_name"
    )
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")

    assert encoded
    assert display_name["raw_type"]["module"]["status"] == "error"
    assert frozen.require_text("display_name") == "Pump A"


def test_oversized_exact_type_metadata_exhausts_shared_text_budget():
    value_type = type(
        "BudgetedMetadata",
        (),
        {"__str__": lambda self: "Pump A"},
    )
    value_type.__module__ = "m" * 10_000

    frozen = freeze_request(
        SCHEMA,
        request_values(display_name=value_type()),
        limits=FreezeLimits(max_text_bytes=64),
    )

    payload = frozen.to_audit_payload()
    display_name = next(
        field for field in payload["fields"] if field["name"] == "display_name"
    )
    serialized = json.dumps(payload, ensure_ascii=True)
    with pytest.raises(FrozenRequestValueError) as exc_info:
        frozen.require_text("display_name")

    assert exc_info.value.reason == "limit_exceeded"
    assert display_name["raw_type"]["module"] == {
        "status": "limit_exceeded"
    }
    assert len(serialized) < 2_000
    assert "m" * 100 not in serialized


class ExplodingMapping(dict):
    def __iter__(self):
        raise RuntimeError("mapping iteration exploded")


def test_mapping_subclass_is_rejected_without_iteration():
    frozen = freeze_request(SCHEMA, ExplodingMapping(request_values()))

    with pytest.raises(FrozenRequestValueError) as exc_info:
        frozen.require_text("display_name")

    assert exc_info.value.reason == "invalid_mapping"
