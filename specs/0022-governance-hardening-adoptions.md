# Spec-0022: Governance-hardening adoptions (SSD-pack Tier 1 + 2)

- **Status:** Implemented
- **Linked ADR:** _none — no boundary change_
- **Linked CHANGELOG entry:** `[Unreleased]` › `<Added|Changed|Fixed>`

## Problem

`docs/analysis/20260822-ssd-template-pack-analysis.md` reviewed an external
governance-template pack against this repo and surfaced (a) verified defects —
a history-only gitleaks scan that misses working-tree secrets, event-payload
interpolation in the credentialed deploy job, no SHA-pinned third-party
actions, and a SessionStart hook whose module-scope imports break its
always-exit-0 contract on a bare interpreter — and (b) governance mechanisms
this repo lacks while already having everything needed to enforce them. This
spec adopts the analysis's Tier 1 + Tier 2 roadmap; Tier 3 (spec-traceability
lint, native pre-push hook, `gate-full`, `pip-audit`) stays deferred behind a
future ADR/decision, and ADR-0021's pre-commit-wiring prose drift is
explicitly out of scope.

## Requirements

Tier 1 — defect fixes and no-conflict hardening:

- R1: `make secret-scan` runs both gitleaks passes — `dir` (working tree) and
  `git` (history) — and no longer uses the deprecated history-only `detect`.
- R2: no workflow `run:` body interpolates `${{ github.event.* }}`,
  `${{ github.head_ref }}`, or `${{ secrets.* }}`; `deploy.yml` binds its
  release tag and project id through `env:` (the `eval-gate.yml` idiom).
- R3: `scripts/harness_session_start.py` returns exit 0 on a bare interpreter
  (no venv, no `httpx`, no `mangomas`), degrading its probes with a warning.
- R4: `permissions.deny` blocks the MCP filesystem-write and git-mutation
  tools, closing part of ADR-0021's conceded MCP bypass at the permission
  layer.
- R5: third-party GitHub Actions (`codecov/codecov-action`,
  `google-github-actions/*`) are pinned to full commit SHAs, with Dependabot
  configured for the `github-actions` ecosystem as the bump mechanism;
  first-party `actions/*` stay tag-pinned by policy.
- R6: `pytest-cov` and `coverage` are exact-pinned in the dev extra so the
  coverage denominator cannot drift between environments.

Tier 2 — adapted mechanisms:

- R7: a subprocess meta-test proves the conftest collection gate actually
  skips/unskips and that the zero-skip guard fires.
- R8: an escalate-only session guard fails an otherwise-green run on any
  unsanctioned skip, xfail, or xpass; the nine env-gate reasons are
  single-sourced in `tests/constants.py`.
- R9: `specs/TEMPLATE.md` offers an optional WHEN/THEN Scenarios section with
  the both-directions fail-closed rule.
- R10: `mango-architect` carries an adversarial-review protocol (severity
  enum, confidence tags, 2-fix-cycle cap, red-stage mode for tests-only
  diffs).
- R11: the PreToolUse hook also inspects Bash `tool_input.command` for
  protected-path mentions and emits an advisory `ask` (never deny, never a
  non-zero exit).
- R12: every `Settings` field is documented in CLAUDE.md's config tables, and
  a reverse drift test keeps it that way.
- R13: `docs/plans/` gains a `_template.md`.
- R14: three contract tests pin asserted-but-untested behavior: an OpenAPI
  normalized-projection snapshot, the mid-stream SSE truncation contract (no
  `done`, no invented error frame), and an exhaustive
  `MangomasError`-subclass → intended-HTTP-status walk.
- R15: CLAUDE.md's Key Design Rules table gains an "Enforced by" column.

All changes are additive to runtime behaviour: no `mangomas` package code
changes except none at all — the production package is untouched (scripts,
tests, workflows, config docs, and the Claude Code corpus only).

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| _none_ | — | — |

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_ (the error walk records intended statuses, including
  `DatasetError`'s deliberate root-mapped 500, without changing
  `_ERROR_STATUS`)
- Registry additions: _none_

## Backwards-compatibility

- `src/mangomas/` is byte-identical; no protected path is touched.
- The zero-skip guard is escalate-only: it can turn a green run red on an
  unsanctioned outcome but never masks a failing run, and the nine existing
  env-gated suites remain sanctioned verbatim.
- The new PreToolUse Bash check is advisory (`ask`) and exits 0 always;
  `scripts/check_protected_paths.py` in CI remains the authoritative gate
  (ADR-0021 unchanged).
- The MCP deny rules remove no capability that has a sanctioned use: file
  edits and git mutations already flow through the audited native tools.

## Test plan

- Unit: `tests/deploy/test_workflow_hardening.py` (new),
  `tests/deploy/test_ci_make_parity.py` (two-pass assertion),
  `tests/test_harness_session_start.py` (bare-interpreter subprocess),
  `tests/test_lint_agent_frontmatter.py` (Bash-command advisory),
  `tests/tooling/test_claude_code_settings.py` (deny rules, hook roster),
  `tests/tooling/test_collection_gate.py` (new; subprocess gate proof),
  `tests/test_openapi_snapshot.py` (new), `tests/test_streaming.py`
  (mid-stream truncation), `tests/test_errors.py` (status walk),
  `tests/deploy/test_env_example_contract.py` (reverse config-doc drift).
- Gated: none — every new test runs offline in the default suite.
- Coverage: 95% global gate and per-package floors unchanged;
  `make scripts-coverage` re-checked after the `scripts/` edits (84 floor).

## Acceptance criteria

- [x] Working-tree secret is caught: `make secret-scan` runs `gitleaks dir`
      and `gitleaks git`; parity test proves `detect` is gone.
- [x] No workflow `run:` body interpolates event payload or secrets
      (test proves it, and fails if reintroduced).
- [x] `harness_session_start.py` exits 0 with `httpx`/`mangomas` absent
      (subprocess test proves it).
- [x] Default `pytest -q` run is green with all nine gated suites sanctioned;
      an ad-hoc skip/xfail turns it red (meta-test proves both directions).
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; no ADR needed (no boundary changed).

> Acceptance adjudicated 2026-08-22 against the shipped tree (roadmap Phase 0.4); unchecked boxes remain genuinely open.
