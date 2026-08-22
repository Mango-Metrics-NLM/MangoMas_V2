# Spec-0024: Deploy-manifest application + post-deploy smoke

- **Status:** Draft
- **Linked ADR:** _none — no boundary change_ (amends the spec-0004 delivery; ADR-0001's "no GCP resources provisioned here" posture is unchanged)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed` (when implemented)
- **Origin:** `docs/analysis/20260822-next-steps-roadmap-analysis.md` §3 Phase 0, item 0.1

## Problem

`.github/workflows/deploy.yml` deploys with `gcloud run deploy --image ...`
only. It never applies `deploy/service.yaml`, so none of the manifest's env
vars, `secretKeyRef`s, probes, resource limits, or autoscaling bounds reach the
deployed service — a real deploy runs with library defaults: auth off, SQLite
storage, the console telemetry exporter, and an LLM base URL pointing at
`localhost:1234`. The manifest is documentation, and no test catches the drift
(`tests/deploy/test_deploy_contract.py` validates each artifact in isolation but
never asserts the workflow consumes the manifest). The hazard is latent — WIF
auth fails while repo secrets are unset — but the release-published trigger
means it arms the moment a release is cut into a credentialed repo, which is why
this spec must land **before** the v0.4.0 release cut.

## Requirements

- The deploy job applies `deploy/service.yaml` (e.g. `gcloud run services
  replace` with image substitution, or an equivalent that provably carries every
  manifest field), replacing the image-only `gcloud run deploy` call.
- A post-deploy smoke step probes the deployed service's `/healthz` and
  `/readyz` and fails the job on non-200.
- `tests/deploy/test_deploy_contract.py` gains a workflow↔manifest tie: the
  deploy workflow must reference `deploy/service.yaml` (both directions — see
  Scenarios).
- Event-payload interpolation stays behind `env:` indirection (the existing
  workflow-hardening contract tests must remain green).
- Absorbs the deferred "`verify` job gating `deploy.yml`" item recorded in
  `NEXT_STEPS.md`.
- Must remain **additive & default-OFF** in effect: nothing changes for any
  environment until a human publishes a release with WIF secrets configured
  (unchanged from today's trigger contract).

## Scenarios (WHEN/THEN)

- WHEN the deploy workflow's manifest-application step is removed or renamed
  THEN `test_deploy_contract.py` fails (guard can fire — `mango-mutation-proof`).
- WHEN the workflow applies the manifest and the manifest parses with both
  probes and `secretKeyRef`s THEN the contract suite passes (never vacuously
  green).
- WHEN the deployed revision fails its `/readyz` probe THEN the workflow run
  fails at the smoke step rather than reporting success.

## Config / env additions

_None._ All production configuration continues to live in
`deploy/service.yaml`; this spec makes that existing contract real.

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_
- Registry additions: _none_

## Backwards-compatibility

- No code-path change; `src/` is untouched. CI behaviour for pushes/PRs is
  unchanged. The only behavioural change is at release-publish time, where the
  deployed service starts receiving the configuration the manifest always
  claimed to provide.

## Test plan

- `tests/deploy/test_deploy_contract.py`: workflow↔manifest tie, smoke-step
  presence, both directions per Scenarios.
- Existing `tests/deploy/test_workflow_hardening.py` stays green (SHA pinning,
  no attacker-influenced interpolation in `run:` bodies).
- Live validation is out of scope until a GCP project exists (decision D2 in
  the origin analysis); the spec is complete when the contract tests pin the
  workflow shape.

## Acceptance criteria

- [ ] `deploy.yml` applies `deploy/service.yaml`; image-only deploy is gone.
- [ ] Post-deploy smoke step probes `/healthz` + `/readyz`.
- [ ] Contract test ties workflow to manifest, proven in both directions.
- [ ] `ruff`, `mypy`, `pytest` (95 % gate), `frontmatter-lint` all clean.
- [ ] CHANGELOG updated.
