# ADR-0011: Claude Code harness hook hardening

## Status

Accepted

## Context

`.claude/settings.json`'s `Stop` hook hardcoded `--cov-fail-under=85` while
`pyproject.toml` declares `95` and `ci.yml` overrides to `90` — three numbers
for one nominal gate, with nothing keeping them aligned. The `Stop` hook never
checked `stop_hook_active`, risking Claude Code's 8-consecutive-block
override. `BREAKING-CHANGE`/protected-path governance logic lived only in
`scripts/lint_agent_frontmatter.py`, which `pyproject.toml`'s
`source = ["mangomas"]` coverage scope makes permanently invisible to the 95%
gate. No hook audited `.claude/settings.json` edits, despite Anthropic's own
`security.md` recommending a `ConfigChange` hook for exactly that.

## Decision

Add a pure-domain `mangomas.harness` package (sibling of `rag`/`eval`/
`workflow`) holding coverage-floor parsing, relocated `BREAKING-CHANGE`/
protected-path governance (imported by, not duplicated in,
`scripts/lint_agent_frontmatter.py`), and the `Stop`/`ConfigChange` hook
decision functions. Two thin scripts (`harness_stop_gate.py`,
`harness_config_audit.py`) wire those decisions to hooks; both are opt-in via
new `HarnessSettings` fields (`stop_gate_mode`, `config_audit_mode`)
defaulting to today's exact behavior.

## Consequences

### Positive

- `pyproject.toml` is now the single source of truth for the coverage floor;
  `harness/coverage.py` reads it at runtime, and
  `tests/harness/test_coverage_consistency.py` guards it against
  `check_coverage.py`'s `GLOBAL_FLOOR` drifting apart again.
- Governance logic (marker, protected paths, staged-diff read) is shared by
  the `PreToolUse` and `ConfigChange` hooks from one module and gets real
  coverage-gate credit, unlike `scripts/`.
- `Stop` now respects `stop_hook_active`, avoiding the 8-block override.
- Zero observable behavior change by default — both new modes ship off/advisory.

### Negative / Trade-offs

- Two new hook-invoked Python processes add latency to every `Stop`/
  `ConfigChange` firing.
- `ConfigChange`'s payload field naming the changed source is undocumented
  upstream; `_extract_source` guesses candidate keys and fails open until
  confirmed against a live firing.
- `ci.yml`'s `--cov-fail-under=90` remains a third, unreconciled value —
  deliberately not touched here since raising it blind (without checking real
  coverage against a 95% floor) could break CI; tracked as a fast-follow.

### Neutral

- `permissions.deny` additions (`.env`/`secrets`/`curl`) are independent of
  the hook changes and enforced natively, not via a hook.
- This is single-repo hardening and groundwork for a future managed-settings
  rollout, not enterprise hook governance itself — that needs a Teams/
  Enterprise tier and admin console or MDM, neither of which this org has
  evidence of today.

## Alternatives Considered

- **`ConfigChange` guard over `pyproject.toml`** — rejected: the event only
  fires for Claude Code's own config sources, never arbitrary project files.
- **Staged-diff `BREAKING-CHANGE` bypass on `ConfigChange`** — rejected: the
  event fires on an in-session change that won't be in git's staging area.
- **Duplicate the marker/protected-path constants in a second module** —
  rejected: reintroduces the exact drift this change eliminates.
- **Leave governance logic in `scripts/`** — rejected: permanently invisible
  to the coverage gate.
- **Managed/enterprise settings distribution** — out of scope: no Teams/
  Enterprise tier or admin-console access for this org today.

## References

- Code: `src/mangomas/harness/{governance,coverage,config_audit}.py`,
  `scripts/harness_stop_gate.py`, `scripts/harness_config_audit.py`,
  `scripts/lint_agent_frontmatter.py`, `src/mangomas/config.py::HarnessSettings`,
  `.claude/settings.json`.
- Related ADRs: ADR-0007 (sibling-package precedent), ADR-0009 (exporter seam
  reused by `_HarnessOrchestrator`).
- External links: Claude Code `hooks.md` / `security.md` (`ConfigChange`
  recommendation, `stop_hook_active` block-cap behavior).
