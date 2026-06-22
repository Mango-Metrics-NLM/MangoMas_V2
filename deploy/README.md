# Deploying Mango-Mas V2 to Cloud Run

This directory provisions and deploys the service to **Cloud Run** using
**Terraform** and **keyless GitHub Actions auth** (Workload Identity Federation —
no service-account JSON keys are stored anywhere).

The container image is the repository-root [`Dockerfile`](../Dockerfile)
(multi-stage, non-root, `$PORT`-aware, `/healthz` healthcheck). No application
code changes are required to deploy.

## Layout

| Path | Purpose |
|------|---------|
| `terraform/` | Cloud Run service, Artifact Registry, runtime + deployer service accounts, WIF pool/provider |
| `terraform/terraform.tfvars.example` | Copy to `terraform.tfvars` for a local bootstrap apply |
| `../.github/workflows/deploy.yml` | Build → push → `terraform apply` on release (or manual dispatch) |

## One-time bootstrap

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # edit project_id / github_repo
terraform init
terraform plan
terraform apply
```

Then wire the Terraform outputs into the repository's Actions configuration
(**Settings → Secrets and variables → Actions → Variables**):

| Repo variable | Source |
|---------------|--------|
| `GCP_PROJECT_ID` | your project id |
| `GCP_REGION` | e.g. `us-central1` |
| `WIF_PROVIDER` | `terraform output workload_identity_provider` |
| `DEPLOY_SA` | `terraform output deployer_service_account` |
| `AR_REPOSITORY` | `mangomas` (or your `artifact_repository_id`) |
| `SERVICE_NAME` | `mangomas` (or your `service_name`) |

## Continuous deployment

`deploy.yml` triggers on `release: published` (and `workflow_dispatch`). It
authenticates via WIF, builds + pushes the image tagged with the release tag (or
git sha), and runs `terraform apply` to roll out the revision. The image tag is
the only changing input — Terraform owns the rest of the service definition.

## Environment-variable contract (production)

Only **plain** config goes in the `env` map; secrets resolve at runtime through
Secret Manager via the app's secrets seam (`MANGOMAS_SECRETS__PROVIDER=gcp`).

| Variable | Value | Notes |
|----------|-------|-------|
| `MANGOMAS_ENV` | `prod` | |
| `MANGOMAS_LOG__FORMAT` | `json` | Cloud Logging-compatible structured logs |
| `MANGOMAS_TELEMETRY__EXPORTER` | `gcp` | Cloud Trace span export (requires the `gcp-trace` extra in the image) |
| `MANGOMAS_TELEMETRY__GCP_PROJECT_ID` | _project id_ | |
| `MANGOMAS_LLM__PROVIDER` | `vertex` | Optional — Vertex AI LLM via ADC |
| `MANGOMAS_LLM__PROJECT_ID` / `__LOCATION` | _project_ / _region_ | When `PROVIDER=vertex` |
| `MANGOMAS_DB__PROVIDER` | `postgres` | Optional — Cloud SQL |
| `MANGOMAS_DB__URL` | _DSN_ | Cloud SQL connection string |
| `MANGOMAS_SECRETS__PROVIDER` | `gcp` | Secret Manager backend |
| `MANGOMAS_SECRETS__PROJECT_ID` | _project id_ | |
| `MANGOMAS_SECRETS__STRICT` | `true` | Fail-loud on secret-resolution failures (ships with the strict-mode change) |

> Identity is always sourced from the runtime service account (ADC / Workload
> Identity). Service-account JSON keys are never used.

## Verifying changes

- `terraform fmt -check -recursive` and `terraform validate` run in CI
  (`deploy-lint` job, path-filtered to `deploy/**`).
- Manual smoke test: authenticate, `terraform plan` against a sandbox project,
  then `gcloud run services describe <service> --region <region>` to confirm the
  revision and `/healthz` readiness.
