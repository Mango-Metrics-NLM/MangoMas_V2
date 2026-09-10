# Spec-0017: Protected-path governance contract

- **Status:** Implemented
- **Linked ADR:** ADR-0021 (Protected-path governance: CI trailer gate)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed` / `Added`

## Problem

The repo documents that six core contracts (`core/agent.py`, `core/orchestrator.py`,
`core/structured.py`, `core/tools.py`, `errors.py`, `registry.py`) are protected: an edit must carry a
`BREAKING-CHANGE` marker. In practice this has never been enforced.

`.claude/settings.json`'s `PreToolUse` hook invokes
`lint_agent_frontmatter.py --check-protected-paths "$CLAUDE_TOOL_INPUT_path"` —
`$CLAUDE_TOOL_INPUT_path` is not an environment variable Claude Code defines (hook
input arrives as JSON on stdin), so the argument is always empty and the check is a
no-op. Independently, the script currently crashes on import (`pydantic` is not
guaranteed to be installed in the hook's execution context) and exits 1, which Claude
Code treats as non-blocking. And even a working version reads `git diff --staged`,
which is empty during a `PreToolUse` edit (the file is not staged yet), so a correct
implementation at this layer would block *every* protected edit rather than the
unmarked ones. Finally, the `PreToolUse` matcher (`"Edit|Write"`) is an exact-match
alternation and does not cover `Bash` or MCP filesystem writes, so no PreToolUse-layer
check can be a complete gate regardless of its internal correctness.

There is also no backstop: `--check-protected-paths` is wired in exactly one place in
the whole repo (`.claude/settings.json`). It is not run in CI, in `make gate`, or by
pre-commit.

This spec makes the *authoritative* enforcement point a CI job that reads committed
history — the one thing an in-session agent cannot rewrite — and demotes the
PreToolUse hook to an advisory prompt for the human.

## Requirements

- R1 — A new `make protected-paths` target and CI job compute
  `git diff --name-only $BASE_REF...HEAD` against the protected-path set and require
  a `BREAKING-CHANGE` trailer in at least one commit message
  (`git log --format=%B $BASE_REF..HEAD`) on any branch that touches one. Fails the
  build (non-zero) otherwise. Intended to be registered as a required status check —
  that registration is a GitHub branch-protection setting, a repo-admin action no
  file in this repo can express or verify; see `NEXT_STEPS.md`'s outstanding-actions
  list.
- R2 — The protected-path set moves to `pyproject.toml` under
  `[tool.mangomas.governance]` (stdlib `tomllib`, no new dependency) so both the CI
  script and the hook read one definition without importing `mangomas.harness` (which
  would require the package to be installed for a hook that must run before
  `pip install -e .` has happened).
- R3 — `lint_agent_frontmatter.py` gains a stdlib-only `--hook pre-tool-use` mode that
  reads the tool-call JSON from stdin, extracts `tool_input.file_path`, and — for a
  protected path — emits an **advisory** `permissionDecision: "ask"` JSON response
  (exit 0) naming the path and the trailer requirement, rather than attempting to
  block. `pydantic`/`yaml` imports are deferred into the schema-lint code path so the
  hook mode has no import-time dependency on either.
- R4 — The same stdin-JSON fix applies to the `PostToolUse` ruff-autofix hook, which
  has the identical `$CLAUDE_TOOL_INPUT_path` defect.
- R5 — `.claude/settings.json`'s `PreToolUse` matcher widens to
  `Edit|Write|NotebookEdit` (still exact-match; MCP filesystem coverage is out of
  scope for a first pass and is called out as a known gap in the ADR).
- R6 — `_HarnessOrchestrator.stream_dispatch` (`composition.py`) is fixed so its span
  actually covers token emission: today it opens a span and returns an unconsumed
  async generator (`core/orchestrator.py`'s `stream_dispatch` never yields, it
  returns one), so the span closes at iterator construction. The fix must not hold
  OTel context across a `yield` (this leaks the harness span into every span the
  consumer subsequently creates) and must end the span on early consumer
  abandonment (client disconnect), not only on full drain.
- R7 — `src/mangomas/harness/` is created, porting `main`'s `governance.py` (the
  `PROTECTED_PATHS`/marker constants, relocated here so R1-R3's logic is coverage-visible
  and shared by both the PreToolUse hook and the new `ConfigChange` hook) and
  `config_audit.py` (the `ConfigChange` decision logic). `main`'s `coverage.py` and
  `scripts/harness_stop_gate.py` are explicitly **not** ported: `read_coverage_floor`
  parses `--cov-fail-under` out of `pyproject.toml` and feeds it back to a pytest run
  whose addopts already set that value — a tautology on this branch, where
  `scripts/check_coverage.py` is already the documented single source of truth and
  `tests/deploy/test_ci_make_parity.py` already asserts the addopts/floor match.
- Must remain **additive**: the legacy `--check-protected-paths <path>` flag stays for
  pre-commit / manual use, unchanged in behaviour.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_HARNESS__CONFIG_AUDIT_MODE` | `off` | `ConfigChange` hook mode: `off`\|`audit`\|`block` |

No `MANGOMAS_*` var governs the CI gate or the PreToolUse advisory hook — those are
Claude Code / CI configuration, not application runtime behaviour, consistent with
spec-0016's stance that pure dev-tooling config stays out of `Settings`.

## Protocol / contract impact

- No protocol signatures change.
- No new error types.
- No registry additions.

## Backwards-compatibility

- `--check-protected-paths <path>` (used by pre-commit) is unchanged.
- The PreToolUse hook's new behaviour (`"ask"` instead of a broken no-op `"allow"`) is
  strictly more protective than today, and never blocks — a human can always approve.
- `ConfigChange` hook defaults to `off` (today's exact behaviour: no such hook existed).

## Test plan

- Unit: `tests/test_lint_agent_frontmatter.py` extended for `--hook pre-tool-use`
  (subprocess-level: `subprocess.run([sys.executable, "scripts/lint_agent_frontmatter.py",
  "--hook", "pre-tool-use"], input=..., env=<clean>)`), asserting exit code, stdout
  JSON shape, and that the check has no import-time `pydantic` dependency.
- New `tests/test_check_protected_paths.py` for the CI script's trailer-detection logic.
- New `tests/harness/` for `governance.py` + `config_audit.py`.
- `tests/test_composition.py` extended: an `InMemorySpanExporter`-based assertion that
  the consumer's `trace.get_current_span()` is unchanged between streamed chunks, and
  that the span ends both on full drain and on early `aclose()`.
- `tests/deploy/test_ci_make_parity.py` gains a job-delegation test for
  `protected-paths`.
- `tests/tooling/test_claude_code_settings.py`'s `PREEXISTING_HOOKS` constant updates
  to the new hook commands (three of four entries change).
- Coverage: maintain the 95% global gate; new `harness` package floor (see ADR-0021).

## Acceptance criteria

- [x] `make protected-paths` fails on a branch that edits a protected path without a
      `BREAKING-CHANGE` trailer, and passes when the trailer is present.
      (`tests/test_check_protected_paths.py`)
- [x] The PreToolUse hook returns a valid JSON `"ask"` decision for a protected path
      via stdin, with no import-time crash — verified against a bare interpreter with
      no `pydantic` installed at all
      (`tests/test_lint_agent_frontmatter.py::test_subprocess_hook_mode_runs_without_pydantic_installed`).
- [x] A streaming request with the harness enabled shows a `harness.agent_invoke`
      span whose duration covers the last emitted token, and the consumer's current
      span is unaffected between chunks
      (`tests/test_composition.py::test_harness_stream_span_*`).
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean — `make gate`
      green, including the two new `protected-paths` and `scripts-coverage` gates.
- [x] CHANGELOG updated; ADR-0021 accepted.
