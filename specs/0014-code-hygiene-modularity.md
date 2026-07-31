# Spec-0014: Code hygiene & modularity overhaul

- **Status:** In progress — defect + deduplication tiers complete and merging
  via this PR; the remaining oversized-module decompositions (`cli/main.py`,
  `config.py`, `telemetry.py`) and the protected-path `core/tools.py` /
  `errors.py` batch are descoped to **spec-0015** (see "Scope revision" below)
  rather than bundled into this PR.
- **Linked ADR:** ADR-0019 (re-export facade decomposition) — Accepted, proven
  by the `api/app.py` split; the remainder of its scope carries over to
  spec-0015.
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed` / `Changed` / `Added` / `Removed`

## Scope revision (mid-execution)

The milestone plan below was written before this PR (#24) was flipped to
ready-for-review partway through execution. To keep the PR mergeable and
reviewable rather than open-ended, the **defect fixes (D1–D12) and the
low-risk deduplication milestones** (`agents/_prompt.py`,
`agents/_structured.py`, `eval/_langfuse.py`, `eval/_options.py`,
`mangomas/_entry_points.py`, `mangomas/_headers.py`,
`OpenAICompatHTTPClient._request`/`_log_and_translate`, the GCP secrets
error-handling collapse) are **in scope and complete**. The churnier
structural splits — `cli/main.py` (708 lines) into command modules,
`config.py`/`telemetry.py` into packages, and the protected-path
`core/structured.py` extraction from `core/tools.py` + `errors.py` — are
**deferred to a follow-up PR**, tracked by `specs/0015-package-decomposition.md`.
R2's `core/structured.py` bullet and R3 (oversized-module decomposition
beyond `api/app.py`) below describe that deferred work; they are not met by
this PR and their acceptance boxes are unchecked accordingly.

## Problem

A full-repo audit found twelve verified defects hiding inside residual
duplication (a silently dropped tool prompt, untruncated upstream bodies in
client-visible errors, config errors surfacing as per-row failures, a broken
`make rag` target, a resource leak, a missing lock, an import-time telemetry
side effect), ~100 lines of dead code plus three dead `AgentSettings` fields,
about ten measured duplication clusters, and four oversized modules
(`cli/main.py` 708 lines, `config.py` 593 lines and the repo's #1 churn file,
`telemetry.py` 337 lines, `create_app` the only C901 violation). The intended
outcome is a defect-free, deduplicated, decomposed codebase with every tunable
reachable through `Settings` and the full quality gate green at every commit.

## Requirements

- R1 — every audited defect (D1–D12) fixed with a dedicated regression test.
- R2 — duplication clusters collapsed behind shared helpers
  (`agents/_prompt.py`, `agents/_structured.py`, `eval/_langfuse.py`,
  `eval/_options.py`, `adapters/_openai_client._request` /
  `_log_and_translate`, `secrets/gcp.py::_handle_failure`,
  `mangomas/_entry_points.py`, `mangomas/_headers.py`). `core/structured.py`
  is deferred to spec-0015 (protected-path batch).
- R3 — oversized modules decomposed into packages with **permanent re-export
  facades** at the old import paths (ADR-0019); existing tests pass
  unmodified. Landed for `api/app.py` (M11); `cli/main.py`, `config.py`, and
  `telemetry.py` are deferred to spec-0015.
- R4 — no dead configuration surface: `AgentSettings.temperature` and
  `max_tokens` are consumed by agents; `max_tool_steps` is env-driven;
  `model_override` is explicitly documented as reserved.
- R5 — CI and the Makefile cannot drift: CI invokes `make` targets and a
  contract test pins the coverage floors that are declared in two places.
- Must remain **additive & default-OFF** where behaviour changes are optional;
  the two deliberate behaviour fixes (D1 tool-prompt concatenation, D9 step
  budget) are called out in the CHANGELOG as fixes.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_AGENTS__<NAME>__MAX_TOOL_STEPS` | `5` (`DEFAULT_TOOL_MAX_STEPS`) | Per-agent cap on ToolAgent LLM calls (previously an unreachable literal) |
| `MANGOMAS_AGENTS__<NAME>__TEMPERATURE` | _(none)_ | Per-agent sampling override, now actually forwarded to `LLMClient.complete/stream` |
| `MANGOMAS_AGENTS__<NAME>__MAX_TOKENS` | _(none)_ | Per-agent completion cap, forwarded via the new additive protocol keyword |

All tunables are `DEFAULT_*` module constants surfaced through a `Settings`
group — **no hard-coded values**.

## Protocol / contract impact

- `LLMClient.complete` / `StreamingLLMClient.stream`
  (`adapters/llm/base.py`): gain an **additive keyword-only**
  `max_tokens: int | None = None` parameter (default preserves behaviour).
- New error types: _none_. Middleware envelope codes (`request_too_large`,
  `server_at_capacity`) are documented in `errors.py` as an API-layer
  vocabulary, not new classes.
- Registry additions: _none_ (workflow node registration gains a
  `register_node` convenience wrapper; keys unchanged).
- Removed: _none_ in this PR. The dead-code removal
  (`core.tools.ToolResult`, `core.tools.parse_tool_call`,
  `core.tools._default_parser`) and the `parse_or_recover` →
  `core/structured.py` extraction are part of the deferred protected-path
  batch — see "Scope revision" above and spec-0015.

## Backwards-compatibility

- `api/app.py` keeps its old import path as a permanent facade over the
  extracted `api/errors.py` / `api/models.py` / `api/routes/` modules
  (ADR-0019); the HTTP surface (routes, status mapping, error envelope) is
  unchanged and existing test suites pass unmodified.
- CLI flags, exit codes (0/1/2/3), and the console-script entry point
  `mangomas.cli.main:app` are unchanged (that module is not decomposed in
  this PR — see "Scope revision").
- `mangomas.config`, `mangomas.telemetry`, and `mangomas.core.tools` are not
  decomposed in this PR, so no facade is needed for them yet; a
  `tests/test_import_compat.py` identity test is deferred to spec-0015
  alongside the decompositions it would cover.
- Deliberate behaviour changes, each CHANGELOG'd: D1 (tool prompt now
  concatenated with a custom system prompt), D9 (`max_tool_steps` is now an
  exact LLM-call cap), eval option validation unified on the strict side,
  kw-only constructors for `AgentTarget`/`PipelineTarget`/`InlineSource`/
  `JsonlSource`, planner/reviewer gain fenced-JSON recovery.

## Test plan

- Unit: per-defect regression tests named in the delivery plan
  (`docs/plans/20260730T210645Z-code-hygiene-modularity-plan.md`); CI↔Makefile
  contract tests in `tests/deploy/test_ci_make_parity.py`; fakes extended in
  `tests/fakes.py` (`make_orchestrator`, `_NamedChat`, entry-point doubles).
  `tests/test_import_compat.py` is deferred to spec-0015 with the
  decompositions it would cover.
- Gated: no new external-SDK behaviour; existing `RUN_*` suites unchanged.
- Coverage: maintain the 95% global gate and all per-package floors; new
  floors added for `_headers.py` (100%), `config.py`, `telemetry.py`, and
  `metrics.py` (95% each).

## Acceptance criteria

- [x] All D1–D12 regression tests present and green.
- [x] Duplicate-cluster greps return a single definition site each for the
      in-scope clusters (R2, minus the deferred `core/structured.py`).
- [x] `ruff` reports zero C901; `mypy --strict` clean; frontmatter lint clean.
- [x] `pytest` (95% gate) + `scripts/check_coverage.py` floors green at every
      milestone commit.
- [x] Every `AgentSettings` field consumed or documented as reserved.
- [x] CHANGELOG updated; ADR-0019 Accepted; CLAUDE.md module map synced.
- [ ] Oversized-module decomposition (`cli/main.py`, `config.py`,
      `telemetry.py`) and the protected-path `core/structured.py` batch —
      deferred to spec-0015, not part of this PR's acceptance.
