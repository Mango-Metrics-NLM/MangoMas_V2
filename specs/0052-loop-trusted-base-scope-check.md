# Spec-0052: Loop trusted base and base-anchored scope check

- **Status:** Draft
- **Linked ADR:** [ADR-0042](../docs/adr/0042-governed-self-improvement-loop.md) (Proposed)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Governed self-improvement loop: decision records (ADR-0042 / spec-0052..0055)`
- **Delivery plan:** [milestone M1](../docs/plans/20261123T000000Z-governed-self-improvement-loop-plan.md)

## Problem

A loop that writes code must not be able to change what judges it. The
protected set is 13 exact paths (`pyproject.toml:147-167`) matched by exact
intersection (`scripts/check_protected_paths.py:191`). CI workflows, the
`Makefile`, most gate scripts, `tests/`, `evals/` and `.claude/` are outside it,
and a `pull_request` run executes the PR's own copy of all of them.
ADR-0030:86-90 names a required status check as the only defence for `ci.yml`
and the `Makefile`, but a required check is matched by name.

Two of ADR-0030's choices are deliberate trade-offs for human-authored PRs: a
bare `BREAKING-CHANGE:` approves every touched protected path
(`scripts/_governance.py:170-175`, ADR-0030:60-66), and an unreadable base
policy falls back to the working tree with a loud warning
(`scripts/check_protected_paths.py:165-171`; fail-closed was rejected at
ADR-0030:124-126). Both are mutation-guarded (`MUT-GOV-02`, `MUT-GOV-03`). They
are reasonable when a person writes the trailer. They are not when the author
is optimising against the gate, so the loop check makes the opposite choices.

Finally, implementer sessions act with the maintainer's GitHub identity. Any
GitHub-side control their credential can change, such as a commit status, a
repository variable or a ruleset, is no control over them.

## Requirements

- **R1 Policy.** A `[tool.mangomas.governance.loop]` table in `pyproject.toml`
  (protected, and read from the default branch) holding
  `implementation_prefix = "claude/loop-"`, `proposal_prefix = "claude/proposal-"`,
  `trusted_base`, `work_area`, `proposal_new_paths` and `proposal_index_files`
  (glob lists), plus `max_open_implementation_prs = 1` and
  `max_consecutive_failures`. `**` matches any number of path segments and `*`
  matches within one, on normalised repo paths. If the table is missing, every
  `claude/**` head fails and every other head passes with a warning, so a
  human PR can restore it.
- **R2 Classification the session cannot choose.** The branch prefix is a
  label, not an identity. Before the loop runs (M5), record one of:
  (a) loop sessions act through a dedicated identity, a GitHub App or machine
  user, and the check classifies by PR author; or (b) a verified guarantee
  that a session can push only the branch the driver assigns
  (`claude/loop-<ID>-<n>` or `claude/proposal-<ID>`). Until one is recorded,
  the prefix rules catch mistakes, not evasion, and the loop does not run.
  Governed heads must come from this repository and target `dev`; a governed
  prefix from a fork, or aimed at another base, fails. The item ID comes from
  the branch name, which GitHub fixes when the PR opens.
- **R3 Implementation rules.** Every changed path must match the item's
  `allowed_paths` and lie inside `work_area`, and none may match
  `trusted_base`. The rule is evaluated per changed file and the trusted base
  wins, so no glob-to-glob intersection is needed. Renames count both paths;
  deletions and mode changes count. The check also enforces, from the GitHub
  API:
  - the item's `max_diff_lines` (additions plus deletions);
  - no other open implementation PR (`max_open_implementation_prs`);
  - every `depends_on` item already has a merged implementation PR;
  - the item's `min_stage` is at or below the repository variable `LOOP_STAGE`;
  - fewer than `budget.max_attempts` earlier attempts for the item;
  - fewer than `max_consecutive_failures` consecutive closed, unmerged
    implementation PRs.
- **R4 Suppressions.** An implementation diff that adds `# pragma: no cover`,
  `# noqa`, `# ruff: noqa`, `# type: ignore`, `# mypy: ignore-errors`,
  `pytest.skip(` or a `skip`, `skipif` or `xfail` mark fails unless the item
  lists that suppression in `allowed_suppressions`. A truncated patch fails.
- **R5 Proposal rules.** A proposal branch may add new files only under
  `proposal_new_paths` (`specs/`, `docs/adr/`, `docs/plans/`, `docs/analysis/`,
  `docs/proposals/`, `backlog/`, `tests/contract/`), and may modify only
  `proposal_index_files` (`specs/README.md`, `NEXT_STEPS.md`, `CHANGELOG.md`).
  Editing, renaming or deleting any existing record (an ADR, spec, plan or
  backlog item) fails, which is what keeps "Proposed → Accepted" a human edit.
  So does adding any test-runner configuration file (`conftest.py`,
  `pytest.ini`, `.pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`,
  `.coveragerc`) anywhere.
- **R6 Fail closed** on: an unreadable base policy (for `claude/**` heads); a
  truncated or failed file listing or patch; an unknown item ID; a governed
  head from a fork or to a base other than `dev`; and, for implementation
  branches, `LOOP_ENABLED` not set to `true`.
- **R7 Anchor.** `.github/workflows/loop-scope.yml` runs on
  `pull_request_target` (opened, synchronize, reopened, edited) for base
  branches `dev`, `qa` and `main`; the last two exist only so that a governed
  head aimed there fails visibly. GitHub always runs the default branch's copy
  of a workflow for this event. The job:
  - checks out only the default branch;
  - reads PR files, patches and other PRs through the API;
  - never checks out or executes PR code;
  - holds only `contents: read` and `pull-requests: read`, and uses no secrets;
  - passes event values through `env:`, never interpolated into `run:`
    (`tests/deploy/test_workflow_hardening.py`);
  - keys its concurrency group per PR
    (`loop-scope-${{ github.ref }}-${{ github.event.pull_request.number }}`),
    because under `pull_request_target` `github.ref` is the default branch for
    every PR, and the hardening test requires the group to contain it;
  - is named `Loop scope check`.
- **R8 Self-protection.** `scripts/check_loop_scope.py` and
  `.github/workflows/loop-scope.yml` join `protected_paths` and
  `GOVERNANCE_SURFACE`. Both are low-churn, and ADR-0030's rule applies: a
  gate's own definition must leave a record when it changes.
- **R9 Required, with a pinned source.** `Loop scope check` and
  `Guard falsifiability probe` become required on `dev`, `qa` and `main`, with
  the expected source set to GitHub Actions. `CONTRIBUTING.md` lists both, and
  its "Job name in `ci.yml`" column becomes "Job name (workflow)".
- **R10 Credential.** Before M5, verify and record that the implementer's
  credential has no write access to workflows, commit statuses, check runs,
  repository variables or administration, and no ruleset bypass. If any of
  these cannot be removed, the loop does not run. Option (a) of R2 is the
  likely way to meet this.
- **R11 Mutation proof.** `scripts/guard_probe_manifest.json` gains
  `MUT-LOOP-*` entries that must each turn a named test red: removing a
  `trusted_base` glob, flipping fail-closed, accepting a truncated listing,
  dropping the suppression rule, and skipping the open-PR check.

## Trusted base (first version)

| Globs | Why |
|---|---|
| `.github/**` | CI defines the gate; templates and Dependabot configuration too |
| `Makefile`, `GNUmakefile`, `makefile`, `requirements*.txt`, `requirements.lock*`, `uv.lock`, `.python-version`, `.pre-commit-config.yaml`, `pyrightconfig.json`, `.importlinter`, `typings/**` | Build, dependency and tool configuration; GNU make reads `GNUmakefile` and `makefile` before `Makefile` |
| `**/pyproject.toml`, `**/ruff.toml`, `**/.ruff.toml`, `**/mypy.ini`, `**/.mypy.ini`, `**/pytest.ini`, `**/.pytest.ini`, `**/tox.ini`, `**/setup.cfg`, `**/.coveragerc`, `**/.gitignore`, `**/.ignore` | Tool configuration at any depth: ruff uses the nearest config and honours ignore files; a nested ini file can become pytest's |
| `**/conftest.py`, `**/sitecustomize.py`, `**/usercustomize.py`, `**/*.pth`, `**/*.pyi` | Files that change how tests or type-checking run; mypy prefers a sibling stub |
| `scripts/**` | Gate, governance, probe and deploy scripts and their shared helpers; tracking their import closure piecemeal is error-prone |
| `tests/**`, `examples/**` | Acceptance, and the fixtures tests load; contract tests arrive only on proposal branches |
| `evals/**`, `eval_harness_bridge/**`, `src/mangomas/eval/**`, `src/mangomas/cli/commands/eval.py`, `src/mangomas/cli/commands/_eval_config.py`, `src/mangomas/cli/exit_codes.py`, `src/mangomas/config/evaluation.py` | The measuring instrument, including its CLI and settings |
| `src/mangomas/harness/**`, `src/mangomas/config/harness.py`, `src/mangomas/config/repair.py`, `src/mangomas/composition/harness.py`, `src/mangomas/composition/repair.py` | The safety instrument and its wiring |
| `src/mangomas/core/**` and every entry of `protected_paths` | Public contracts. `protected_paths` names exact files, while `CONTRIBUTING.md` treats all of `core/orchestrator/` as the dispatch surface; marker-gated by ADR-0021 and ADR-0030, and off-limits to the loop by ADR-0042 |
| `.claude/**`, `**/.claude/**`, `AGENTS.md`, `**/AGENTS.md`, `**/CLAUDE.md`, `.mcp.json` | The steering corpus, including directory-scoped skills |
| `deploy/**`, `Dockerfile*`, `.dockerignore`, `docker-compose.yml`, `.gitleaks.toml`, `.gitleaksignore`, `.env.example`, `.env.gcp-demo` | Deploy, secret scanning and the configuration contract |
| `mango-integration-contracts/**` | A cross-repository contract package |
| `backlog/**`, `specs/**`, `docs/adr/**`, `docs/plans/**`, `docs/analysis/**`, `docs/proposals/**` | Design and acceptance records, added only on proposal branches |
| `CONTRIBUTING.md`, `SECURITY.md`, `NEXT_STEPS.md` | Process and roadmap records |

`work_area`, the positive allowlist an item's `allowed_paths` must sit inside:
`src/mangomas/**`, live docs under `docs/`, `README.md` and `CHANGELOG.md`, in
every case minus the trusted base.

## Scenarios (WHEN/THEN)

- WHEN an implementation branch changes `Makefile`, or adds
  `src/mangomas/adapters/ruff.toml` or a `.pyi` stub, THEN the check fails and
  names the path and the matching glob; WHEN it changes only paths inside the
  item's `allowed_paths` and the work area THEN it passes.
- WHEN an implementation diff adds `# type: ignore` and the item does not allow
  it THEN the check fails; WHEN the item allows it THEN it passes.
- WHEN another implementation PR is open, or the diff exceeds `max_diff_lines`,
  or a `depends_on` item is unmerged, or `min_stage` is above `LOOP_STAGE`,
  THEN the check fails; WHEN none of these holds THEN it passes.
- WHEN a proposal branch edits an existing ADR THEN it fails; WHEN it adds a new
  ADR and a row to `specs/README.md` THEN it passes.
- WHEN a proposal branch adds `tests/contract/loop_0007/pytest.ini` THEN it
  fails.
- WHEN a `claude/loop-*` head comes from a fork, or targets `qa`, THEN it fails.
- WHEN the policy table is missing THEN `claude/**` heads fail and other heads
  pass with a warning.
- WHEN the file listing or a patch is truncated THEN the check fails.
- WHEN an implementation branch renames `Makefile` to `docs/Makefile.txt` THEN
  it fails.
- WHEN `LOOP_ENABLED` is not `true` THEN every implementation branch fails, and
  proposal branches are unaffected.
- WHEN a PR edits `.github/workflows/loop-scope.yml` THEN the run still uses
  the default branch's copy, and the edit itself is a trusted-base violation.
- WHEN any `MUT-LOOP-*` mutation is applied THEN its named test goes red.

## Config / env additions

| Setting | Default | Purpose |
|---|---|---|
| `[tool.mangomas.governance.loop]` in `pyproject.toml` | absent: every `claude/**` head fails | Trusted base, work area, proposal paths and limits |
| GitHub repository variable `LOOP_ENABLED` | unset: off | When not `true`, the scope check fails every implementation PR and the driver (M5) starts no session. Sessions already running are not stopped |
| GitHub repository variable `LOOP_STAGE` | unset: `shadow` | The stage the maintainer has promoted the loop to |

Nothing is added to `Settings`: this policy governs the repository, not the
running application.

## Protocol / contract impact

- New/changed protocols: none.
- New error types: none.
- Registry additions: none.
- New script `scripts/check_loop_scope.py`, make target `loop-scope`, workflow
  `loop-scope.yml`; `protected_paths` and `GOVERNANCE_SURFACE` gain two entries.

## Backwards-compatibility

- Heads outside `claude/**`, including every human PR, see one extra passing
  check and nothing else.
- Interactive Claude Code sessions keep using `claude/<name>` branches. Those
  pass as ungoverned until R2 is settled, which is why the loop cannot run
  before then.
- `protected_paths` keeps its 13 entries plus R8's two, and the trailer rules
  and `scripts/check_protected_paths.py` are unchanged.

## Out of scope

The repair sandbox's staging hole (a staged `conftest.py` reaches a pytest
oracle; `src/mangomas/harness/isolation/base.py:97-112` refuses only startup
hooks) is real but belongs on the ADR-0039/0040 line: the loop does not use
the repair sandbox.

## Test plan

- Unit: `tests/test_check_loop_scope.py`, a pure function over policy, branch
  class, PR metadata and file list, with both directions for every scenario;
  registered in `SCRIPTS_TESTS` (`Makefile:28-31`).
- Workflow: `tests/deploy/test_workflow_hardening.py` (workflow count 6 → 7,
  per-PR concurrency group) and `tests/deploy/test_ci_make_parity.py`.
- Governance: `tests/harness/test_governance.py` for R8's containment.
- Gates: `MUT-LOOP-*` entries prove each guard can fire.

## Acceptance criteria

- [ ] Every scenario is a test, in both directions.
- [ ] `make gate` and the guard probe are green.
- [ ] `Loop scope check` and `Guard falsifiability probe` are required on
      `dev`, `qa` and `main` with their source pinned, and `CONTRIBUTING.md`
      lists both.
- [ ] The R2 and R10 results are recorded in this spec.
- [ ] CHANGELOG updated, naming the tests that lock this in.
