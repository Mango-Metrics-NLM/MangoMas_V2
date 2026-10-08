# Spec-0035: Environment branches and per-environment deploy

- **Status:** Implemented (pending the admin steps in `deploy/README.md`)
- **Linked ADR:** ADR-0036
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — per-environment Cloud Run deploy (ADR-0036)`

## Problem

The repository had one trunk (`feat/initial-release`) hard-coded across CI,
the Makefile and tests, a diverged `main`, and a single-track deploy where a
manual dispatch could ship any branch to production. The team wants `main` to
be production, with `dev` (integration) and `qa` (release candidate) stages,
each deploying to its own environment without cross-environment trust.

## Requirements

- R1 — CI gates PRs into `dev`, `qa` and `main`; the protected-path gate diffs
  against the PR's own base (`github.base_ref`, else the default branch).
- R2 — One base manifest (`deploy/service.yaml`); environment differences live
  only in `deploy/environments.yaml` overlays with a closed schema.
- R3 — The workflow resolves the environment from the ref: `dev`/`qa` pushes,
  and a published, non-prerelease `v<digit>…` tag reachable from `main` for
  production. A dispatch cannot widen what a ref may deploy.
- R4 — Each environment has its own image repository, deploy identity and
  (non-production) runtime identity; the manifest pins the image digest.
- R5 — No hard-coded project ids; `${NAME}` placeholders are filled from the
  environment at render time.
- R6 — **Default-OFF**: branch-push deploys are skipped until
  `MULTI_ENV_DEPLOY_ENABLED=true`; a release deploys production exactly as
  before apart from the digest-pinned image.
- R7 — The registry is validated offline in CI (`make validate-config` →
  `make deploy-validate`).

## Scenarios (WHEN/THEN)

- WHEN a feature branch is pushed THEN `plan` refuses (exit 3); WHEN `dev` is
  pushed (flag on) THEN `mangomas-dev` deploys.
- WHEN a `v*` tag is not reachable from `origin/main` THEN `plan` refuses;
  WHEN it is THEN production plans.
- WHEN a release is a prerelease THEN `plan` does not run.
- WHEN a dispatch requests `prod` for a branch ref THEN `plan` refuses.
- WHEN an overlay sets a plain value on a secret variable, an unknown key, a
  reserved env name or a non-scaling annotation THEN parsing/rendering fails
  (exit 2); WHEN the registry is valid THEN `deploy-validate` passes.
- WHEN a non-production environment shares production's secret, repository
  or runtime identity THEN the contract suite fails.

## Config / env additions

| Name | Default | Purpose |
|------|---------|---------|
| `MANGOMAS_ENV` | `local` | gains the `qa` value |
| `MULTI_ENV_DEPLOY_ENABLED` (repo variable) | unset | enables branch-push deploys |
| `DEPLOY_REGISTRY` / `DEPLOY_SCRIPT` / `DEPLOY_LOG_LEVEL` / `RENDERED_MANIFEST` / `DEPLOY_RENDER_ENV_VARS` (Make) | see `Makefile` | deploy tooling knobs |

## Protocol / contract impact

- New/changed protocols: _none_. New error types: _none_. Registry additions: _none_.

## Backwards-compatibility

- Production renders to `deploy/service.yaml` plus the image (test-pinned); the
  `mangomas` repository and default runtime identity are unchanged.
- `MANGOMAS_ENV` only gains a value; nothing branches on it.

## Test plan

- Unit: `tests/test_deploy_environment.py` (script, 100 % line + branch).
- Contract: `tests/deploy/test_deploy_environments.py`,
  `test_deploy_contract.py`, `test_ci_make_parity.py`.
- Corpus: `tests/tooling/test_corpus_contract.py` (script ownership, cited
  make targets and files resolve).
- Regression: `tests/regression/test_adr0036_deploy_defects.py`.
- Gates proven both directions by mutation (see the PR).

## Acceptance criteria

- [x] R1–R7 implemented and tested.
- [ ] Admin steps in `deploy/README.md` performed; `MULTI_ENV_DEPLOY_ENABLED` set.
- [ ] `feat/initial-release` renamed to `dev`; legacy `main` archived and reset.
