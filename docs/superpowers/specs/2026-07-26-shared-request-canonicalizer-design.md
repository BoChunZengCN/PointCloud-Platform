# Shared Request Canonicalizer Design

## 1. Status and scope

This design replaces the Phase 15 Task 3 model-library-local request snapshot
with a shared, immutable request-freezing component.

The component exists to guarantee one invariant:

> The value fingerprinted for idempotency and the value consumed by business
> validation come from the same one-time capture.

The first consumer is `create_model_asset`. Later Phase 15 write services may
reuse the component, but this change does not implement model-version import,
retrieval, registration, binding, API, or CLI behavior.

## 2. Problem statement

The current model catalog calls conversion logic twice:

1. before `start_operation`, while building an audit request snapshot; and
2. after `start_operation`, while normalizing business values.

Stateful or adversarial Python objects can return different values from those
two calls. That can make two different valid mutations share one idempotency
fingerprint and replay the wrong result.

The current local snapshot also attempts generic structural inspection.
Operations such as `isinstance()` and user-controlled iteration can raise
before an audit operation exists. Adding more type-specific exception handling
has repeatedly exposed another representation class, so request capture must
become a separate, schema-driven boundary.

## 3. Chosen approach

Use a shared frozen request model.

`freeze_request()` captures each supplied field exactly once according to a
declarative schema. It returns an immutable `FrozenRequest` containing:

- an ASCII-safe canonical audit representation;
- captured business text or list-item text;
- raw type identity;
- stable capture-error or resource-limit markers.

Before audit start, callers may only:

- validate identifiers that form an audit storage path, such as
  `operation_id`;
- freeze the request; and
- pass `FrozenRequest.to_audit_payload()` to `start_operation`.

Authorization and business validity decisions remain after audit start.
Business code must consume the `FrozenRequest`; it must not inspect, iterate,
or convert the original request values again.

## 4. Alternatives rejected

### 4.1 Strict transport types only

Accepting only exact `str` and `list[str]` values is simpler, but it changes
the existing documented normalization behavior for text-like values and makes
the shared component less useful to internal Phase 15 services.

The selected design still requires exact `list` containers for term lists, but
it preserves one-time text conversion for scalar text and list elements.

### 4.2 Normalize before audit start

Performing complete business normalization before `start_operation` would
produce a reliable fingerprint, but authorization and invalid-input failures
could occur without an audit lifecycle. This violates the Phase 15 rule that
write failures are auditable.

### 4.3 Continue extending the local generic snapshot

The local snapshot has already required multiple representation-specific
patches. It has no stable interface separating capture from business use and
cannot guarantee that later code consumes the captured value. It is removed,
not extended.

## 5. Module boundary

Create:

```text
src/pc_system/request_canonicalization.py
tests/test_phase15a_request_canonicalization.py
```

Modify:

```text
src/pc_system/model_matching_identity.py
src/pc_system/model_matching_audit.py
src/pc_system/model_library.py
tests/test_phase15a_identity.py
tests/test_phase15a_audit.py
tests/test_phase15a_model_library.py
```

The final bounded review wave hardens direct principal construction, invalid
audit-request handling, and published-asset audit recovery without changing
the model-library public call signature.

## 6. Public interfaces

Core immutable types:

```python
FieldKind = Literal["identifier", "text", "term_list"]
FieldSpec(name: str, kind: FieldKind)
FreezeLimits(
    max_depth: int = 16,
    max_nodes: int = 4096,
    max_text_bytes: int = 1_048_576,
    max_collection_items: int = 4096,
)
RequestSchema(
    schema_id: str,
    schema_version: str,
    fields: tuple[FieldSpec],
)
FrozenRequestValueError(field_name: str, reason: str)
```

Callable interfaces:

```python
FrozenRequest.to_audit_payload() -> dict
FrozenRequest.require_identifier_text(field_name: str) -> str
FrozenRequest.require_text(field_name: str) -> str
FrozenRequest.require_term_texts(field_name: str) -> tuple[str]
freeze_request(
    schema: RequestSchema,
    values: Mapping[str, object],
    *,
    limits: FreezeLimits = FreezeLimits(),
) -> FrozenRequest
```

`FrozenRequest` internals are immutable. `to_audit_payload()` returns a fresh
JSON-safe dictionary so Task 2 cannot mutate the frozen state.

## 7. Model asset schema

`model_library.py` defines:

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

Field semantics:

- `identifier`: capture only an exact string as a usable business value.
  Other types receive a stable capture marker and are rejected after audit
  start.
- `text`: call `str(value)` exactly once. Store the captured text or a stable
  conversion-error marker.
- `term_list`: require an exact built-in `list`. Iterate it once, subject to
  limits, and call `str(item)` exactly once per item. List subclasses and other
  iterables are rejected after audit start.

Using exact container types is intentional. It avoids invoking an overridden
`__class__`, `__iter__`, or other subclass behavior during type
classification.

## 8. Capture representation

The audit payload has this top-level shape:

```json
{
  "canonicalizer_schema": "1.0",
  "request_schema_id": "model_asset.create",
  "request_schema_version": "1.0",
  "fields": [
    {
      "name": "display_name",
      "kind": "text",
      "raw_type": {
        "module_hex": "6275696c74696e73",
        "qualname_hex": "737472"
      },
      "capture": {
        "status": "ok",
        "text_utf8_surrogatepass_hex": "50756d702041"
      }
    }
  ]
}
```

Rules:

- All keys and status markers are fixed ASCII literals.
- User and dynamically generated text is encoded with UTF-8
  `surrogatepass`, then hexadecimal ASCII.
- Type identity comes from `type(value)`, never `value.__class__`.
- Type metadata access and conversion are guarded and produce fixed error
  markers.
- Exact-string type metadata consumes the same aggregate text budget as
  captured business text. A character-count lower bound is checked before
  UTF-8 encoding or hexadecimal allocation.
- Field order is the schema order.
- Term order is captured input order. Business normalization may later
  lowercase, deduplicate, and sort the captured terms.
- Unknown or missing input fields are represented explicitly and rejected
  after audit start.
- Canonical JSON uses `sort_keys=True`, `ensure_ascii=True`, and compact
  separators.

If two requests can produce different valid normalized business values, their
canonical payloads must differ. The canonicalizer may conservatively
distinguish two inputs that normalize to the same result; an idempotency
conflict is safer than replaying a different mutation.

## 9. Total-function boundary

For finite Python values whose conversion terminates, `freeze_request()` must
not leak an ordinary `Exception`.

The boundary guards:

- schema and mapping access;
- type metadata access;
- `str()` conversion;
- list length and iteration;
- UTF-8 surrogate-pass encoding;
- canonical payload construction.

Captured failures become immutable markers. Business accessors raise
`FrozenRequestValueError` only after the audit operation has started.

The component does not attempt to sandbox non-terminating user code and does
not swallow process-control exceptions such as `KeyboardInterrupt` or
`SystemExit`. Normal production transports provide JSON primitives; arbitrary
Python-object handling is defensive support for internal callers.

## 10. Resource limits

Defaults:

- maximum capture depth: `16`;
- maximum captured nodes: `4096`;
- maximum aggregate captured text bytes: `1,048,576`;
- maximum items in one collection: `4096`.

Limit exhaustion produces a field-specific `limit_exceeded` marker. It does
not raise before audit start. Accessing that field after audit start raises
`FrozenRequestValueError`, which the model library maps to stable
`invalid_model_asset`.

The aggregate text limit includes exact `__module__` and `__qualname__`
metadata, including bounded exception-type metadata. If metadata exhausts the
limit, the field capture is `limit_exceeded` and the payload contains only the
bounded marker, never the oversized metadata text.

Different invalid requests that exceed the same limit may share the same
failure fingerprint. They cannot produce or replay a successful business
mutation.

## 11. Model-library data flow

```text
validate operation_id
        |
        v
freeze MODEL_ASSET_CREATE_SCHEMA once
        |
        v
start_operation(frozen.to_audit_payload())
        |
        +--> replay: authorize -> validate from this FrozenRequest
        |             -> replay terminal result, finish an invalid running
        |                request, or recover a matching published asset
        |             failures use the independent replay-failure audit
        |
        v
authorize expert
        |
        v
read captured values from FrozenRequest
        |
        v
validate identifiers / normalize terms / build manifest
        |
        v
atomic no-replace publication -> audit event -> completion
```

`create_model_asset()` must never call `str()`, iterate `keywords`/`tags`, or
inspect type information on the original business values after freezing.

## 12. Error handling

- Capture/conversion/type/list/limit errors:
  `FrozenRequestValueError` after audit start, mapped to
  `invalid_model_asset`.
- Authorization errors: unchanged `permission_denied`.
- Idempotency mismatch: unchanged `idempotency_conflict`.
- Audit persistence errors: unchanged fail-closed
  `audit_persistence_error`.
- Asset publication and integrity errors: unchanged.

Failed fresh operations terminate through Task 2 `fail_operation`.
Failures after terminal replay use the existing independent
`model_asset.replay_failure` operation and never mutate the original terminal
projection.

If failure terminalization cannot be made durable, `audit_persistence_error`
overrides the business error. A same-idempotency retry of a still-running
invalid request validates the new call's one frozen capture and may finish the
canonical operation as failed. A still-running valid request without a
published asset remains `operation_busy`.

Invalid `request_id` and `idempotency_key` values at the public audit-start
boundary become stable `invalid_audit_request`. A separate
`audit.mutation_failure` operation records only fixed bounded text and the
valid target operation ID; failure to persist that audit fails closed as
`audit_persistence_error`.

## 13. Persistence and compatibility

The canonicalizer changes the request fingerprint representation used by the
unreleased Task 3 implementation.

No migration is required because Phase 15 is still a draft PR and has not been
merged into `main`. If a developer retains local WIP idempotency indexes, the
new request fingerprint fails closed with `idempotency_conflict`; it never
silently replays an operation under the old representation.

The model asset manifest adds the canonical `operation_id`. Once the immutable
asset is published, audit append or completion interruption leaves the
canonical operation running (or completed when completion was already
durable); it is never changed to failed. A same-idempotency expert replay
verifies every stable frozen business field, canonical actor and operation,
and the exact `model_asset.created` fingerprint before completing and
returning the existing asset. Matching events are reused; conflicts fail
closed and the asset is never overwritten.

Asset no-replace publication also distinguishes the actual `os.link` outcome.
Failure before the link remains `model_asset_persistence_error` and may
terminalize the operation. Failure of directory durability after a successful
link returns `publication_recovery_required`, preserves the visible asset and
running canonical operation, and uses the same verified replay recovery path;
path existence is never used to guess publication ownership.

## 14. Testing strategy

### 14.1 Canonicalizer unit tests

- `str()` is called once and the same captured value is returned to business
  code.
- A stateful `str()` cannot make audit and business values diverge.
- An exception-raising `__class__` property is never accessed.
- Conversion exceptions become captured errors.
- Surrogate type metadata and text remain JSON- and UTF-8-safe.
- Exact lists are iterated once; list subclasses are rejected without
  iteration.
- Mutation of original values after freeze does not alter the frozen request.
- Field order and canonical payload are deterministic.
- Different valid captured values have different canonical payloads.
- Missing/unknown fields and every resource limit produce stable markers.
- Oversized exact type metadata consumes the shared budget without oversized
  audit payload allocation.

### 14.2 Model-library integration tests

- Stateful display-name values use the single frozen capture.
- Same idempotency key plus a different captured valid value produces
  `idempotency_conflict`.
- Exceptional `__class__`, `str()`, and list values produce audited
  `invalid_model_asset`, never a raw pre-audit exception.
- Existing authorization, replay failure, concurrent no-replace publication,
  manifest integrity, and listing tests remain green.
- Invalid audit identifiers are separately audited without raw values.
- Failure-terminalization interruption is recoverable on same-idempotency
  retry.
- Append/completion interruption, manifest/event tampering, acknowledgement
  loss, and concurrent published recovery preserve the immutable asset and
  converge the canonical operation.

### 14.3 Regression gates

- canonicalizer tests;
- Task 3 model-library tests;
- all Task 2 audit tests;
- complete repository pytest suite;
- `py_compile`;
- `git diff --check`.

## 15. Acceptance criteria

The design is complete when:

1. no local snapshot/canonicalization helpers remain in `model_library.py`;
2. every Task 3 business value is read from `FrozenRequest`;
3. the raw request is not converted or iterated after `freeze_request`;
4. stateful conversion and exceptional `__class__` regression tests pass;
5. the independent review reports no Critical or Important canonicalization,
   idempotency, authorization, audit, or immutable-publication findings;
6. the full repository suite passes;
7. PR #4 remains Draft until these gates are satisfied.
