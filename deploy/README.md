# Cloud Run deployment

Author-only deploy artifacts for Mango-Mas V2. **This repository provisions no
GCP resources** (ADR-0001) — these files describe *how* to deploy, and the
`deploy.yml` workflow runs the build/push/deploy on a published release. End-to-
end deployment is **not** validated by this repo's tests.

## Contents

- `service.yaml` — Cloud Run (Knative serving v1) service manifest. Replace
  `REGION` / `PROJECT_ID` / image tag at deploy time.
- `../.github/workflows/deploy.yml` — build → Artifact Registry push → `gcloud
  run deploy`, authenticated via **Workload Identity Federation** (no
  service-account JSON keys).

## Identity & prerequisites (ADR-0001)

- **Workload Identity Federation only.** The workflow references these GitHub
  Actions secrets *by name* (values live in repo settings, never in the repo):
  `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT`, `GCP_PROJECT_ID`.
- Runtime secrets (API keys, DB URLs) come from **GCP Secret Manager**
  (`MANGOMAS_SECRETS__PROVIDER=gcp`), injected as `secretKeyRef` or resolved via
  `LLMSettings.secret_ref` — never baked into the image or the manifest.
- Container is **non-root**, honours `$PORT`, and exposes `/healthz` (liveness)
  + `/readyz` (readiness).

## Runtime environment-variable contract

All configuration is env-driven with the `MANGOMAS_` prefix and `__` nested
delimiter. The authoritative per-field defaults live in the CLAUDE.md
configuration table; this is the deploy-time contract by settings group.

### Top-level

| Var | Default | Purpose |
|-----|---------|---------|
| `MANGOMAS_ENV` | `local` | Set to `prod` on Cloud Run |
| `MANGOMAS_LOG_LEVEL` | `INFO` | Root log level |
| `MANGOMAS_DISCOVERY_ENABLED` | `false` | Entry-point plugin discovery (eval + agents) |

### Settings groups

| Prefix | Purpose |
|--------|---------|
| `MANGOMAS_LLM__` | LLM provider/endpoint/model (`vertex` on GCP) |
| `MANGOMAS_DB__` | Turn storage (`postgres` / Cloud SQL on GCP) |
| `MANGOMAS_API__` | HTTP surface options |
| `MANGOMAS_LOG__` | Log format (`json` on Cloud Run) |
| `MANGOMAS_TELEMETRY__` | Span exporter (`gcp` → Cloud Trace) |
| `MANGOMAS_LOOP__` | Orchestrator loop caps |
| `MANGOMAS_MEMORY__` | File-memory backend |
| `MANGOMAS_HARNESS__` | Claude Code harness spans + metrics exporter |
| `MANGOMAS_EVAL__` | Evaluation harness config |
| `MANGOMAS_SECRETS__` | Secrets provider (`gcp`) + `STRICT` fail-loud mode |
| `MANGOMAS_EMBEDDINGS__` | Embedding provider (RAG, opt-in) |
| `MANGOMAS_VECTOR__` | Vector store (RAG, opt-in) |
| `MANGOMAS_RAG__` | Chunking parameters (RAG, opt-in) |

Recommended production baseline: `MANGOMAS_ENV=prod`,
`MANGOMAS_LOG__FORMAT=json`, `MANGOMAS_TELEMETRY__EXPORTER=gcp`,
`MANGOMAS_LLM__PROVIDER=vertex`, `MANGOMAS_DB__PROVIDER=postgres`,
`MANGOMAS_SECRETS__PROVIDER=gcp`, `MANGOMAS_SECRETS__STRICT=true`.

## Deploy

Triggered automatically on a published GitHub release, or manually via
`workflow_dispatch`. To deploy locally instead:

```bash
IMAGE="us-central1-docker.pkg.dev/PROJECT_ID/mangomas/mangomas:$(git rev-parse --short HEAD)"
docker build -t "$IMAGE" .
docker push "$IMAGE"
gcloud run deploy mangomas --image "$IMAGE" --region us-central1 \
  --platform managed --no-allow-unauthenticated
```
