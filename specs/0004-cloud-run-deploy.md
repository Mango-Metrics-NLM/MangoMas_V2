# Spec-0004: Cloud Run deployment pipeline

- **Status:** Implemented (Milestone E — author-only artifacts)
- **Linked ADR:** ADR-0001 (cloud targets)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Cloud Run deploy pipeline`

## Problem

`NEXT_STEPS.md` › "Cloud Run deployment pipeline": provide a `deploy/`
directory and a release workflow so the service can be deployed to Cloud Run,
with the runtime env-var contract documented. This repository provisions no GCP
resources (ADR-0001) — artifacts only.

## Requirements

- Cloud Run service manifest (non-root, `$PORT`, `/healthz` + `/readyz` probes).
- GitHub Actions workflow: build → Artifact Registry push → `gcloud run deploy`,
  on published release, authenticated via **Workload Identity Federation**.
- `deploy/README.md` documents the full `MANGOMAS_*` runtime contract.
- No service-account JSON keys; secrets via Secret Manager.

## Config / env additions

No new application settings. The deploy manifest sets existing `MANGOMAS_*`
vars; the workflow uses GitHub secrets (`GCP_WIF_PROVIDER`,
`GCP_DEPLOY_SERVICE_ACCOUNT`, `GCP_PROJECT_ID`) referenced by name.

## Protocol / contract impact

- None. `Dockerfile` and the `/healthz` + `/readyz` endpoints already satisfy
  ADR-0001 — no application code change.

## Backwards-compatibility

- Additive files only; no runtime behaviour change.

## Test plan

- `tests/deploy/test_deploy_contract.py`: `service.yaml` is valid YAML with
  liveness `/healthz` + readiness `/readyz` and no literal secrets;
  `deploy.yml` is valid YAML using WIF (`id-token: write`, no `credentials_json`);
  `deploy/README.md` documents every settings-group env prefix (doc-sync,
  derived from `Settings.model_fields`). **No live-cloud assertion.**

## Acceptance criteria

- [x] `service.yaml` + `deploy.yml` + `deploy/README.md` present and lint-clean.
- [x] Container non-root / `$PORT` / probes (already in `Dockerfile`).
- [x] WIF only; no JSON keys. Doc-sync test passes. 95% coverage maintained.
- [ ] Live deploy — **out of scope** for this repo (ADR-0001).
