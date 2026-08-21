# ADR-0019: Backwards-compatible package decomposition via permanent re-export facades

## Status

Accepted — proven by the `api/app.py` decomposition (spec-0014 / M11):
`api/errors.py`, `api/models.py`, and `api/routes/{system,agents,workflows}.py`
now hold the split-out logic, `app.py`'s previously-importable names
(`_ERROR_STATUS`, `error_envelope`, …) are re-exported permanently, the HTTP
surface is byte-identical (OpenAPI schema diffed), and no consumer import
changed. The remaining decompositions this ADR scopes —
`cli/main.py`, `config.py`, `telemetry.py`, and the protected-path
`core/tools.py` / `errors.py` batch — are deferred to a follow-up PR
(spec-0015) rather than bundled into this one; see that spec for the
up-to-date remaining scope.

Three of those four have since landed under spec-0015: `config/` (12 domain
modules), `telemetry/` (6 dependency layers) and `cli/` (a command package
behind `cli/main.py`). The protected-path `core/tools.py` / `errors.py` batch
remains deferred. Applying the pattern three more times surfaced one thing the
"Neutral" note below did not anticipate — see **Amendment** at the end.

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

## Amendment — what a facade does *not* preserve

Recorded after applying the pattern to `config/`, `telemetry/` and `cli/`,
because the original "existing tests pass unmodified and thereby prove the
facades" claim is true only for a partition and misleading for anything else.

**A facade preserves object identity, not module-global name binding.**
`monkeypatch.setattr(facade, "helper", ...)` rebinds the name in the facade
alone; a submodule that calls `helper()` through its own globals never sees it.
So a patch seam is not carried across a split by the facade — it has to be
converted, by making the patched callable reachable through a *module object*
(`_runtime._build()`, `exporters._build_span_exporter(...)`) so one patch point
still reaches every consumer.

Three splits, three different shapes, and the difference is what decides how
much work each needs:

| Split | Shape | Seam sites | Consequence |
|---|---|---|---|
| `config/` | partition — domains genuinely disjoint | 0 | suite passed unmodified |
| `telemetry/` | layered DAG over shared mutable state | 6 (1 silent) | needed a pre-split safety commit |
| `cli/` | one root plus four command groups | 15 (**13 silent**) | needed a guard landed first |

"Silent" is the load-bearing word: a test whose patch does not reach its
consumer keeps passing, because it asserts something the *real* system also
produces. Green is not evidence the facade worked. Convert the seam and prove
the conversion — with a mutation, not an assertion — before moving any code.

`tests/test_import_compat.py` is where the identity half of the contract lives,
and its `_PRIVATE_FACADE_CONTRACT` exists for exactly this reason: the names a
seam depends on are private, so the public-surface tests cannot see them.

## Alternatives Considered

- **Deprecation-warning shims** — rejected: there are no external consumers to
  migrate, so warnings are pure noise.
- **Big-bang import rewrite** — rejected: hundreds of churned lines, destroys
  `git blame`, and turns mechanical refactors into risky ones.

## References

- Code: `src/mangomas/config/`, `src/mangomas/telemetry/`,
  `src/mangomas/cli/` (all three landed), `src/mangomas/core/tools.py`
  (deferred)
- Contract: `tests/test_import_compat.py`, `tests/test_cli_surface.py`,
  `tests/_seam_guards.py`
- Related: Spec-0014; `scripts/lint_agent_frontmatter.py` (protected paths)
- Related ADRs: ADR-0011 (workflow layering), ADR-0015 (backpressure)
