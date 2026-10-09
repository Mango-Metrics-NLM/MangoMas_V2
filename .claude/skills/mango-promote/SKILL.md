---
name: mango-promote
description: >
  Promoting code through the environment branches in Mango-Mas V2 (ADR-0036).
  Use when: opening a dev → qa or qa → main promotion PR, cutting a production
  release tag, shipping a hotfix from main, back-merging main into qa and dev,
  or diagnosing a promotion whose protected-path gate or deploy plan failed.
  Covers the merge-commit rule, trailer carry-over, the tag/ancestry contract,
  and a self-paced /loop for watching a promotion through to deploy.
argument-hint: "Name the promotion (dev→qa, qa→main, hotfix) or paste the failing step"
---

# Mango-Mas Promotion Skill

## When to Use

- Moving tested work up a stage: `dev` → `qa`, or `qa` → `main` plus a release
- A production defect needs a fix that cannot wait for the next promotion
- A release just shipped and `qa` / `dev` must catch up with `main`
- A promotion PR's `Protected-path governance gate` or the deploy `plan` job failed

## The Model (ADR-0036)

| Branch | Takes PRs from | Deploys |
|---|---|---|
| `dev` (default) | feature / dependabot branches | push → `mangomas-dev` |
| `qa` | `dev` | push → `mangomas-qa` |
| `main` | `qa`, `hotfix/*` | published release `v<digit>…` reachable from `main` → `mangomas` |

Which ref deploys where is `deploy/environments.yaml`, not this table — run
`make deploy-validate` to see the live mapping.

## Procedure

### Promote dev → qa (and qa → main)

1. Confirm the source branch is green on its latest commit.
2. Open a PR **from the source branch itself** (never a copy) into the target.
3. Merge with **"Create a merge commit"**. Never squash or rebase a promotion:
   `scripts/check_protected_paths.py` walks `git log <base>..<head>` for
   `BREAKING-CHANGE` trailers, and a squash keeps only the PR body, so an
   approved protected-path change arrives unapproved and the gate fails.
4. Check locally first, against the same base CI will use:

   ```bash
   git fetch origin qa
   make protected-paths BASE_REF=origin/qa
   ```

### Cut a production release

1. After `qa` → `main` merges, tag the merge commit on `main`: `v<MAJOR>.<MINOR>.<PATCH>`
   (`chore(release): vX.Y.Z` per `mango-release`). Prereleases never deploy.
2. Publish the GitHub release for that tag. `deploy.yml`'s `plan` job proves the
   tag is reachable from `origin/main` and that its name is a valid image tag;
   the `prod` GitHub Environment then waits for its reviewer.
3. Dry-run the plan locally to see exactly what will happen:

   ```bash
   make deploy-plan GITHUB_REF=refs/tags/v1.2.3 DEPLOY_LOG_LEVEL=DEBUG
   ```

### Hotfix

1. Branch `hotfix/<slug>` from `main`; fix with a test; PR into `main`.
2. Merge with a merge commit, then release as above.
3. Back-merge immediately (next section). A hotfix that never reaches `dev`
   regresses on the next promotion.

### After the one-time `main` reset

Set `NIGHTLY_SCAN_ENVIRONMENT_BRANCHES=true` (repository variable) so the
nightly scans every environment branch, then confirm with a manual
`workflow_dispatch` of `nightly.yml`. Preview the list locally:

```bash
NIGHTLY_SCAN_ENVIRONMENT_BRANCHES=true make nightly-scan-refs
```

Each scan leg runs its own branch's Makefile — rename or remove a nightly
target only after the change has been promoted to `main`, or that leg fails.

### Back-merge after every release or hotfix

Open `main` → `qa`, then `qa` → `dev`, both as merge commits. `main` requires
branches to be up to date, so a skipped back-merge blocks the next `qa` → `main`
promotion rather than drifting silently.

### Watch a promotion through (self-paced loop)

`/loop` with no interval paces itself. Point it at the promotion PR, and use
`mango-pr-watcher` for the read-only triage step:

```text
/loop check PR <n>: CI on the head commit, mergeability, review threads; once merged, the
deploy.yml run for the target branch (plan → verify → deploy → smoke). Stop when the smoke
probe passes or something needs a human.
```

## Diagnosing Failures

| Symptom | Cause | Fix |
|---|---|---|
| Gate: protected path "NOT APPROVED" on a promotion | squash-merged upstream, trailer lost | add a commit with `BREAKING-CHANGE: <path> — <why>` to the source branch |
| `plan` exit 3: "not reachable from refs/remotes/origin/main" | tag cut on a side branch | delete the tag, tag the `main` merge commit |
| `plan` exit 3: "not a valid image tag" | release name has `+`, `/` or > 128 chars | use plain `vX.Y.Z` |
| `plan` exit 3: "no environment deploys from" | ref not in the registry | expected for feature branches; otherwise check `deploy/environments.yaml` |
| deploy skipped on a push | `MULTI_ENV_DEPLOY_ENABLED` unset | provision per `deploy/README.md`, then set the variable |

## Constraints

- DO NOT squash, rebase or fast-forward a promotion or back-merge.
- DO NOT force-push `dev`, `qa` or `main`.
- DO NOT tag anything not on `main`, and never move a published `v*` tag.
- DO NOT skip the back-merge after a release or hotfix.
