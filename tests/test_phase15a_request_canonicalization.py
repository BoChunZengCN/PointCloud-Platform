import json

import pytest

from pc_system.request_canonicalization import (
    FieldSpec,
    FreezeLimits,
    FrozenRequestValueError,
    RequestSchema,
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


class ExplodingMapping(dict):
    def __iter__(self):
        raise RuntimeError("mapping iteration exploded")


def test_mapping_subclass_is_rejected_without_iteration():
    frozen = freeze_request(SCHEMA, ExplodingMapping(request_values()))

    with pytest.raises(FrozenRequestValueError) as exc_info:
        frozen.require_text("display_name")

    assert exc_info.value.reason == "invalid_mapping"
