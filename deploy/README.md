# Cloud Run deployment

Author-only deploy artifacts for Mango-Mas V2. **This repository provisions no
GCP resources** (ADR-0001) — these files describe *how* to deploy, and the
`deploy.yml` workflow runs the build/push/deploy on a published release. End-to-
end deployment is **not** validated by this repo's tests.

Since ADR-0036 there are three environments — `dev`, `qa` and `prod` — each a
separate Cloud Run service built from the same base manifest. See
[Environments](#environments-adr-0036).

## Contents

- `service.yaml` — Cloud Run (Knative serving v1) base service manifest; as
  written it *is* the production service. The image is set at deploy time.
- `environments.yaml` — the environment registry: which ref deploys where,
  and each environment's overlay on `service.yaml` (service name, `MANGOMAS_*`
  values, secret names, autoscaling bounds, runtime service account).
- `../scripts/deploy_environment.py` — the registry's only reader (`plan` /
  `render`), driven through `make deploy-plan` / `make deploy-render`.
- `../.github/workflows/deploy.yml` — plan → verify → build (or reuse) →
  Artifact Registry push → `gcloud run services replace` → smoke probe,
  authenticated via **Workload Identity Federation** (no service-account JSON
  keys).

## Identity & prerequisites (ADR-0001)

- **Workload Identity Federation only.** The workflow references these GitHub
  Actions secrets *by name* (values live in repo settings, never in the repo):
  `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT`, `GCP_PROJECT_ID`. The first
  two are **GitHub Environment secrets** — one deploy identity per environment
  (see below) — so a dev push cannot authenticate as the production deployer.
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
| `MANGOMAS_ENV` | `local` | `local`/`dev`/`qa`/`prod`; set per environment by `environments.yaml` |
| `MANGOMAS_LOG_LEVEL` | `INFO` | Root log level |
| `MANGOMAS_DISCOVERY_ENABLED` | `false` | Entry-point plugin discovery (eval + agents) |

### Settings groups

| Prefix | Purpose |
|--------|---------|
| `MANGOMAS_LLM__` | LLM provider/endpoint/model (`vertex` on GCP) |
| `MANGOMAS_DB__` | Turn storage (`postgres` / Cloud SQL on GCP) |
| `MANGOMAS_API__` | HTTP surface options (incl. opt-in `CORS_ALLOW_ORIGINS`) |
| `MANGOMAS_AUTH__` | API authentication (opt-in bearer / API-key; `SECRET_REF`) |
| `MANGOMAS_TENANCY__` | Tenant-scoped storage (opt-in; `X-Tenant-ID` header row-filter) |
| `MANGOMAS_LOG__` | Log format (`json` on Cloud Run) |
| `MANGOMAS_TELEMETRY__` | Span exporter (`gcp` → Cloud Trace) |
| `MANGOMAS_LOOP__` | Orchestrator loop caps |
| `MANGOMAS_MEMORY__` | File-memory backend |
| `MANGOMAS_HARNESS__` | Claude Code harness spans + metrics exporter |
| `MANGOMAS_SIGNAL__` | CognitiveSignal JSONL/HTTP producer (opt-in; not a capability grant) |
| `MANGOMAS_EVAL__` | Evaluation harness config |
| `MANGOMAS_SECRETS__` | Secrets provider (`gcp`) + `STRICT` fail-loud mode |
| `MANGOMAS_EMBEDDINGS__` | Embedding provider (RAG, opt-in) |
| `MANGOMAS_VECTOR__` | Vector store (RAG, opt-in) |
| `MANGOMAS_RAG__` | Chunking parameters (RAG, opt-in) |
| `MANGOMAS_WORKFLOW__` | Declarative workflow-graph dispatch (opt-in) |

Recommended production baseline: `MANGOMAS_ENV=prod`,
`MANGOMAS_LOG__FORMAT=json`, `MANGOMAS_TELEMETRY__EXPORTER=gcp`,
`MANGOMAS_LLM__PROVIDER=vertex`, `MANGOMAS_DB__PROVIDER=postgres`,
`MANGOMAS_SECRETS__PROVIDER=gcp`, `MANGOMAS_SECRETS__STRICT=true`.

## Environments (ADR-0036)

| Environment | Deploys on | Cloud Run service | GitHub Environment rule |
|---|---|---|---|
| `dev` | push to `dev` | `mangomas-dev` | branch `dev` |
| `qa` | push to `qa` | `mangomas-qa` | branch `qa` |
| `prod` | published release, tag `v*` reachable from `main` | `mangomas` | tag `v*` + required reviewer |

`deploy/environments.yaml` is the source of that table;
`tests/deploy/test_deploy_environments.py` fails if `deploy.yml`'s push
branches or dispatch options drift from it, if an overlay changes a manifest
field outside the allow-list, if a non-production environment reuses a
production secret, or if production renders to anything but `service.yaml`
plus the image.

**Image promotion.** The image tag is the git *tree* hash, so a `qa` → `main`
promotion (same tree, new merge commit) reuses the exact image QA tested; the
build step is skipped when the tag already exists, and a release also tags the
image with its release name.

**One-time setup (admin; this repo provisions nothing):**

1. Per environment, create a runtime service account, a deploy service account
   with `roles/run.developer` and `roles/run.invoker` on *that service only*
   plus `roles/iam.serviceAccountUser` on *that runtime account only*, and the
   Secret Manager secrets its overlay names (e.g. `mangomas-llm-api-key-dev`).
   Give dev and qa their own databases.
2. Restrict each WIF provider binding to its GitHub Environment
   (`attribute.environment`), so only `environment: prod` jobs can mint the
   production deployer's token.
3. Create GitHub Environments `dev`, `qa`, `prod` with the rules above, each
   holding `GCP_WIF_PROVIDER` + `GCP_DEPLOY_SERVICE_ACCOUNT`; delete any
   repository-level copies of those two secrets.
4. Set the repository variable `MULTI_ENV_DEPLOY_ENABLED=true`. Until then a
   push to `dev`/`qa` skips the deploy (releases still deploy production).

**Debugging.** `plan` logs which environment a ref resolved to and why; a
refusal (exit `3`) or configuration error (exit `2`) also appears as an error
annotation on the run. Re-run the workflow with debug logging (it sets
`RUNNER_DEBUG=1`) to log every resolution step and overlay field, or run
locally:

```bash
make deploy-plan DEPLOY_LOG_LEVEL=DEBUG GITHUB_REF=refs/heads/qa
make deploy-render ENVIRONMENT=qa IMAGE=example:tag DEPLOY_LOG_LEVEL=DEBUG
```

## Deploy

Triggered by a push to `dev`/`qa`, a published GitHub release (`prod`), or
manually via `workflow_dispatch` — whose `environment` input must own the
selected ref, so a manual run cannot push a feature branch to production. The
workflow applies the rendered manifest in full (spec-0024) — an image-only
`gcloud run deploy` would drop every env var, `secretKeyRef`, probe, resource
limit and autoscaling bound above — and then smoke-probes `/healthz` +
`/readyz` on the deployed revision with an identity token (the service is
private: `services replace` never creates an `allUsers` invoker binding). To
deploy locally instead, mirror the workflow:

```bash
ENVIRONMENT=qa   # dev | qa | prod — see deploy/environments.yaml
IMAGE="us-central1-docker.pkg.dev/PROJECT_ID/mangomas/mangomas:$(git rev-parse 'HEAD^{tree}')"
docker build -t "$IMAGE" .
docker push "$IMAGE"
make deploy-render ENVIRONMENT="$ENVIRONMENT" IMAGE="$IMAGE"   # -> rendered-service.yaml
gcloud run services replace rendered-service.yaml --region us-central1

URL="$(gcloud run services describe mangomas-qa --region us-central1 --format 'value(status.url)')"
TOKEN="$(gcloud auth print-identity-token)"   # caller needs roles/run.invoker
curl -fsS -H "Authorization: Bearer ${TOKEN}" "${URL}/healthz"
curl -fsS -H "Authorization: Bearer ${TOKEN}" "${URL}/readyz"
```
