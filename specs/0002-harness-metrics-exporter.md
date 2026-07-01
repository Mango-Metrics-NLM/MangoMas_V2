# Spec-0002: Harness metrics-exporter routing

- **Status:** Implemented (Milestone C)
- **Linked ADR:** ADR-0009
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Telemetry exporter selection`

## Problem

`NEXT_STEPS.md` › "Harness metrics-exporter selection": route the
`harness.agent_invoke` spans to a different exporter than application spans.
Today the harness tracer is only a differently-*named* tracer on the shared
global provider, so its spans cannot be rerouted by namespace alone.

## Requirements

- `MANGOMAS_HARNESS__METRICS_EXPORTER` selects `inherit` (default — reuse the
  global application exporter, no change), `console`, or `gcp`.
- When non-`inherit`, harness spans go to a dedicated exporter.

## Config / env additions

| Env var | Default | Purpose |
|---------|---------|---------|
| `MANGOMAS_HARNESS__METRICS_EXPORTER` | `inherit` | Harness span exporter (`inherit`/`console`/`gcp`) |

`DEFAULT_HARNESS_METRICS_EXPORTER` in `config.py`; new field on `HarnessSettings`.

## Protocol / contract impact

- No protocol change. New `build_scoped_tracer(namespace, *, exporter)` in
  `telemetry.py`; `_HarnessOrchestrator` uses it instead of the bare
  `get_tracer`.

## Backwards-compatibility

- Default `inherit` → `build_scoped_tracer` returns the global tracer, identical
  to the previous `get_tracer(metrics_namespace)`.

## Test plan

- Unit: `build_scoped_tracer("ns", exporter="inherit")` reuses the global
  provider; a non-inherit exporter builds a dedicated provider that captures the
  emitted spans (asserted via an in-memory exporter).

## Acceptance criteria

- [x] Default `inherit` → no behaviour change.
- [x] Non-inherit routes harness spans to a dedicated exporter.
- [x] 95% coverage maintained; ruff/mypy/frontmatter-lint clean.
