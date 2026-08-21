# Spec-0015: Package decomposition (deferred spec-0014 scope)

- **Status:** In progress
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

**Corpus pointers this breaks (recorded 2026-08, spec-0018).** Twelve live
corpus files cite `config.py` and/or `telemetry.py` by path — six agents
(`mango-backend`, `mango-llm-adapter-dev`, `mango-orchestrator-dev`,
`mango-storage-adapter-dev`, `mango-telemetry-exporter-dev`,
`mango-workflow-graph-dev`) and six skills (`mango-adapter`,
`mango-agent-add`, `mango-config`, `mango-deploy`, `mango-observability`,
`mango-rag`). Turning either module into a package invalidates all twelve.
They must be updated **in the PR that does the split**, not after.

**Amendment — the premise here was false.** This paragraph originally asserted
that "the corpus pointer test is deliberately strict (a symbol must resolve in
the named file)". No such test exists. `tests/tooling/test_corpus_contract.py`
has no assertion that resolves a cited source path or symbol; its only
path-existence check concerns the retired `.github/` tree. The requirement was
therefore enforced by review discipline alone.

Two things follow. First, the corpus files must still be updated in the
splitting PR — the requirement stands, only its stated enforcement was wrong.
Second, new corpus content should cite settings by **dotted import path**
(`mangomas.config`), which survives the split by construction; spec-0019's
agents already follow this, so they need no edit here.

- `src/mangomas/cli/main.py` — 708 lines, the CLI's `chat`/`history`/`eval`/
  `rag`/`workflow` command groups plus `orchestrator_session()` all in one
  module.
- `src/mangomas/config.py` — 593 lines and the repo's #1 churn file by commit
  count; every `*Settings` group lives in one file.
- `src/mangomas/telemetry.py` — 337 lines; OTel tracer/meter setup, exporter
  selection, and the harness span helpers are not yet separated.
- `src/mangomas/core/tools.py` + `src/mangomas/errors.py` (protected paths) —
  dead code (`ToolResult`, `_default_parser`, never referenced in `src/`)
  plus the `parse_or_recover` JSON-recovery helper that
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
  (`ToolResult`, `_default_parser`) is deleted.
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
  full HTTP surface are unchanged.

### Amendment — the acceptance bar, corrected

The original bar read "existing test suites must pass unmodified; a test
needing edits to keep passing means the facade is incomplete." That is too
strong, and stating it unqualified would fail the phase for a reason that is
not a facade defect.

**A facade preserves object identity, not module-global name bindings.** After
a split, `monkeypatch.setattr(cli_main, "_build", ...)` rebinds the name in the
*facade* module, but a command living in `cli/commands/chat.py` resolves
`_build` through its own module globals and never sees the patch. The test is
not asserting a broken facade; it is asserting a seam that moved.

There are **21 such sites**: 15 CLI (`tests/test_cli.py`, `test_cli_rag.py`,
`test_workflow_cli.py`, `tests/eval/test_cli_eval.py`) and 6 telemetry
(`tests/test_telemetry.py`, `test_metrics.py`, `test_composition.py`).

The corrected bar:

- **Import-level compatibility is absolute** — every public name stays
  importable from its old path, and `tests/test_import_compat.py` proves
  `old is new` for each.
- **Patch seams are converted, not preserved by accident.** Before a split,
  the patched callable becomes an object attribute (or is looked up through
  one) so a single patch point still reaches every consumer. Where that is not
  practical, the affected test sites are updated in a **dedicated pre-split
  commit**, so the split commit itself is reviewable as a pure move.
- Behaviour, CLI flags, exit codes, the console-script entry point, and the
  HTTP surface stay byte-identical either way.

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
