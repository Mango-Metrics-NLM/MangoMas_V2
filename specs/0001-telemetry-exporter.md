# Spec-0001: Telemetry exporter selection

- **Status:** Implemented (Milestone C)
- **Linked ADR:** ADR-0009
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Telemetry exporter selection`

## Problem

`NEXT_STEPS.md` › "Cloud Logging + Cloud Trace exporter swap": ship a Cloud
Trace exporter selectable behind the existing `configure_telemetry()` without
changing default behaviour.

## Requirements

- `MANGOMAS_TELEMETRY__EXPORTER` selects `console` (default) or `gcp` (Cloud
  Trace). Absent → console, identical to today.
- Cloud SDK is an optional extra (`gcp`), lazily imported; the module imports
  without it.

## Config / env additions

| Env var | Default | Purpose |
|---------|---------|---------|
| `MANGOMAS_TELEMETRY__EXPORTER` | `console` | Application span exporter (`console`/`gcp`) |

`DEFAULT_TELEMETRY_EXPORTER` in `config.py`; new `TelemetrySettings` group.

## Protocol / contract impact

- No protocol change. New `_build_span_exporter` helper + `exporter` param on
  `configure_telemetry`. New optional dep `opentelemetry-exporter-gcp-trace`
  under the `gcp` extra.

## Backwards-compatibility

- Env absent → single `ConsoleSpanExporter`, byte-identical to prior behaviour.

## Test plan

- Unit: `_build_span_exporter` console/gcp (gcp via monkeypatched lazy helper);
  `configure_telemetry(exporter="gcp")` constructs the exporter.
- Gated: `@pytest.mark.gcp_trace` (`RUN_GCP_TRACE=1`) builds the real exporter.

## Acceptance criteria

- [x] Off by default → console exporter, no behaviour change.
- [x] `gcp` selects Cloud Trace via lazy import behind the `gcp` extra.
- [x] 95% coverage maintained; ruff/mypy/frontmatter-lint clean.
