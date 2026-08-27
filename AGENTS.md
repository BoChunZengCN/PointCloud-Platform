# Point Cloud Platform — Project Memory

## Development Efficiency Protocol

These rules are the default operating model for future phases. They exist to
preserve engineering quality while limiting unnecessary execution time,
review loops, test runs, and conversation/token usage.

### 1. Freeze scope before implementation

- Define the task outcome, allowed files, acceptance tests, and explicit
  non-goals before editing production code.
- Treat newly discovered non-blocking work as follow-up debt instead of
  silently expanding the active task.
- Security-, concurrency-, persistence-, and recovery-sensitive work requires
  a short written design and threat boundary before implementation.

### 2. Stop patch stacking early

- Permit at most two local fix/review cycles for the same defect class.
- If a second review still finds a related Critical or Important issue, stop
  local patching and redesign the affected mechanism at the architectural
  level before continuing.
- Do not build increasingly complex filesystem or concurrency safeguards on a
  design whose core primitive cannot satisfy the required invariant.

### 3. Use proportional verification

For each change, run tests in this order:

1. New or directly affected regression tests.
2. The affected module or focused integration suite.
3. The full repository suite once at the phase/commit readiness gate.
4. The full suite once more only when required before push or merge.

Do not rerun the full suite after every micro-edit. A failing focused test must
be resolved before advancing to broader verification.

### 4. Keep review bounded

- Default to one implementer and one independent final reviewer per task.
- Perform design review before implementation for high-risk mechanisms, then
  perform one implementation review after focused verification.
- Fix all confirmed Critical and Important findings. Record Minor findings for
  later unless they block the stated acceptance criteria.
- Review summaries should contain only verdict, Critical/Important findings,
  verification evidence, and commit SHA. Store detailed probes in project
  reports rather than repeating them in the main conversation.

### 5. Control context and token usage

- Main progress updates should use the compact form: completed work, test
  evidence, current blocker, next action.
- Avoid repeating unchanged history, full command transcripts, temporary test
  paths, or previously reported findings.
- Give sub-agents only the task brief, relevant file paths, exact Git range,
  and acceptance criteria; do not fork the complete conversation unless it is
  genuinely required.
- Prefer one consolidated tool call for independent read-only checks and avoid
  polling or duplicate repository inspection.

### 6. Keep commits intentional

- Aim for one feature commit and at most one review-fix commit per bounded
  task.
- Stage exact files only; never use broad staging for a dirty worktree.
- Do not mix unrelated fixes, generated test directories, or another task's
  changes into a commit.
- Run focused verification, static checks, and diff checks before committing;
  run the full suite at the readiness gate defined above.

### 7. Escalate architecture decisions explicitly

Stop and request direction when completion requires a meaningful change to
the approved architecture, threat model, persistence semantics, or user-visible
workflow. Present one recommended option with its trade-offs instead of
continuing an open-ended patch loop.

## Current Lesson from Phase 15A

The audit-discard work demonstrated that path-based cleanup and quarantine can
expand into a cross-platform secure-filesystem transaction problem. Future
work in this area must begin with an explicit recovery architecture and threat
model. Do not attempt repeated TOCTOU fixes around automatic rename or
recursive deletion without first proving that the underlying platform
primitive can enforce the required invariant.
