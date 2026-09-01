# Code hygiene & modularity overhaul — delivery plan

- **Branch:** `claude/code-hygiene-modularity-3r4umt`
- **Date:** 2026-07-30
- **Target release:** `[Unreleased]` → next minor
- **Status:** Delivered — PR #24 merged (`specs/0014-code-hygiene-modularity.md`,
  `CHANGELOG.md`). M0–M9, M11, M13–M15 landed as planned. M10 (`cli/` split)
  and M12(b)/(c) (`telemetry/`, `config/` packages) were descoped
  mid-execution to spec-0015 and landed there 2026-08-22 — see
  `docs/plans/20260809T143356Z-harness-corpus-decomposition-plan.md` and
  `specs/0015-package-decomposition.md` for that record. M12(a)
  (`_HarnessOrchestrator` → a standalone `mangomas/harness.py`) did not land
  as described: `_HarnessOrchestrator` instead moved into the `composition/`
  package's own `harness.py` submodule via a later, unrelated decomposition
  (2026-08-30, tracked only in `CHANGELOG.md`'s `[Unreleased]` section).
- **Spec:** `specs/0014-code-hygiene-modularity.md` · **ADR:** ADR-0019

## Executive Summary

A five-part audit (four domain sweeps plus a quantitative pass over function
lengths, complexity, git churn, and layering) found twelve verified defects,
~100 lines of dead code, three dead `AgentSettings` fields, ten measured
duplication clusters, four oversized modules, ~400 redundant test-suite
lines, and CI↔Makefile drift. This plan lands all of it as ~18–20
independently-green commits on a single branch, delivered as one draft PR.
Every decomposition keeps its old import path via a permanent re-export
facade (ADR-0019); the only deliberate behaviour changes are defect fixes,
each with a regression test and a CHANGELOG entry. Parallelisable milestones
are developed in detached-HEAD worktrees and cherry-picked onto the branch in
landing order; `make gate` must pass at every landing.

## Milestone M0 — Scaffolding

Spec-0014, ADR-0019 (Proposed), this plan document, CHANGELOG `[Unreleased]`
scaffold. Draft PR opened; PR-activity subscription engaged.

## Milestone M1 — Defect fixes: agents (D1, D9)

`ToolAgent` concatenates a custom system prompt with the tool-format prompt
instead of discarding the latter; the step loop honours `max_tool_steps`
exactly; `DEFAULT_TOOL_MAX_STEPS` + `AgentSettings.max_tool_steps`
(`MANGOMAS_AGENTS__<NAME>__MAX_TOOL_STEPS`) wired through `composition.py`.
Tests: prompt concatenation, counting-fake call cap, env wiring.

## Milestone M2 — Defect fixes: adapters/infra (D2, D7, D10)

Error-detail truncation (`DEFAULT_ERROR_DETAIL_TRUNCATE`) applied in the LM
Studio LLM/embeddings adapters and the SQLite repository; lock added to the
lazy metrics singleton; `IngestReport.deleted_sources` counts real deletions.

## Milestone M3 — Defect fixes: eval (D3, D4, D8, D11, D12)

Scorer options validated at factory time (config errors exit 2, not N row
errors); `sqlite:///` URL normalisation shared via `adapters/storage/_url.py`
and used by the `sqlite_results` sink; eval discovery drops its import-time
tracer, adopts the locked per-registry latch, and both discovery modules gain
a `callable(factory)` guard; `merge_gate_results` made order-independent;
`tests/integration/__init__.py` added.

## Milestone M4 — Defect fixes: tooling (D5, D6)

`make rag` gains `--no-cov`; `run_workflow_e2e.py` closes the orchestrator in
a `finally`.

## Milestone M5 — Agents dedup + config wiring

`agents/_prompt.py` (system-prompt resolution + message building, collapsing
five/four duplicated sites), `agents/_structured.py`
(`StructuredOutputAgent` collapsing planner/reviewer), and activation of
`AgentSettings.temperature` / `max_tokens` (additive kw-only protocol param);
`model_override` documented as reserved.

## Milestone M6 — Adapters/secrets dedup

`OpenAICompatHTTPClient._post_json` collapses four POST→log→translate sites;
`secrets/gcp.py::get` rewritten as an exception matrix with the missing SDK
install hint; `event=` log-envelope and `logger.exception` unification.

## Milestone M7 — Eval dedup

`eval/_langfuse.py`, `eval/_options.py` (strict-side option validation),
gate-result construction collapse, shared `mangomas/_entry_points.py`,
kw-only constructors for the four positional eval classes, unused loggers
removed.

## Milestone M8 — Protected batch

Single `BREAKING-CHANGE`-marked commit over `core/tools.py` + `errors.py`:
new `core/structured.py` (`build_structured_prompt`, `_extract_json_span`,
`parse_or_recover`, `parse_llm_json_object`); dead symbols removed; llm_judge
and `StructuredOutputAgent` adopt the shared helpers; re-exports preserved.

## Milestone M9 — Misc src dedup

`mangomas/_headers.py` (shared header sanitiser), `FileMemoryRepository`
close-state honoured, `workflow/nodes/_factory.register_node`, span-scope
fixes in fan_out/sequence nodes.

## Milestone M10 — cli/ split

`cli/{main,eval,rag,workflow,_runtime}.py`; `orchestrator_session()`; shared
verbose callback; config-error→exit-2 context manager; new
`.github/agents/api-dev/cli-dev.agent.md` sub-agent. CLI tests unmodified.

## Milestone M11 — api/ split

`api/routes/{system,agents,workflows}.py`, `api/errors.py` (+ shared
`error_envelope()` used by middleware), `api/models.py`; middleware docstring
rewrite, `_emit_access_log` extraction, `HTTPStatus` constants. Removes the
repo's only C901 violation. API tests unmodified.

## Milestone M12 — Central splits

(a) `_HarnessOrchestrator` → `mangomas/harness.py`; (b) `telemetry/` package
with `_exporters.py` dedup; (c) `config/` package (`defaults.py`,
`models.py`, `settings.py`) — last, since everything imports it. Facades +
`dir()`-diff shim tests; `mango-config`/`mango-observability` skill bodies
updated.

## Milestone M13 — Test-suite consolidation (+M13b)

Root `runner`/`client` fixtures, `make_report()` factory, fakes additions,
`test_cli_close.py` collapse, provider-neutral `tests/e2e_helpers.py`,
redundant fixture/finally deletions, constants cleanup. M13b (own commit):
flat-root migration via `git mv` + `test_composition.py` split.

## Milestone M14 — Tooling/CI

CI invokes `make` targets; `tests/deploy/test_ci_make_parity.py` contract
tests (floors 95×2 / 100×2, `--no-cov` guard); `check_coverage.py` docstring
fix + floors for `config/`, `telemetry/`, `metrics.py`, `harness.py` +
`api/**`/`cli/**` patterns; new `gcp-secrets`/`gcp-trace`/`langfuse`
targets; `COVERAGE_FILE=.coverage.bridge`; pre-commit frontmatter hook;
optional final ruff `TC` commit.

## Milestone M15 — Docs/harness finale

CLAUDE.md error-table + module-map sync; spec acceptance boxes checked;
ADR-0019 → Accepted; CHANGELOG polish; PR flipped draft→ready and driven to
merge.

## Sequential thinking — execution order

M0 → {M1, M2, M4, M9, M10, M11 in parallel worktrees} → {M5, M6, M7} → M8 →
M12a→b→c → M13 (→ M13b) → M14 → M15. Hard serial: M3 after M2 (shared
`sqlite.py`); M8 after M5+M7; M12 after all source tracks; M13/M14/M15 tail.

## Open Questions

None — behaviour-fix semantics, config wiring, scope, and delivery shape were
confirmed before execution began.
