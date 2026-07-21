# Phase 14 Segmentation Correction Loop Design

Date: 2026-07-20

Status: Approved

Depends on: Phase 13A segmentation runs and Phase 13B golden evaluation

## 1. Goal

Phase 14 adds a human-in-the-loop correction workflow around automatic point-cloud segmentation. The algorithm remains responsible for producing the first result. People review suspected errors, confirm correct objects, and make small targeted corrections.

The completed loop is:

```text
automatic segmentation
  -> prioritized review queue
  -> correction draft
  -> confirm / merge / split / relabel / noise correction
  -> review and immutable publication
  -> derived golden-label version
  -> Phase 13B evaluation and optional regression gate
  -> bounded parameter-search trigger
  -> versioned feedback dataset for later training
```

The phase must make common corrections simple while preserving exact source-point membership, immutable history, provenance, and future training eligibility.

## 2. Current Baseline

Phase 13A already provides:

- Immutable source-point fingerprints.
- Versioned segmentation runs.
- Object membership artifacts with `source_point_indices`.
- Requested and executed algorithm metadata.
- Operational proxy quality findings.

Phase 13B already provides:

- Immutable JSON/JSONL benchmark imports.
- Strict point correspondence.
- Point, instance, class, noise, and bounding-box metrics.
- Candidate/baseline comparisons and regression gates.
- Bounded deterministic parameter search.
- Read-only APIs and frontend evaluation summaries.

The existing comparison baseline is a metric baseline. It cannot restore label state. There is currently no correction draft, event history, undo/redo, label-release lineage, or feedback dataset.

## 3. Scope

Phase 14 includes:

- Correction sessions created from completed Phase 13A runs.
- Optional initialization from an existing compatible Phase 13B benchmark sample.
- A deterministic review queue derived from segmentation risks.
- Confirm, merge, split, relabel, mark-noise, and restore operations.
- Draft revision control, undo, redo, and baseline restoration.
- A simple object-first correction workbench.
- Immutable correction releases and derived benchmarks.
- Automatic Phase 13B reevaluation after publication.
- Optional baseline comparison and bounded parameter-search trigger.
- Training-ready feedback bundles with explicit eligibility policy.
- CLI, protected write APIs, read APIs, documentation, and tests.

## 4. Non-Goals

Phase 14 does not include:

- Training or fine-tuning a segmentation model.
- Automatically replacing a production model or configuration.
- Real-time simultaneous editing of one sample.
- Loading a complete unbounded LAS/LAZ file into the browser.
- CAD or model-library retrieval and registration.
- Cross-time equipment identity resolution.
- Distributed annotation orchestration.

Model training, Champion/Challenger validation, drift detection, and controlled production promotion remain later phases. Phase 14 only guarantees that its feedback format is suitable for those workflows.

## 5. Core Architecture

The subsystem has six focused components.

### 5.1 Correction Sessions

`correction_sessions` owns session creation, lifecycle state, active editor, optimistic revision, and artifact references.

A session binds:

- `session_id`
- `asset_id`
- `segmentation_run_id`
- source URI and source fingerprint
- source point count
- optional base benchmark and sample
- base-label fingerprint
- editor and optional reviewer
- status and revision
- timestamps
- current event cursor
- artifacts and downstream release references

### 5.2 Correction Events

`correction_events` validates and appends correction operations. Events are immutable and ordered. Accepted write operations increment the session revision.

### 5.3 Draft Materialization

`correction_materializer` reconstructs the current draft from:

1. the selected baseline;
2. accepted correction events;
3. undo, redo, and restoration control events.

Materialization is deterministic. The same baseline and event log must produce the same point assignments, object summaries, and draft fingerprint.

### 5.4 Review Queue

`correction_review` turns Phase 13A findings and optional Phase 13B errors into a prioritized queue. The queue is a review aid, not a claim of accuracy.

### 5.5 Correction Releases

`correction_releases` freezes a reviewed draft, validates it, writes an immutable release, creates a derived benchmark, and invokes downstream evaluation.

### 5.6 Training Contract

`correction_training_contract` writes before/after assignments, provenance, quality state, and training eligibility without starting training.

## 6. Session Lifecycle

The lifecycle is:

```text
draft -> in_review -> published
   \-> abandoned
```

Rules:

- Only `draft` sessions accept correction operations.
- `submit` transitions a valid draft to `in_review`.
- A rejected review returns the session to `draft` with a review event.
- `publish` is allowed only from `in_review`.
- `published` and `abandoned` sessions are immutable.
- A published release may be superseded but never overwritten.
- Restoring an old published version creates a new session and new release.

Development mode may allow the editor to publish their own work. Production policy may require a distinct reviewer.

## 7. Initial Baselines

A correction session always starts from a completed Phase 13A run.

### 7.1 Automatic-Segmentation Baseline

When no benchmark is supplied:

- Each predicted object becomes one initial instance.
- `source_point_indices` determine point membership.
- Unassigned source points become noise.
- The initial class is the predicted `class_id`, label, or `object_candidate`.
- Axis-aligned boxes are derived from member points and truthfully marked as generated.

This route allows people to correct an automatic result without first producing labels manually.

### 7.2 Existing-Label Baseline

When a benchmark sample is supplied:

- The benchmark source fingerprint must match the run source fingerprint.
- Point labels are overlaid using strict source indices.
- Existing labels and boxes become the baseline.
- Unlabeled evaluated points retain the automatic segmentation assignment but are marked as machine-originated.
- Provenance distinguishes human, imported, and machine-originated labels.

The base benchmark remains immutable.

## 8. Point and Object Contract

The draft materializes a complete assignment for every evaluated source point:

```json
{
  "source_point_index": 42,
  "instance_id": "obj-003",
  "class_id": "pipe",
  "is_noise": false,
  "origin": "machine"
}
```

Invariants:

- A source point has exactly one current assignment.
- A non-noise assignment has a valid instance and class identifier.
- Noise uses the reserved instance and class `noise`.
- No event may reference an index outside the evaluated source-point range.
- Object identifiers are stable inside one correction session.
- New identifiers are generated from the session sequence and never reused.

The browser operates on projected points, but every selection sent to the backend is a list of exact `source_point_index` values.

## 9. Correction Operations

### 9.1 Confirm

`confirm` marks an object as reviewed without changing membership. It is a one-click action and records the reviewer decision.

### 9.2 Merge

`merge` requires at least two active non-noise objects.

- The user selects source objects.
- The server previews the resulting membership, class conflicts, and point count.
- Confirmation creates one target object.
- If classes differ, an explicit target class is required.
- Source objects are retired in the draft but remain visible in history.

### 9.3 Split

`split` operates on one active non-noise object.

- The user selects the points that form the new fragment.
- The original instance keeps the unselected remainder.
- The selected fragment receives a new deterministic instance ID.
- Both sides must be non-empty and satisfy the configured minimum point count.
- The new fragment inherits the source class unless the event specifies a new class.

The interface needs only one selected subset; the system automatically computes the remainder.

### 9.4 Relabel

`relabel` changes the class of an object. It does not change membership.

### 9.5 Noise Correction

`mark_noise` accepts complete objects or explicit source-point indices. `restore_from_noise` requires a valid destination object or creates a new object with an explicit class.

### 9.6 Undo and Redo

Undo and redo append control events that reference earlier correction events. They do not remove history.

- Undo marks the latest active reversible event as inactive.
- Redo reactivates the latest undone event.
- A new correction after undo invalidates the old redo branch.
- Session-wide and object-scoped restoration remain separately auditable.

### 9.7 Baseline Restoration

`restore` supports:

- full-draft reset to the selected baseline;
- one-object restoration;
- restoration of explicit point indices.

Restoration appends an event and increments the revision. It never truncates the log.

## 10. Review Queue

The review queue ranks likely correction candidates using recorded evidence:

- explicit engine fallback;
- low confidence;
- suspected merged objects;
- small-fragment or over-segmentation findings;
- high noise concentration;
- abnormal bounds or density;
- optional Phase 13B false-positive, false-negative, and low-IoU evidence;
- unreviewed machine-originated labels.

Each item records:

- reason code;
- severity;
- object IDs;
- evidence values;
- suggested action;
- deterministic priority score;
- review status.

Suggested actions are advisory. The system never applies a correction without an explicit user event.

## 11. Simple Correction Workbench

Phase 14 adds `frontend/correction.html`, `correction.js`, and `correction.css`.

### 11.1 Layout

- Left: prioritized review queue.
- Center: interactive point-cloud canvas.
- Right: context-sensitive actions and object facts.
- Bottom: undo, redo, restore baseline, save state, submit, and publish status.

### 11.2 Viewer

The built-in viewer uses native Canvas projection over the bounded Phase 13A evaluated point set.

It supports:

- rotate, zoom, and pan;
- top, front, and side presets;
- object coloring;
- selected-point highlighting;
- box, lasso, and brush selection;
- object click selection;
- optional display of baseline versus draft.

The first-party viewer avoids a mandatory heavy frontend dependency. A later Potree or WebGL adapter may consume the same selection and event APIs.

### 11.3 Interaction Simplicity

- Confirm: one click.
- Relabel: select an object and choose one class.
- Noise toggle: one click.
- Merge: choose objects and confirm once.
- Split: select one subset and confirm once.
- Advanced point-selection controls remain collapsed until needed.
- Every destructive-looking action shows a preview summary.
- Undo, redo, and baseline restore remain visible.

## 12. Concurrency and Collaboration

Multiple users may correct different samples concurrently. One sample has at most one active editable draft.

- The active editor owns a renewable soft lock.
- Other users may read the session, queue, points, and diff.
- Every write carries `expected_revision`.
- A stale revision returns HTTP 409.
- A conflicting active editor returns HTTP 423.
- Lock release occurs on submit, abandon, explicit release, or configured expiry.
- Lock expiry does not discard draft data.
- Every event records actor, timestamp, client request ID, and resulting revision.

Real-time co-editing of the same sample is out of scope.

## 13. Baselines, Versions, and Recovery

The system records:

- source segmentation baseline;
- optional label baseline;
- correction-session baseline;
- published release lineage;
- evaluation baseline used for regression.

Recovery semantics:

- Draft reset creates a restore event.
- Partial restoration creates a scoped restore event.
- Published releases are immutable.
- Rolling back a release creates a new release derived from the selected historical release.
- A release records `parent_release_id` and `supersedes_release_id` where applicable.

Raw LAS/LAZ data is never restored because it is never modified.

## 14. Artifacts

Session artifacts:

```text
reports/segmentation_corrections/<asset_id>/<session_id>/
  correction_session.json
  baseline_labels.json
  events.jsonl
  draft_labels.json
  draft_objects.json
  review_queue.json
  correction_diff.json
  publication.json
```

Release and feedback artifacts:

```text
reports/segmentation_correction_releases/<asset_id>/<release_id>/
  release.json
  labels.json
  objects.json
  publication_tasks.json

datasets/segmentation_feedback/<release_id>/
  feedback_manifest.json
  before_labels.json
  after_labels.json
  event_summary.json
  training_policy.json
```

Derived benchmark data uses the existing Phase 13B layout under a new immutable benchmark ID.

## 15. Publication Pipeline

Publication executes these durable steps:

1. Freeze the reviewed revision.
2. Recheck session state, editor lock, and expected revision.
3. Verify source fingerprint and evaluated point count.
4. Materialize and validate all assignments.
5. Recompute summaries and affected boxes.
6. Write the immutable correction release.
7. Write the derived Phase 13B benchmark.
8. Write the feedback dataset and training policy.
9. Evaluate the original segmentation run against the new labels.
10. Optionally compare with a supplied evaluation baseline.
11. If policy permits, create or execute a bounded parameter-search task.

The release remains published if a downstream evaluation or search fails. `publication_tasks.json` records each task as planned, running, completed, or failed and supports retry without repeating correction work.

Automatic parameter search is forbidden when the source split is `golden_regression`. Such data is evaluation-only.

## 16. Future Training Contract

Every feedback release records:

- source asset and point fingerprint;
- source segmentation run and algorithm version;
- algorithm configuration fingerprint;
- before and after assignments;
- correction events and affected point indices;
- editor and reviewer;
- benchmark split and license;
- evaluation and gate results;
- training eligibility and reasons;
- parent dataset and release lineage.

Training policy values are:

- `eligible`: reviewed non-golden data with compatible license and valid evaluation.
- `evaluation_only`: data intentionally reserved for evaluation.
- `blocked`: invalid provenance, incompatible license, failed publication validation, or golden-regression use.

Phase 14 never starts model training and never promotes a model. Later training phases must consume only explicitly eligible releases and still pass Champion/Challenger validation.

## 17. Public Interfaces

### 17.1 APIs

Read APIs:

```text
GET /segmentation-corrections/<asset_id>
GET /segmentation-corrections/<asset_id>/<session_id>
GET /segmentation-corrections/<asset_id>/<session_id>/points
GET /segmentation-corrections/<asset_id>/<session_id>/events
GET /segmentation-corrections/<asset_id>/<session_id>/review-queue
GET /segmentation-corrections/<asset_id>/<session_id>/diff
GET /segmentation-correction-releases/<asset_id>/<release_id>
```

Protected write APIs:

```text
POST /segmentation-corrections
POST /segmentation-corrections/<asset_id>/<session_id>/events
POST /segmentation-corrections/<asset_id>/<session_id>/undo
POST /segmentation-corrections/<asset_id>/<session_id>/redo
POST /segmentation-corrections/<asset_id>/<session_id>/restore
POST /segmentation-corrections/<asset_id>/<session_id>/submit
POST /segmentation-corrections/<asset_id>/<session_id>/review
POST /segmentation-corrections/<asset_id>/<session_id>/publish
POST /segmentation-correction-releases/<asset_id>/<release_id>/retry
```

The points API is paginated and capped. It returns exact source indices, coordinates, baseline assignments, draft assignments, and review state.

### 17.2 CLI

CLI commands support automation and non-browser testing:

- `create-segmentation-correction`
- `apply-segmentation-correction-event`
- `submit-segmentation-correction`
- `publish-segmentation-correction`
- `retry-correction-publication`

Interactive selection remains a frontend responsibility.

## 18. Validation and Error Handling

Stable errors include:

- invalid or incomplete source run;
- source fingerprint mismatch;
- incompatible base benchmark;
- session already active;
- session locked by another editor;
- stale revision;
- illegal lifecycle transition;
- invalid point index;
- duplicate point assignment;
- invalid merge cardinality;
- invalid split selection;
- class conflict requiring an explicit class;
- published session mutation;
- publication validation failure;
- training-policy violation;
- forbidden golden-regression parameter search.

Rejected events do not advance the revision or alter draft artifacts.

No API response exposes unrestricted filesystem paths. External identifiers are validated before path construction. Existing API-key protection applies to every write route.

## 19. Testing Strategy

### 19.1 Unit Tests

- Session lifecycle and revision transitions.
- Event validation and deterministic replay.
- Confirm, merge, split, relabel, noise, undo, redo, and restoration.
- Queue scoring and suggested actions.
- Diff summaries and draft fingerprints.
- Training eligibility.

### 19.2 Synthetic Geometry Tests

- Separated objects that should remain unchanged.
- One object incorrectly split into fragments.
- Two objects incorrectly merged.
- Thin structures and small fragments.
- Noise points restored into an object.
- Affected box recomputation.

### 19.3 Integration Tests

- Create a session from a completed Phase 13A run.
- Initialize with and without Phase 13B labels.
- Apply operations through API and CLI.
- Detect stale revisions and locks.
- Submit, review, publish, and restore.
- Build a derived benchmark.
- Execute Phase 13B evaluation and optional regression.
- Write a feedback release with correct training policy.

### 19.4 Frontend Tests

- Review queue and context-sensitive actions.
- Canvas projection and exact index selection.
- Simple merge and split flows.
- Undo, redo, restore, and diff display.
- Loading, conflict, validation, and publication states.

### 19.5 Regression

All Phase 1 through Phase 13B tests must continue to pass.

## 20. Acceptance Criteria

Phase 14 is complete when:

- A completed automatic segmentation run can create a correction session without preexisting labels.
- People can confirm, merge, split, relabel, and correct noise using exact source indices.
- Common operations follow the approved one- or two-step interaction model.
- Drafts support deterministic undo, redo, and full or partial baseline restoration.
- Concurrent stale writes cannot overwrite newer work.
- Publication produces an immutable release, derived benchmark, and feedback dataset.
- Published labels can be evaluated by Phase 13B.
- Optional regression and parameter-search tasks use the correct release and split policy.
- Golden-regression data cannot become training data or parameter-search input.
- Historical releases can be restored only by creating a new release.
- The correction workbench clearly separates machine suggestions from human-confirmed state.
- Full regression and security tests pass.

## 21. Delivery Order

1. Session, baseline, and assignment contracts.
2. Event validation and deterministic materialization.
3. Merge, split, relabel, noise, undo, redo, and restore.
4. Review queue and correction diff.
5. Immutable publication and release lineage.
6. Derived benchmark, automatic evaluation, regression, and feedback export.
7. Protected APIs and CLI.
8. Simple correction workbench.
9. Documentation, end-to-end tests, full regression, and independent review.

## 22. Confirmed Decisions

- Automatic segmentation always produces the initial result.
- People confirm and correct; they do not annotate from zero by default.
- The browser edits the bounded Phase 13A evaluated point set.
- Raw LAS/LAZ remains immutable.
- The workflow uses draft, review, and immutable publication.
- One sample has one active editor; different samples may be edited concurrently.
- The interaction is object-first with system suggestions and hidden advanced tools.
- Baseline restoration and label-version lineage are first-class capabilities.
- Feedback is designed for later self-training, but Phase 14 does not train or promote models.
