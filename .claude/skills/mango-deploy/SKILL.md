---
name: mango-deploy
description: >
  Cloud deployment and telemetry-exporter release workflow for Mango-Mas V2.
  Use when: authoring the Cloud Run service definition, the deploy/ env-var
  contract, or the GitHub Actions deploy workflow; selecting an OpenTelemetry
  exporter (console vs OTLP vs Cloud Trace) via MANGOMAS_TELEMETRY__EXPORTER;
  routing harness spans via MANGOMAS_HARNESS__METRICS_EXPORTER; or reviewing a
  container for Cloud Run readiness (non-root, $PORT-aware, /healthz probe).
  Covers ADR-0001's swap matrix, Workload Identity Federation (no service-account
  keys), lazy cloud-SDK imports behind extras, and the gated-test pattern.
argument-hint: "Describe the deploy/telemetry change (e.g. 'add Cloud Trace exporter', 'author the Cloud Run YAML') or paste a failing deploy workflow"
---

# Mango-Mas Deploy Skill

## When to Use

- Author/change `deploy/` (Cloud Run service YAML or Terraform, `deploy/README.md`)
- Author/change `.github/workflows/deploy.yml` (Artifact Registry push + deploy)
- Add/select a telemetry exporter in `src/mangomas/telemetry.py` via a new
  `TelemetrySettings` group (`MANGOMAS_TELEMETRY__*`)
- Route harness `harness.agent_invoke` spans separately via
  `MANGOMAS_HARNESS__METRICS_EXPORTER`
- Verify the container meets Cloud Run's contract (ADR-0001)

---

## Guardrails (from ADR-0001 "cloud target swap matrix")

| Boundary | Rule |
|----------|------|
| Compute | Container is **non-root**, honours `$PORT`, exposes `/healthz` liveness/readiness. |
| Identity | **Workload Identity Federation only** — no service-account JSON keys in code, config, or CI. |
| Telemetry | Cloud Trace/Logging selected through configuration, not new call sites. Default exporter unchanged when the env var is absent. |
| Secrets | Runtime secrets via GCP Secret Manager provider (`MANGOMAS_SECRETS__PROVIDER=gcp`); never baked into the image. |
| Env contract | Every `MANGOMAS_*` var the service consumes is documented in `deploy/README.md`. |

---

## Rules (do not regress)

| Rule | Detail |
|------|--------|
| Additive & default-OFF | Absent `MANGOMAS_TELEMETRY__EXPORTER` → identical exporter as today. Absent `MANGOMAS_HARNESS__METRICS_EXPORTER` → harness spans fall through to the app exporter. |
| Lazy SDK | `opentelemetry-exporter-gcp-trace` imported inside a factory helper (`# noqa: PLC0415`, `# pragma: no cover - requires extra`) behind the `gcp` extra. Module imports without the extra. |
| No hard-coded values | Endpoints, sample rates, project/location are `DEFAULT_*` constants surfaced via `TelemetrySettings` / `HarnessSettings`. |
| Gated tests | Real Cloud Trace export is unit-tested with the SDK mocked and gated by `RUN_GCP_TRACE=1`, mirroring `RUN_VERTEX` / `RUN_GCP_SECRETS`. End-to-end cloud deploy is **not** claimed in this repo. |
| No cloud provisioning | This repo creates no GCP resources (ADR-0001); `deploy/` provides artifacts + contract only. |

---

## Configuration

`MANGOMAS_TELEMETRY__EXPORTER` (`console` | `otlp` | `gcp`), plus exporter
endpoint/sample-rate fields on `TelemetrySettings`.
`MANGOMAS_HARNESS__METRICS_EXPORTER` selects a separate exporter for
`harness.agent_invoke` spans; default falls through to the application exporter.
Cloud Run runtime reads the standard `MANGOMAS_*` groups (LLM, DB, SECRETS,
TELEMETRY, HARNESS) — enumerate them all in `deploy/README.md`.

---

## Verification

```powershell
ruff check --fix src tests ; ruff format src tests
mypy
python -m pytest tests/test_telemetry*.py -q

# Gated Cloud Trace path (SDK mocked)
pip install -e ".[dev,gcp]"
$env:RUN_GCP_TRACE='1' ; python -m pytest -q -k telemetry

# Workflow / YAML lint
python -m pytest -q -k deploy      # env-contract doc-sync + workflow shape
```

See ADR-0001 (cloud targets), the telemetry/harness specs under `specs/`, and
`docs/architecture/cloud-providers.md`.

---

## Constraints

- DO NOT accept service-account JSON keys anywhere — WIF/ADC only.
- DO NOT change the default exporter behaviour when the env var is unset.
- DO NOT import the Cloud Trace SDK at module top-level — lazy-import behind `gcp`.
- DO NOT claim end-to-end cloud validation from this repo's tests.
