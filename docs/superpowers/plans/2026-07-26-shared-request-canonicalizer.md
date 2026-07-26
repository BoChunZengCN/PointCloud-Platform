# Shared Request Canonicalizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Task 3's local request snapshot with a shared one-time request freezer so audit fingerprints and model-library business validation always consume the same captured values.

**Architecture:** A new schema-driven `request_canonicalization` module freezes exact identifiers, scalar text, and exact-list term values into immutable records. It emits a fresh ASCII-only audit payload for Task 2 and exposes captured business values after audit start, without touching the original request twice. `model_library` defines its request schema, starts the audit from the frozen payload, and performs all authorization and validation against the frozen request.

**Tech Stack:** Python 3.11 standard library, frozen dataclasses, pytest, existing Phase 15 Task 1 identity/errors and Task 2 audit operations.

## Global Constraints

- The approved design is `docs/superpowers/specs/2026-07-26-shared-request-canonicalizer-design.md`.
- Validate only path-forming `operation_id` before `start_operation`; authorization and business validity decisions remain inside the audit lifecycle.
- Do not modify `model_matching_audit.py`, `model_matching_identity.py`, or `model_matching_errors.py`.
- Do not implement Phase 15A Task 4 or later work.
- Do not add third-party dependencies.
- Catch ordinary request-conversion `Exception` values, but do not swallow `KeyboardInterrupt` or `SystemExit`.
- Default limits are depth `16`, nodes `4096`, aggregate text bytes `1_048_576`, and collection items `4096`.
- All dynamic audit text uses UTF-8 `surrogatepass` hexadecimal ASCII.
- A successful business value is always the value captured before audit start; production code never converts or iterates the original value again.
- Existing immutable hard-link publication, replay-failure audit, and audit lifecycle behavior must remain unchanged.

---

### Task 1: Shared Immutable Request Freezer

**Files:**
- Create: `src/pc_system/request_canonicalization.py`
- Create: `tests/test_phase15a_request_canonicalization.py`

**Interfaces:**
- Consumes: Python request values and the exact limits in Global Constraints.
- Produces: `FieldSpec`, `FreezeLimits`, `RequestSchema`, `FrozenRequest`, `FrozenRequestValueError`, and `freeze_request`.

- [ ] **Step 1: Write failing public-interface and one-time-capture tests**

Create `tests/test_phase15a_request_canonicalization.py` with these fixtures and tests:

```python
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
```

- [ ] **Step 2: Run the interface tests and verify RED**

Run:

```powershell
& 'D:\01_Codex Project\point-cloud-system\.venv\Scripts\python.exe' -m pytest tests/test_phase15a_request_canonicalization.py -v --basetemp .p15a-canon-t1-red
```

Expected: collection fails with `ModuleNotFoundError: No module named 'pc_system.request_canonicalization'`.

- [ ] **Step 3: Add failing type, mutation, and limit tests**

Append:

```python
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
```

- [ ] **Step 4: Implement immutable schema and capture records**

Create `src/pc_system/request_canonicalization.py`.

Use frozen dataclasses for public schema/limits and private capture records.
Validate developer-supplied schema values in `__post_init__`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


FieldKind = Literal["identifier", "text", "term_list"]
_FIELD_KINDS = frozenset({"identifier", "text", "term_list"})


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise ValueError("FieldSpec name must be a non-empty string.")
        if self.kind not in _FIELD_KINDS:
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
            raise ValueError("Freeze limits must be non-negative integers.")


@dataclass(frozen=True)
class RequestSchema:
    schema_id: str
    schema_version: str
    fields: tuple[FieldSpec, ...]

    def __post_init__(self) -> None:
        if type(self.schema_id) is not str or not self.schema_id:
            raise ValueError("Request schema ID must be non-empty.")
        if type(self.schema_version) is not str or not self.schema_version:
            raise ValueError("Request schema version must be non-empty.")
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("Request schema field names must be unique.")


class FrozenRequestValueError(ValueError):
    def __init__(self, field_name: str, reason: str):
        super().__init__(f"{field_name}: {reason}")
        self.field_name = field_name
        self.reason = reason
```

Implement private capture records so each field stores both its immutable
business value and its audit representation. No private record may store the
original object.

- [ ] **Step 5: Implement guarded one-time capture**

Implement these exact rules:

```python
def _text_hex(value: str) -> str:
    return value.encode("utf-8", "surrogatepass").hex()


def _metadata(owner: object, name: str) -> dict:
    try:
        raw = getattr(owner, name)
        text = str(raw)
    except Exception as exc:
        return {
            "status": "error",
            "error_type": _type_identity(exc),
        }
    return {"status": "ok", "text_hex": _text_hex(text)}


def _type_identity(value: object) -> dict:
    value_type = type(value)
    return {
        "module": _metadata(value_type, "__module__"),
        "qualname": _metadata(value_type, "__qualname__"),
    }
```

Capture rules:

- increment the node budget before each field or term item;
- exact `str` identifiers capture their existing value without conversion;
- scalar text calls `str(value)` once inside `try/except Exception`;
- term lists require `type(value) is list`, check item count before copying,
  copy with `value[:]`, then call `str(item)` once per copied item;
- calculate text bytes with UTF-8 `surrogatepass`;
- when any budget is exceeded, freeze `limit_exceeded`;
- never call `isinstance(value, ...)` on request values;
- never store `value`, a list item, or another original request object.

`freeze_request` requires `type(values) is dict`. A different mapping type
freezes a request-level `invalid_mapping` error without iterating it.
Unknown keys and missing schema fields become request errors.

- [ ] **Step 6: Implement immutable access and audit payload generation**

`FrozenRequest` stores only tuples and frozen private records.

Accessors:

- first reject a request-level error;
- verify the requested field exists and matches the accessor kind;
- raise `FrozenRequestValueError(field_name, capture_status)` unless capture
  status is `ok`;
- return the stored `str` or `tuple[str, ...]` without conversion.

`to_audit_payload()` constructs a new dictionary from immutable records on
every call:

```python
{
    "canonicalizer_schema": "1.0",
    "request_schema_id": schema.schema_id,
    "request_schema_version": schema.schema_version,
    "request_errors": list(self._request_errors),
    "fields": [
        {
            "name": field.name,
            "kind": field.kind,
            "raw_type": field.raw_type_payload,
            "capture": field.capture_payload,
        }
        for field in frozen_fields
    ],
}
```

The returned structure contains only dictionaries, lists, integers, booleans,
`None`, and fixed ASCII or hexadecimal strings.

- [ ] **Step 7: Run Task 1 tests and verify GREEN**

Run:

```powershell
& 'D:\01_Codex Project\point-cloud-system\.venv\Scripts\python.exe' -m pytest tests/test_phase15a_request_canonicalization.py -v --basetemp .p15a-canon-t1-green
```

Expected: all canonicalizer tests pass.

- [ ] **Step 8: Run static checks and commit Task 1**

Run:

```powershell
& 'D:\01_Codex Project\point-cloud-system\.venv\Scripts\python.exe' -m py_compile src/pc_system/request_canonicalization.py tests/test_phase15a_request_canonicalization.py
git diff --check
git add src/pc_system/request_canonicalization.py tests/test_phase15a_request_canonicalization.py
git commit -m "feat: add shared request canonicalizer"
```

Expected: both checks exit `0`, and the commit contains exactly the two Task 1
files.

---

### Task 2: Integrate Frozen Requests into Model Asset Creation

**Files:**
- Modify: `src/pc_system/model_library.py`
- Modify: `tests/test_phase15a_model_library.py`
- Modify: `docs/superpowers/plans/2026-07-22-phase15a-cad-model-library-audit.md`

**Interfaces:**
- Consumes: Task 1 `freeze_request`, `FrozenRequestValueError`, `FieldSpec`, and `RequestSchema`.
- Produces: `create_model_asset` whose audit fingerprint and business values come from one `FrozenRequest`; the existing model-library public API is unchanged.

- [ ] **Step 1: Write the two blocking regression tests**

Add:

```python
class _StatefulDisplayName:
    def __init__(self, first, second):
        self.values = (first, second)
        self.calls = 0

    def __str__(self):
        value = self.values[min(self.calls, 1)]
        self.calls += 1
        return value


class _ExplodingClassDisplayName:
    @property
    def __class__(self):
        raise RuntimeError("class access exploded")

    def __str__(self):
        return "Pump A"


def test_model_create_consumes_one_frozen_stateful_text(tmp_path):
    display_name = _StatefulDisplayName("Pump A", "Pump B")

    created = create_pump(tmp_path, display_name=display_name)

    assert created["display_name"] == "Pump A"
    assert display_name.calls == 1


def test_same_snapshot_cannot_hide_different_business_value(tmp_path):
    first = _StatefulDisplayName("shared", "Pump A")
    second = _StatefulDisplayName("shared", "Pump B")
    create_pump(tmp_path, display_name=first)

    replayed = create_pump(
        tmp_path,
        display_name=second,
        operation_id="op-model-replay",
        request_id="request-model-replay",
    )

    assert replayed["display_name"] == "shared"
    assert first.calls == 1
    assert second.calls == 1


def test_exploding_class_property_does_not_escape_before_audit(tmp_path):
    created = create_pump(
        tmp_path, display_name=_ExplodingClassDisplayName()
    )

    assert created["display_name"] == "Pump A"
    assert load_operation(tmp_path, "op-model-001")["status"] == "completed"
```

The second test proves that the business result is the exact captured
fingerprint value. It must not expect `"Pump A"` or `"Pump B"` because neither
is allowed to be read after freezing.

- [ ] **Step 2: Run the regressions and verify RED**

Run:

```powershell
& 'D:\01_Codex Project\point-cloud-system\.venv\Scripts\python.exe' -m pytest tests/test_phase15a_model_library.py -v -k "frozen_stateful or same_snapshot or exploding_class_property" --basetemp .p15a-canon-t2-red
```

Expected: current local snapshot either calls conversion twice or leaks from
request representation inspection, so at least one test fails for the
reviewed root cause.

- [ ] **Step 3: Define the model asset request schema**

Import:

```python
from pc_system.request_canonicalization import (
    FieldSpec,
    FrozenRequestValueError,
    RequestSchema,
    freeze_request,
)
```

Define:

```python
MODEL_ASSET_CREATE_SCHEMA = RequestSchema(
    schema_id="model_asset.create",
    schema_version="1.0",
    fields=(
        FieldSpec("model_id", "identifier"),
        FieldSpec("display_name", "text"),
        FieldSpec("category_id", "identifier"),
        FieldSpec("manufacturer", "text"),
        FieldSpec("model_number", "text"),
        FieldSpec("keywords", "term_list"),
        FieldSpec("tags", "term_list"),
    ),
)
```

- [ ] **Step 4: Replace local snapshot and consume frozen values**

At the beginning of `create_model_asset`, after validating only
`operation_id`, call:

```python
frozen_request = freeze_request(
    MODEL_ASSET_CREATE_SCHEMA,
    {
        "model_id": model_id,
        "display_name": display_name,
        "category_id": category_id,
        "manufacturer": manufacturer,
        "model_number": model_number,
        "keywords": keywords,
        "tags": tags,
    },
)
```

Pass:

```python
request_payload=frozen_request.to_audit_payload()
```

After `start_operation` and expert authorization, replace raw-value
normalization with:

```python
normalized_model_id = validate_identifier(
    frozen_request.require_identifier_text("model_id"), "model_id"
)
normalized_category_id = validate_identifier(
    frozen_request.require_identifier_text("category_id"), "category_id"
)
normalized_display_name = frozen_request.require_text(
    "display_name"
).strip()
if not normalized_display_name:
    raise ValueError("display_name must not be empty.")
normalized_display_name.encode("utf-8")
normalized_manufacturer = frozen_request.require_text(
    "manufacturer"
).strip()
normalized_model_number = frozen_request.require_text(
    "model_number"
).strip()
normalized_keywords = _terms(
    frozen_request.require_term_texts("keywords"), "keywords"
)
normalized_tags = _terms(
    frozen_request.require_term_texts("tags"), "tags"
)
```

Change `_terms` to accept `tuple[str, ...]` and remove every `str(value)` call:

```python
def _terms(values: tuple[str, ...], label: str) -> list[str]:
    normalized = sorted(
        {value.strip().lower() for value in values if value.strip()}
    )
    if any(len(value) > 128 for value in normalized):
        raise ValueError(
            f"{label} entries must not exceed 128 characters."
        )
    return normalized
```

Catch `FrozenRequestValueError` with validation errors and map it to
`ModelMatchingError("invalid_model_asset", str(exc))` after audit start.

- [ ] **Step 5: Delete local request snapshot implementation**

Remove all of these helpers from `model_library.py`:

```text
_text_hex
_metadata_text
_type_identity
_business_string
_path_representation
_snapshot_sort_key
_snapshot_representation
_snapshot_value
_audit_request_snapshot
```

Remove imports used only by those helpers. Keep `os` because immutable asset
publication still uses filesystem operations.

Run:

```powershell
rg -n "_snapshot|_audit_request|_business_string|_path_representation" src/pc_system/model_library.py
```

Expected: no matches.

- [ ] **Step 6: Update legacy edge tests to the approved schema**

Keep scalar text coercion tests for `Path` and unusual objects because text is
captured once.

Change list-subclass expectations: an `_ExplodingList` used for `keywords` or
`tags` must now return audited `invalid_model_asset` without invoking its
custom iterator.

Retain replay-failure audit interruption, no-replace concurrency, manifest
integrity, and listing tests unchanged.

Add an assertion that a failed canonicalization operation ends with:

```python
assert _failure_code(tmp_path, "op-model-001") == "invalid_model_asset"
```

- [ ] **Step 7: Update the Phase 15A Task 3 plan**

In `docs/superpowers/plans/2026-07-22-phase15a-cad-model-library-audit.md`,
replace the local `_terms`/snapshot guidance with:

```text
Task 3 consumes the shared request canonicalizer defined by
docs/superpowers/specs/2026-07-26-shared-request-canonicalizer-design.md.
The audit fingerprint and all business validation values must come from one
FrozenRequest. The original request values must not be converted or iterated
after freeze_request returns.
```

Add the new module and test file to Task 3's approved staging list.

- [ ] **Step 8: Run focused and audit regression suites**

Run:

```powershell
& 'D:\01_Codex Project\point-cloud-system\.venv\Scripts\python.exe' -m pytest tests/test_phase15a_request_canonicalization.py tests/test_phase15a_model_library.py tests/test_phase15a_audit.py -q --basetemp .p15a-canon-t2-focused
```

Expected: all selected tests pass.

- [ ] **Step 9: Run complete verification**

Run:

```powershell
& 'D:\01_Codex Project\point-cloud-system\.venv\Scripts\python.exe' -m pytest -q --basetemp .p15a-canon-t2-full
& 'D:\01_Codex Project\point-cloud-system\.venv\Scripts\python.exe' -m py_compile src/pc_system/request_canonicalization.py src/pc_system/model_library.py tests/test_phase15a_request_canonicalization.py tests/test_phase15a_model_library.py
git diff --check
```

Expected: full pytest, compilation, and whitespace checks exit `0`. The only
accepted warning is the existing Starlette/httpx deprecation warning.

- [ ] **Step 10: Commit Task 2**

Run:

```powershell
git add src/pc_system/model_library.py tests/test_phase15a_model_library.py docs/superpowers/plans/2026-07-22-phase15a-cad-model-library-audit.md
git commit -m "fix: consume frozen model asset requests"
```

Expected: the commit contains exactly the three Task 2 files.

---

## Final branch gate

After both task reviews are clean:

1. generate one review package from `31083e9` through the new HEAD;
2. run a fresh whole-branch review focused on canonicalization, idempotency,
   authorization, audit durability, and immutable publication;
3. fix all Critical and Important findings in one bounded wave;
4. rerun the complete suite;
5. push the same `codex/phase15-model-matching` branch so PR #4 updates;
6. keep PR #4 Draft until the final review and CI gates are clean.
