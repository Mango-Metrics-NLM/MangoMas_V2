---
name: mango-soft-deploy
description: >
  Repeatable Cloud Run soft-deploy verification loop for Mango-Mas V2
  (mangomas-dev / mangomas-qa). Use when: rendering and validating overlays,
  smoking non-z health/readiness endpoints (/health, /ready), executing an
  authenticated two-credential Cloud Run invoke, or verifying Vertex
  MANGOMAS_LLM__LOCATION=global configuration before marking a soft-deploy green.
argument-hint: "Specify environment ('dev' or 'qa') or paste a failing smoke/auth response"
---

# Mango-Mas Soft-Deploy Skill

## When to Use

- Verifying a `dev` or `qa` Cloud Run soft-deploy (`deploy/environments.yaml`)
- Debugging `*.run.app` Google Frontend `404` responses on `*z` paths or `401` on authenticated invokes
- Confirming `MANGOMAS_LLM__LOCATION=global` Vertex configuration and offline deploy guards

Use `mango-deploy` for Cloud Run manifest/exporter design and `mango-gcp-demo` for local ADC/billing pre-flight. Never run billed live Vertex/Cloud Run commands without explicit operator consent.

---

## Procedure

1. **Pre-flight & consent gate.** Confirm `make gcp-preflight` passes cleanly locally when testing against GCP, and confirm operator consent before invoking any billed live Cloud Run revision or Vertex endpoint.
2. **Offline validate & render check.** Validate every environment overlay and the non-`z` probe invariants locally before pushing:

   ```powershell
   make deploy-validate
   python -m pytest tests/regression/test_aqa_20261120_soft_deploy.py tests/deploy/test_deploy_contract.py tests/deploy/test_manifest_render_contract.py -q --no-cov
   ```

3. **WIF identity-token smoke on canonical non-`z` paths.**
   - Probe **`GET /health`** and **`GET /ready`** (public `*.run.app` GFE intercepts exact `/healthz` with an HTML 404 before reaching the container; FastAPI still retains `/healthz` and `/readyz` aliases internally).
   - Mint the OIDC identity token via deploy service-account impersonation with `--audiences=$SERVICE_URL`.
4. **Two-credential authenticated invoke.**
   - Send `X-Serverless-Authorization: Bearer <oidc-identity-token>` for Cloud Run IAM and `X-API-Key: <api-token>` (resolved from `MANGOMAS_AUTH__SECRET_REF`) when app auth is enabled.
   - Never overload `Authorization: Bearer` with the Cloud Run OIDC JWT — `require_auth` inspects `Authorization` before `X-API-Key` and rejects the JWT with HTTP `401`.
5. **Vertex `global` publisher check.** Verify `deploy/environments.yaml` sets `MANGOMAS_LLM__LOCATION=global` for `dev` (`gemini-3-flash-preview` / publisher endpoints) before expecting live `/ready` or invoke green.

---

## Verification

```powershell
make deploy-validate
make deploy-plan DEPLOY_LOG_LEVEL=DEBUG GITHUB_REF=refs/heads/dev
python -m pytest tests/regression/test_aqa_20261120_soft_deploy.py tests/test_deploy_environment.py -q --no-cov
```

Check `scripts/deploy_environment.py` exit codes (`0` OK, `2` config error, `3` refused) before accepting any render or plan output.
