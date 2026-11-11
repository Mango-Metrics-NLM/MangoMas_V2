---
name: mango-gcp-demo
description: >
  Operator workflow for demonstrating Mango-Mas against Google Cloud from a
  local CLI (spec-0049). Use when: preparing or running the GCP demo; a live
  Vertex call fails with PERMISSION_DENIED, SERVICE_DISABLED or ADC errors;
  loading the .env.gcp-demo template; or extending the read-only pre-flight
  (scripts/verify_gcp_prerequisites.py, `make gcp-preflight`). Covers the
  pre-flight-first rule, the billing-account trap, consent before billed
  calls, evidence capture, and offline (fake-backed) verification for CI.
argument-hint: "Describe the demo step or paste the failing command/output (e.g. 'Vertex chat returns PERMISSION_DENIED')"
---

# Mango-Mas GCP Demo Skill

The runbook is [docs/runbooks/gcp-local-demo.md](../../../docs/runbooks/gcp-local-demo.md).
This skill is the decision procedure around it. Don't restate the runbook here.

## When to Use

- Running any journey in the runbook against a real project
- Diagnosing a live GCP failure (ADC, billing, API enablement, Postgres endpoint)
- Changing `scripts/verify_gcp_prerequisites.py`, `.env.gcp-demo`, or their tests

## Procedure

1. **Pre-flight first, every time.** Run `make gcp-preflight` before any live
   command. Any `FAIL` row is a stop: fix the environment, not the code.
2. **Billing trap.** A project can report `billingEnabled=true` while its
   billing account is closed. Vertex then rejects every call with
   `PERMISSION_DENIED`, and the CLI persists it as `error_code="llm_bad_response"`.
   The `billing` check reads the account's `open` flag for this reason. Don't debug
   prompts or adapters until that row passes.
3. **Consent before billed calls.** Live Vertex calls cost money and run
   against a specific project. Name the project and get the operator's explicit
   go-ahead before the first live call. Never probe other projects to find one
   that has billing.
4. **Isolate state.** Point `MANGOMAS_DB__URL` at a scratch database for demo
   runs so failed turns don't land in the developer's default store. Give each
   embedding model its own `MANGOMAS_VECTOR__COLLECTION` /
   `PERSIST_DIR` (768-dim vs 3072-dim vectors).
5. **Evidence.** Capture `make gcp-preflight GCP_PREFLIGHT_FLAGS=--json` and
   the command exit codes into `artifacts/validation/` (git-ignored). Report
   only numbers you ran yourself.

## Changing the pre-flight

- Every probe stays read-only (GET or TCP connect). Never create, enable or
  modify a resource from the pre-flight.
- Defaults come from `mangomas.config.Settings`. Don't add literal hosts,
  ports, regions or model IDs.
- `google-auth` is optional. Tests inject a `GoogleAuthBackend` of fakes, so
  the suite runs under `make install` (`[dev]` only), which is what CI installs.
  A test that imports a cloud extra at module level fails CI.
- A 403 on a probe is SKIP (state unverifiable), a disabled/closed state is FAIL,
  and a transport error is FAIL.
- `--json` keys (`all_passed`, `summary`, `checks[name,status,message,details,remediation]`)
  are additive-only.

## Verification

```bash
python -m pytest tests/test_preflight_verification.py tests/deploy/test_env_example_contract.py -q --no-cov
make scripts-coverage
```

## Related Skills

- `mango-deploy`: Cloud Run manifests and exporter selection
- `mango-aqa`: the gated live-tier lane matrix

