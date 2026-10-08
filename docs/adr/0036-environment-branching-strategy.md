# ADR-0036: Environment branches — dev → qa → main

## Status

Proposed

## Context

The repository has one trunk, `feat/initial-release`, pinned by name in
`ci.yml`, the `Makefile` and `tests/deploy/test_ci_make_parity.py`. `main`
exists but diverged (11 commits it alone carries, all superseded or ported per
ADR-0021 / ADR-0023). Deploys are single-track: one Cloud Run service, released
on `release: published`. We want a production branch plus integration and QA
stages, each mapped to an environment.

## Decision

Three long-lived branches, promoted by pull request:

| Branch | Role | Takes PRs from | Environment |
|---|---|---|---|
| `dev` (default) | Integration trunk | feature / dependabot branches | `mangomas-dev` |
| `qa` | Release candidate | `dev` | `mangomas-qa` |
| `main` | Production record | `qa`, `hotfix/*` | `mangomas` (tag `v*` on `main`) |

- Promotion PRs (`dev`→`qa`, `qa`→`main`) use **merge commits**, never squash,
  so every `BREAKING-CHANGE` trailer stays inside the `base..head` range that
  `scripts/check_protected_paths.py` walks.
- Hotfixes branch from `main`, merge into `main`, then back-merge
  `main`→`qa`→`dev`; the same back-merge follows every release.
- CI's protected-path gate judges a PR against the branch it targets
  (`github.base_ref`), and a push against the default branch, so neither a
  promotion nor the trunk rename needs a hard-coded name.
- `feat/initial-release` is renamed to `dev`; the legacy `main` is preserved as
  tag `archive/main-legacy` and `main` restarts from `dev`.

## Consequences

### Positive

- Every stage gets CI on PR; production changes are reviewed twice.
- The gate's base follows the PR, so a `qa`→`main` promotion is judged by
  `main`'s policy (ADR-0030's base-ref rule, unchanged).

### Negative / Trade-offs

- Merge-commit promotion leaves `main` ahead of `qa`/`dev` by merge commits
  until the back-merge runs.
- Per-environment deploys need GCP provisioning (deploy SA per environment,
  scoped IAM, separate secrets and databases) before `deploy.yml` changes.

### Neutral

- Squash merges into `dev` stay allowed if the trailer is in the squash body.

## Alternatives Considered

- **Fast-forward promotion** — rejected: no PR review at the promotion step.
- **Keep `feat/initial-release` as trunk** — rejected: the name carries no role.

## References

- Code: `.github/workflows/ci.yml` (`protected-paths` job), `Makefile` `BASE_REF`
- Related ADRs: ADR-0021, ADR-0023, ADR-0030
