# Spec-0015: Package decomposition (deferred spec-0014 scope)

- **Status:** Draft
- **Linked ADR:** ADR-0019 (re-export facade decomposition — Accepted, proven
  by the `api/app.py` split; this spec applies the same pattern to the
  remaining oversized modules)
- **Linked CHANGELOG entry:** _none yet — this is a stub written to record
  scope, not a landed change_

## Problem

Spec-0014's original scope covered decomposing every oversized module in the
codebase, but that PR (#24) was flipped to ready-for-review partway through
execution to keep it mergeable. The defect fixes and low-risk deduplication
milestones landed; the churnier structural splits below did not, and are
recorded here so they aren't lost:

- `src/mangomas/cli/main.py` — 708 lines, the CLI's `chat`/`history`/`eval`/
  `rag`/`workflow` command groups plus `orchestrator_session()` all in one
  module.
- `src/mangomas/config.py` — 593 lines and the repo's #1 churn file by commit
  count; every `*Settings` group lives in one file.
- `src/mangomas/telemetry.py` — 337 lines; OTel tracer/meter setup, exporter
  selection, and the harness span helpers are not yet separated.
- `src/mangomas/core/tools.py` + `src/mangomas/errors.py` (protected paths) —
  dead code (`ToolResult`, `parse_tool_call`, `_default_parser`, never
  referenced in `src/`) plus the `parse_or_recover` JSON-recovery helper that
  spec-0014 planned to extract into a new `core/structured.py`, shared by
  `eval/scorers/llm_judge.py` and the planner/reviewer structured-output path.
  Both files are protected by `scripts/lint_agent_frontmatter.py` and require
  a `BREAKING-CHANGE` marker on any staged diff — a real backwards-compat
  audit, not a mechanical split, so it does not belong in the same PR as
  routine dedup work.

## Requirements

- R1 — `cli/main.py` decomposes into command modules (one per Typer sub-app
  group) plus a shared `orchestrator_session()` context manager, with
  `mangomas.cli.main` re-exporting the assembled Typer `app` so the
  `mangomas.cli.main:app` console-script entry point is unaffected.
- R2 — `config.py` becomes a `config/` package; `mangomas.config` re-exports
  every `*Settings` class and `DEFAULT_*` constant so no import in `src/` or
  `tests/` changes.
- R3 — `telemetry.py` becomes a `telemetry/` package on the same terms.
- R4 — `core/structured.py` hosts `build_structured_prompt`, a single
  `_extract_json_span`, the relocated `parse_or_recover`, and a new
  `parse_llm_json_object(text, *, detail_truncate=200)` — a **core-local**
  constant default, since `core/` must not import `config` (protocol-first
  layering rule). `eval/scorers/llm_judge.py` and the planner/reviewer
  structured-output path adopt the shared helpers. Dead code
  (`ToolResult`, `parse_tool_call`, `_default_parser`) is deleted.
  `core/tools.py` and `errors.py` land in one commit carrying the
  `BREAKING-CHANGE` marker required by the protected-path lint hook.
- Must remain **additive & default-OFF where behaviour changes are optional**;
  R4's dead-code removal and helper relocation must not change any observable
  return value — only import paths move.

## Config / env additions

None — this is a structural refactor, not a new tunable.

## Protocol / contract impact

- No protocol signatures change. `core/tools.py`'s public re-exports
  (`ToolCallParser`, `ToolRegistry`, `ToolSpec`, `build_tool_system_prompt`,
  `parse_or_recover`) stay importable from the same path.
- Removed (dead, never referenced in `src/`): `core.tools.ToolResult`,
  `core.tools.parse_tool_call`, `core.tools._default_parser`.

## Backwards-compatibility

- Every decomposed module keeps its old import path via a permanent facade
  (`mangomas.config`, `mangomas.telemetry`, `mangomas.cli.main`,
  `mangomas.core.tools`); a new `tests/test_import_compat.py` asserts facade
  identity (`old is new`) for every re-exported name.
- CLI flags, exit codes (0/1/2/3), the console-script entry point, and the
  full HTTP surface are unchanged; existing test suites must pass unmodified
  in the decomposition commits (that's the acceptance bar — a test needing
  edits to keep passing means the facade is incomplete).

## Test plan

- Unit: `tests/test_import_compat.py` (new); existing `tests/test_cli*.py`,
  `tests/test_composition.py`, `tests/test_telemetry.py`,
  `tests/test_tools.py` pass unmodified.
- Coverage: maintain the 95% global gate; `scripts/check_coverage.py` floor
  patterns updated for the new `config/`, `telemetry/`, `cli/` directory
  shapes (locked by `tests/deploy/test_ci_make_parity.py`, per ADR-0019's
  "Negative" consequence).
- Full `make gate` green at every milestone commit before push (mirrors
  spec-0014's integration protocol).

## Acceptance criteria

- [ ] `cli/main.py`, `config.py`, `telemetry.py` decomposed with permanent
      re-export facades; `tests/test_import_compat.py` proves identity.
- [ ] `core/structured.py` extracted; `core/tools.py` + `errors.py` land in
      one `BREAKING-CHANGE`-marked commit; dead code removed.
- [ ] `ruff`, `mypy --strict`, `frontmatter-lint`, `pytest` (95% gate +
      per-package floors), bridge coverage all clean.
- [ ] CHANGELOG updated; this spec's status moves to Implemented.
