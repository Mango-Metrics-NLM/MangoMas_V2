# Spec-0052: Loop trusted base and base-anchored scope check

- **Status:** Draft
- **Linked ADR:** [ADR-0042](../docs/adr/0042-governed-self-improvement-loop.md) (Proposed)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Governed self-improvement loop: decision records (ADR-0042 / spec-0052..0055)`
- **Delivery plan:** [milestone M1](../docs/plans/20261123T000000Z-governed-self-improvement-loop-plan.md)

## Problem

A loop that writes code must not be able to change what judges it. The
protected set is 13 exact paths (`pyproject.toml:147-167`) matched by exact
intersection (`scripts/check_protected_paths.py:191`). CI workflows, the
`Makefile`, gate scripts, `tests/`, `evals/` and `.claude/` are outside it, and
a `pull_request` run executes the PR's own copy of all of them. ADR-0030:86-90
names a required status check as the only defence for `ci.yml` and the
`Makefile`, but a required check is matched by job name, so a PR that can edit
workflows can also produce a passing check of the same name. Two smaller holes:
an unscoped `BREAKING-CHANGE:` approves every touched protected path
(`scripts/_governance.py:170-175`), and an unreadable base policy falls back to
the working tree (`scripts/check_protected_paths.py:165-171`).

## Requirements

- **R1 Policy.** A `[tool.mangomas.governance.loop]` table in `pyproject.toml`
  (already protected and read from the base ref) holding
  `implementation_prefix = "claude/loop-"`, `proposal_prefix = "claude/proposal-"`,
  `trusted_base` and `proposal_paths` (glob lists). `**` matches any number of
  path segments and `*` matches within one segment, on normalised repo paths.
- **R2 Classification.** The head ref decides the class: implementation,
  proposal or ungoverned. Ungoverned branches pass with a "not governed" note,
  so human PRs keep today's rules.
- **R3 Rules.** Implementation: no changed path may match `trusted_base`, and
  every changed path must match the approved item's `allowed_paths`, read from
  the base branch (until spec-0053 lands, every implementation branch fails).
  Proposal: every changed path must match `proposal_paths`, and none may be a
  test-runner hook file. Renames count both paths; deletions and mode changes
  count.
- **R4 Fail closed.** A missing or unparseable base policy, a truncated or
  failed file listing, an unknown item ID, or the repository variable
  `LOOP_ENABLED` not set to `true` (implementation branches only) all fail.
  There is no working-tree fallback.
- **R5 Anchor.** `.github/workflows/loop-scope.yml` runs on
  `pull_request_target` (opened, synchronize, reopened, edited) for base
  branches `dev`, `qa` and `main`; GitHub always runs the default branch's copy
  of the workflow for this event. The job checks out only the default branch,
  lists changed files through the API, never checks out or executes PR code,
  holds only `contents: read` and `pull-requests: read`, uses no secrets, and
  passes event values through `env:` rather than interpolating them into
  `run:` (as `tests/deploy/test_workflow_hardening.py` requires). Job name:
  `Loop scope check`.
- **R6 Required checks.** `CONTRIBUTING.md`'s required-check list gains
  `Guard falsifiability probe` and `Loop scope check`; an admin marks both
  required on `dev`, `qa` and `main`.
- **R7 Credential.** Verify and record whether the implementer's push
  credential can create or update `.github/workflows/**` (GitHub refuses such
  pushes from credentials without workflow permission). If it can, the scope
  check reports through a dedicated GitHub App whose key lives in an
  environment limited to the default branch, and that App is set as the
  required check's source.
- **R8 Staging hole.** The repair sandbox's startup-hook refusal
  (`src/mangomas/harness/isolation/base.py:97-112`) also refuses `conftest.py`,
  `pytest.ini`, `setup.cfg`, `tox.ini`, `.coveragerc` and `pyproject.toml` in a
  staging tree unless the caller allows them explicitly.
- **R9 Mutation proof.** `scripts/guard_probe_manifest.json` gains `MUT-LOOP-*`
  entries: removing a `trusted_base` glob, flipping fail-closed, and accepting
  a truncated listing must each turn a named test red.

## Trusted base (first version)

| Globs | Why |
|---|---|
| `.github/**` | CI defines the gate; templates and Dependabot configuration too |
| `Makefile`, `pyproject.toml`, `requirements.lock*`, `.pre-commit-config.yaml`, `pyrightconfig.json`, `typings/**` | Build, test, type and lint configuration |
| `scripts/**` | Gate, governance, probe and deploy scripts and their shared helpers; tracking their import closure piecemeal is error-prone |
| `**/conftest.py`, `**/sitecustomize.py`, `**/usercustomize.py`, `**/*.pth`, `pytest.ini`, `setup.cfg`, `tox.ini`, `.coveragerc` | Files that change how tests run |
| `tests/**` | Acceptance; contract tests arrive only on proposal branches |
| `evals/**`, `eval_harness_bridge/**`, `src/mangomas/eval/**` | The measuring instrument |
| `src/mangomas/harness/**` | The safety instrument |
| every entry of `protected_paths` | Public contracts, human-only by ADR-0021 and ADR-0030 |
| `.claude/**`, `AGENTS.md`, `**/CLAUDE.md`, `.mcp.json` | The steering corpus |
| `deploy/**`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.gitleaks.toml`, `.gitleaksignore`, `.env.example`, `.env.gcp-demo` | Deploy, secret scanning and the configuration contract |
| `mango-integration-contracts/**` | A cross-repository contract package |
| `backlog/**`, `specs/**`, `docs/adr/**`, `docs/plans/**`, `docs/analysis/**`, `docs/proposals/**` | Design and acceptance records, written only on proposal branches |
| `CONTRIBUTING.md`, `SECURITY.md`, `NEXT_STEPS.md` | Process and roadmap records |

Deliberately left out, so an approved item may name them in `allowed_paths`:
`src/mangomas/**` except the two instruments and the protected paths; live
docs under `docs/` outside the record directories; `examples/**`; `README.md`;
`CHANGELOG.md`.

`proposal_paths`: `specs/**`, `docs/adr/**`, `docs/plans/**`,
`docs/analysis/**`, `docs/proposals/**`, `backlog/**`, `tests/contract/**`,
`NEXT_STEPS.md`, `CHANGELOG.md`.

## Scenarios (WHEN/THEN)

- WHEN an implementation branch changes `Makefile` THEN the check fails and
  names the path and the matching glob; WHEN it changes only paths inside the
  item's `allowed_paths` and outside the trusted base THEN it passes.
- WHEN a proposal branch changes `src/mangomas/workflow/loader.py` THEN it
  fails; WHEN it changes only `specs/0060-x.md` and
  `tests/contract/LOOP-0007/test_x.py` THEN it passes.
- WHEN a proposal branch adds `tests/contract/LOOP-0007/conftest.py` THEN it
  fails.
- WHEN a branch outside both prefixes changes `Makefile` THEN the check passes
  with "not governed".
- WHEN the base policy cannot be read, or the file listing is truncated, THEN
  the check fails.
- WHEN an implementation branch renames `Makefile` to `docs/Makefile.txt` THEN
  it fails.
- WHEN `LOOP_ENABLED` is not `true` THEN every implementation branch fails and
  proposal branches are unaffected.
- WHEN a PR edits `.github/workflows/loop-scope.yml` THEN the run still uses
  the default branch's copy, and the edit itself is a trusted-base violation.
- WHEN any `MUT-LOOP-*` mutation is applied THEN its named test goes red.

## Config / env additions

| Setting | Default | Purpose |
|---|---|---|
| `[tool.mangomas.governance.loop]` in `pyproject.toml` | absent (every implementation branch fails) | The trusted-base and proposal policy |
| GitHub repository variable `LOOP_ENABLED` | unset (loop off) | Kill switch read only by the scope-check workflow |

Nothing is added to `Settings`: this policy governs the repository, not the
running application.

## Protocol / contract impact

- New/changed protocols: none.
- New error types: none.
- Registry additions: none.
- New script `scripts/check_loop_scope.py`, make target `loop-scope`, workflow
  `loop-scope.yml`.

## Backwards-compatibility

- Ungoverned branches, including every human PR, see one extra passing check
  and nothing else.
- `protected_paths`, the trailer rules and `scripts/check_protected_paths.py`
  are unchanged.

## Test plan

- Unit: `tests/test_check_loop_scope.py`, a pure function over policy, branch
  class and file list, with both directions for every scenario.
- Workflow: `tests/deploy/test_workflow_hardening.py` and
  `tests/deploy/test_ci_make_parity.py` updated for the new workflow and target.
- Sandbox: `tests/harness/` for R8.
- Gates: `MUT-LOOP-*` entries prove each guard can fire.

## Acceptance criteria

- [ ] Every scenario is a test, in both directions.
- [ ] `make gate` and the guard probe are green.
- [ ] `Loop scope check` and `Guard falsifiability probe` are required on `dev`,
      `qa` and `main`, and `CONTRIBUTING.md` lists them.
- [ ] The R7 credential result is recorded in this spec.
- [ ] CHANGELOG updated.
