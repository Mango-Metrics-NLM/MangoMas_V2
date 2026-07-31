# ADR-0019: Backwards-compatible package decomposition via permanent re-export facades

## Status

Proposed

## Context

Spec-0014 decomposes four oversized modules (`cli/main.py`, `config.py`,
`telemetry.py`, and the route closures inside `api/app.py`) and relocates
shared helpers out of `core/tools.py` and `composition.py`. The codebase has
in-repo consumers of every one of those import paths (plus a console-script
entry point pinned to `mangomas.cli.main:app`), and the project's contract
rules require that existing imports keep working without a migration.

## Decision

Each decomposed module becomes a package (or keeps a slim module) whose old
import path re-exports the full prior public surface **permanently** — no
deprecation window, no warnings. Protected-path edits (`core/tools.py`,
`errors.py`) are batched into a single commit carrying the `BREAKING-CHANGE`
marker. `core/` stays config-free: shared parsing helpers in
`core/structured.py` take a parameterised `detail_truncate` instead of
importing `config`.

## Consequences

### Positive

- Zero-churn migration: no import in `src/`, `tests/`, or downstream code
  changes; existing tests pass unmodified and thereby prove the facades.
- `tests/test_import_compat.py` pins facade identity (`old is new`), giving
  the facades coverage and preventing silent drift.
- One `BREAKING-CHANGE` ceremony instead of one per touched protected file.

### Negative / Trade-offs

- Facades are permanent surface area; each new public name must be added to
  the facade as well as its home module.
- `scripts/check_coverage.py` floor patterns must track the new directory
  shapes (enforced by the CI↔Makefile contract test).

### Neutral

- A package shadowing its former module name (`config/`, `telemetry/`) makes
  the facade automatic; flat splits (`cli/`) need explicit named re-exports.

## Alternatives Considered

- **Deprecation-warning shims** — rejected: there are no external consumers to
  migrate, so warnings are pure noise.
- **Big-bang import rewrite** — rejected: hundreds of churned lines, destroys
  `git blame`, and turns mechanical refactors into risky ones.

## References

- Code: `src/mangomas/config.py`, `src/mangomas/telemetry.py`,
  `src/mangomas/cli/main.py`, `src/mangomas/core/tools.py`
- Related: Spec-0014; `scripts/lint_agent_frontmatter.py` (protected paths)
- Related ADRs: ADR-0011 (workflow layering), ADR-0015 (backpressure)
