# Spec-0023: Post-review hardening (peer review of spec-0022)

- **Status:** Implemented
- **Linked ADR:** _none — no boundary change_
- **Linked CHANGELOG entry:** `[Unreleased]` › `<Fixed|Added|Changed>`

## Problem

An adversarial peer review of the spec-0022 branch — three parallel audits
covering the branch diff, the coverage/test surface, and the Claude Code
harness — found defects in spec-0022's own work plus long-standing gaps it
had surfaced. The blocker: the injection guard spec-0022 added to protect
`deploy.yml` matched a literal, and GitHub allows arbitrary whitespace inside
`${{ }}`, so the exact line the guard exists to forbid passed it with one
space deleted. A guard a one-character edit defeats is worse than none,
because it reads as protection.

The rest fall into four groups: observability that never reached the operator
(`--verbose` dying mid-run, a silent RAG pipeline), guards weaker than what
they guard (the coverage gate untested, a security invariant defended by
examples, a fuzz test blinded by a pinned parameter), claims stronger than
their mechanism (CLAUDE.md asserting enforcement of config defaults that
nothing compared), and unowned or undocumented surfaces (no agent owning
CI/build, no scheduled automation at all).

## Requirements

- R1: a CLI command's `--verbose` must still emit DEBUG after the tracer
  bootstraps, and a CLI run must honour `MANGOMAS_LOG_LEVEL`,
  `MANGOMAS_LOG__FORMAT` and `MANGOMAS_TELEMETRY__EXPORTER`.
- R1a (added in review): **no module under `src/mangomas` may call
  `mangomas.telemetry.get_tracer` at import time.** R1's settings half was
  unreachable without this. `get_tracer` self-bootstraps `configure_telemetry()`
  with hard-coded defaults, and `configure_telemetry` is idempotent — so a
  module-level `_tracer = get_tracer(__name__)` latches the whole process the
  moment its module is imported. Four modules did exactly that
  (`eval/gate.py`, `eval/baseline.py`, `eval/sinks/langfuse.py`,
  `rag/pipeline.py`); the CLI imports all four, so `configure_cli_logging` was
  always the *second* caller and its format + exporter were silently discarded.
  The rule existed in prose and was enforced only against the
  `mangomas.api.app` import chain, which imports none of the four. It is now a
  totality scan over `src/`. The log *level* is additionally re-applied after
  the idempotent call, so it survives a latch this scan cannot prevent (a
  third-party import, a prior in-process command).
- R2: `rag/` must log its ingestion decisions — start, finish, per-batch
  progress, and the two silent-failure paths (empty path, document dropped
  for producing no chunks) — under an `rag.ingest` span.
- R3: CLAUDE.md's documented config **defaults** must be compared against the
  live `Settings` fields, so the "No hard-coded values" Enforced-by cell is
  true rather than name-only.
- R4: the workflow injection guard must be whitespace-insensitive and cover
  the whole attacker-influenced context family; workflow discovery must see
  `*.yaml` and job-level `uses:`; parsing must be shared with
  `test_ci_make_parity.py` so the two cannot drift.
- R5: `scripts/check_coverage.py`'s own `_check`/`main` must be tested — the
  gate was the least-covered script, and a defect there fails open with no
  coverage number able to reveal it. `SCRIPTS_FLOOR`/`BRIDGE_FLOOR` must be
  pinned.
- R6: `_headers.sanitize_header_token`, the shared log-injection /
  SQL-parameter defence, must be defended by properties over arbitrary text;
  both hand-maintained governance fallbacks must be pinned to the
  `pyproject.toml` table.
- R7: every repeated procedure must live in a skill, and every surface in an
  agent — specifically the `.claude/settings.json` lockstep, the subprocess
  meta-test pattern, the OpenAPI-snapshot regen, and the CI/build surface.
- R8: the Stop hook must surface failures instead of swallowing them, and the
  gated suites plus `secret-scan` must run on a schedule.

Hard-coded values named rather than left inline: `core/tools.py`'s repeated
truncation bound and `adapters/storage`'s triplicated `list_turns` limit.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| _none_ | — | — |

## Protocol / contract impact

- New/changed protocols: _none_. `TurnRepository.list_turns`'s default is now
  a named constant of equal value.
- New error types: _none_
- Registry additions: _none_

## Backwards-compatibility

- No runtime behaviour changes except the intended ones: CLI logging now
  honours configured settings (previously hard-coded defaults), and the RAG
  pipeline emits logs/spans it previously did not.
- `src/mangomas/core/tools.py` is a protected path; the edit replaces four
  literals with an equal-valued named constant and carries the required
  `BREAKING-CHANGE` marker per ADR-0021. No signature, status or wire shape
  moves.
- `MANGOMAS_RAG__MIN_CHUNK_WORDS` is documented as inert rather than changed
  — see Scenarios.

## Scenarios (WHEN/THEN)

- WHEN a CLI command runs with `--verbose` and something later calls
  `get_tracer()` THEN DEBUG records still emit; WHEN it runs without
  `--verbose` THEN the level comes from `MANGOMAS_LOG_LEVEL`, not a
  hard-coded `INFO`.
- WHEN any module under `src/mangomas` binds a tracer at import time THEN the
  scan names the file and line; WHEN `MANGOMAS_LOG__FORMAT=json` is set and a
  CLI command runs THEN its log lines are JSON; WHEN telemetry was already
  configured at DEBUG and a command runs without `--verbose` THEN the level
  drops back to the configured one.
- WHEN a workflow `run:` body contains `${{github.event.x}}` with any
  whitespace THEN the guard fails; WHEN it contains `${{ env.BASE_BRANCH }}`
  THEN it passes.
- WHEN a documented CLAUDE.md default differs from the field's real default
  THEN the contract test names both values.
- WHEN a document produces no chunks THEN a warning names the source and the
  `min_chunk_words` in force; WHEN the path yields no documents THEN a
  warning says so rather than reporting `0` silently.
- WHEN `min_words` varies THEN the chunker's output is unchanged — the
  trailing-fragment drop is provably unreachable (the loop only steps again
  when the previous window was not last, so the final window always extends
  past it). Verified exhaustively over `n < 40` × `size < 15` × every
  overlap: zero reachable states. Resolving that mismatch — accept word loss,
  or retire the knob — is a retrieval-quality decision, deliberately deferred.

## Test plan

- Unit: `tests/test_cli_runtime.py` (R1/R1a), `tests/test_telemetry.py` (R1a),
  `tests/rag/test_pipeline.py` (R2),
  `tests/deploy/test_env_example_contract.py` (R3),
  `tests/deploy/test_workflow_hardening.py` + `_workflows.py` (R4),
  `tests/test_check_coverage.py` (R5), `tests/test_headers_properties.py` +
  `tests/harness/test_governance.py` (R6),
  `tests/tooling/test_corpus_contract.py` (R7),
  `tests/deploy/test_ci_make_parity.py` (R8).
- Gated: none — every new test runs offline in the default suite.
- Gates: proved in both directions. Each new guard was mutation-tested by
  reverting the thing it guards and confirming it fails.
- Coverage: global 95% and per-package floors unchanged; `SCRIPTS_FLOOR`
  ratchets 84 → 92 on measured 94%.

## Acceptance criteria

- [x] `${{github.event.x}}` (no space) fails the injection guard.
- [x] `--verbose` still emits DEBUG after a lazy `get_tracer()`.
- [x] A module-level `get_tracer()` anywhere under `src/` fails the scan.
- [x] A CLI run honours `MANGOMAS_LOG__FORMAT=json` end to end.
- [x] A CLAUDE.md default that disagrees with `Settings` fails CI.
- [x] `scripts/check_coverage.py` reaches 100%; the floor ratchets.
- [x] Widening the header charset to admit CR/LF fails a property test.
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; no ADR needed (no boundary changed).
