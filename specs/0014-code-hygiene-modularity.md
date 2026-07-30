# Spec-0014: Code hygiene & modularity overhaul

- **Status:** In progress
- **Linked ADR:** ADR-0019 (re-export facade decomposition)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed` / `Changed` / `Added` / `Removed`

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
  `eval/_options.py`, `adapters/_openai_client._post_json`,
  `mangomas/_entry_points.py`, `mangomas/_headers.py`, `core/structured.py`).
- R3 — oversized modules decomposed into packages with **permanent re-export
  facades** at the old import paths (ADR-0019); existing tests pass unmodified.
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
- Removed (dead, never referenced in `src/`): `core.tools.ToolResult`,
  `core.tools.parse_tool_call`, `core.tools._default_parser`.
  `parse_or_recover` moves to `core/structured.py` (re-exported from
  `core.tools`).

## Backwards-compatibility

- Every decomposed module keeps its old import path via a permanent facade
  (`mangomas.config`, `mangomas.telemetry`, `mangomas.cli.main`,
  `mangomas.core.tools`); `tests/test_import_compat.py` asserts identity.
- CLI flags, exit codes (0/1/2/3), the console-script entry point
  `mangomas.cli.main:app`, and the HTTP surface (routes, status mapping,
  error envelope) are unchanged; existing test suites pass unmodified in the
  decomposition commits.
- Deliberate behaviour changes, each CHANGELOG'd: D1 (tool prompt now
  concatenated with a custom system prompt), D9 (`max_tool_steps` is now an
  exact LLM-call cap), eval option validation unified on the strict side,
  kw-only constructors for `AgentTarget`/`PipelineTarget`/`InlineSource`/
  `JsonlSource`, planner/reviewer gain fenced-JSON recovery.

## Test plan

- Unit: per-defect regression tests named in the delivery plan
  (`docs/plans/20260730T210645Z-code-hygiene-modularity-plan.md`); shim
  identity tests in `tests/test_import_compat.py`; CI↔Makefile contract tests
  in `tests/deploy/test_ci_make_parity.py`; fakes extended in
  `tests/fakes.py` (`make_orchestrator`, `_NamedChat`, entry-point doubles).
- Gated: no new external-SDK behaviour; existing `RUN_*` suites unchanged.
- Coverage: maintain the 95% global gate and all per-package floors; new
  floors added for `config/`, `telemetry/`, `metrics.py`, `harness.py`.

## Acceptance criteria

- [ ] All D1–D12 regression tests present and green.
- [ ] Duplicate-cluster greps return a single definition site each.
- [ ] `ruff` reports zero C901; `mypy --strict` clean; frontmatter lint clean.
- [ ] `pytest` (95% gate) + `scripts/check_coverage.py` floors green at every
      milestone commit.
- [ ] Every `AgentSettings` field consumed or documented as reserved.
- [ ] CHANGELOG updated; ADR-0019 Accepted; CLAUDE.md module map synced.
