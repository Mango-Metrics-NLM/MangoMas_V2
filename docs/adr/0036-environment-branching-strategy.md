# ADR-0036: Environment branches — dev → qa → main

## Status

Accepted (2026-10-08) — implemented by the rename-proof CI change and the
per-environment deploy (`deploy/environments.yaml`).

## Context

The repository has one trunk, `feat/initial-release`, pinned by name in
`ci.yml`, the `Makefile` and `tests/deploy/test_ci_make_parity.py`. `main`
exists but diverged: its 11 own commits are superseded (workflow runner →
ADR-0023, hook hardening → ADR-0021) or deliberately not ported
(`harness/coverage.py`, `harness_stop_gate.py` — NEXT_STEPS.md, the
reconciliation entry). Deploys are single-track: one Cloud Run service on
`release: published`, and a `workflow_dispatch` that can deploy any branch to
production. We want a production branch plus integration and QA stages.

## Decision

Three long-lived branches, promoted by pull request:

| Branch | Role | Takes PRs from | Deploys to |
|---|---|---|---|
| `dev` (default) | Integration trunk | feature / dependabot | `mangomas-dev` |
| `qa` | Release candidate | `dev` | `mangomas-qa` |
| `main` | Production record | `qa`, `hotfix/*` | `mangomas`, on tag `v*` reachable from `main` |

1. **Merge commits for promotion.** `dev`→`qa` and `qa`→`main` never squash:
   the protected-path gate walks `git log base..head` for `BREAKING-CHANGE`
   trailers, and a squash keeps only the PR body. Squash into `dev` is allowed
   when the trailer is in the squash message.
2. **Back-merge after every release or hotfix** (`main`→`qa`→`dev`). Requiring
   "up to date before merging" on `main` makes a skipped back-merge block the
   next promotion instead of drifting silently.
3. **Gate base follows the PR.** CI resolves the protected-path base as
   `github.base_ref`, else the default branch (push events). The PR run is the
   authoritative one; a push run on a `hotfix/*` branch compares to `dev` and
   is advisory.
4. **Rename, then reset.** `feat/initial-release` is renamed to `dev` after the
   rename-proof CI lands; legacy `main` is kept as tag `archive/main-legacy`
   and `main` restarts from `dev`.
5. **Production deploy is gated by a GitHub Environment** (`prod`: tag rule
   `v*`, required reviewer) with its own deploy identity, closing the
   any-branch `workflow_dispatch` path. `dev`/`qa` get their own Environments
   and service accounts scoped to their own service.
6. **One manifest, overlays in a registry.** `deploy/environments.yaml` maps
   refs to environments and holds each environment's overlay on the unchanged
   `deploy/service.yaml`; `scripts/deploy_environment.py` is its only reader.
   Production renders to `service.yaml` plus the image (test-pinned), so the
   release deploy is backwards compatible.
7. **No cross-environment trust in artefacts.** Each environment has its own
   image repository, deploy identity and runtime identity (non-prod), and the
   manifest pins the image digest. Promotion rebuilds the same git tree rather
   than reusing QA's bytes: reuse across a shared repository would let a dev
   deployer pre-push an image production later trusts.

## Consequences

### Positive

- Every stage gets CI on PR; production changes pass two reviewed promotions.
- A `qa`→`main` promotion is judged by `main`'s governance policy (ADR-0030's
  base-ref rule, unchanged).

### Negative / Trade-offs

- Each release costs two back-merge PRs.
- Scheduled workflows run on the default branch only, so `nightly.yml` scans
  `dev`, not the code in production, until it checks out `main` as well.
- Per-environment deploys need GCP provisioning (deploy/README.md). Branch-push
  deploys stay off behind the `MULTI_ENV_DEPLOY_ENABLED` repository variable
  until it exists; releases deploy production as before, still gated by the
  `verify` job.
- The in-repo `plan`/`render` checks run from the ref being deployed, so they
  catch mistakes, not attacks; the GitHub Environment rules, the `v*` tag
  ruleset and the WIF repository + environment conditions are the boundary.
- Production is byte-identical to QA in *source*, not in *image*.

### Neutral

- Rulesets must allow merge commits on `qa` and `main` and must not require
  linear history.

## Alternatives Considered

- **Fast-forward promotion** — rejected: no PR review at the promotion step.
- **Tag-only environments from one trunk** — rejected: the team asked for
  inspectable `qa` and `main` branch state.

## References

- Code: `.github/workflows/ci.yml` (`protected-paths` job), `Makefile` `BASE_REF`,
  `.github/workflows/deploy.yml`
- Related ADRs: ADR-0021, ADR-0023, ADR-0030
