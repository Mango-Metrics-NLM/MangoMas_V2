"""Contract tests hardening the GitHub Actions workflows (spec-0022 R2, R5).

Two injection/supply-chain rules that were previously held only by convention:

* **No expression interpolation into ``run:`` bodies.** ``deploy.yml`` used to
  inline ``${{ github.event.release.tag_name || github.sha }}`` (and a secret)
  straight into a shell running with ``id-token: write`` — a release tag name
  is event payload, i.e. attacker-influenceable text. ``eval-gate.yml`` already
  practiced the fix (bind the expression to an ``env:`` var, reference the
  shell variable); these tests make that idiom mandatory for every workflow.
* **Third-party actions are SHA-pinned.** A mutable tag like ``@v4`` lets the
  action's owner (or an account takeover — codecov's 2021 uploader compromise
  is the case study) silently swap the code CI runs with repo credentials.
  First-party ``actions/*`` stay tag-pinned by recorded policy (GitHub-owned,
  lower blast radius) — this split is asserted, not implied.

**The forbidden-expression check must be a regex, not a substring.** The first
version of this file tested for the literal ``"${{ github.event."``. GitHub
accepts arbitrary whitespace inside ``${{ }}``, so ``${{github.event.x}}`` —
the very line this test exists to forbid, minus one space — sailed through it.
A guard that a one-character edit defeats is worse than none, because it reads
as protection. The pattern below tolerates any whitespace and covers the whole
attacker-influenced family, not just the one instance that was fixed.

Discovery is guarded (file, run-body and uses counts) so a moved or renamed
workflow directory degrades to a loud failure rather than a vacuously green
pass. The file count is exact — six: ci, deploy, eval-gate, nightly, plus the
two security-scanning workflows (scorecard, codeql) — because a count that
tolerated one fewer would let a deleted workflow go unnoticed by every rule
that iterates them — the same fail-open defect class ``test_env_example_contract.py``
documents. Parsing lives in ``tests/deploy/_workflows.py`` so this suite and
``test_ci_make_parity.py`` cannot drift apart in what they can see.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from tests.deploy import _workflows

_EXPECTED_WORKFLOW_COUNT = 6
# Vacuity floors: low enough that a legitimate change never trips them, high
# enough that a glob matching nothing (or a workflow losing every step) fails
# loudly. Deliberately not the exact counts — see the module docstring.
_MIN_RUN_BODIES = 10
_MIN_THIRD_PARTY_USES = 1
_FIRST_PARTY_OWNER = "actions"
# A commit SHA is 40 hex digits; GitHub accepts either case in `uses:`.
_SHA_HEX_LENGTH = 40
_SHA_REF_RE = re.compile(rf"[0-9a-fA-F]{{{_SHA_HEX_LENGTH}}}")

# Every context whose value an outside contributor can influence, plus
# `secrets.` (which must reach a shell through `env:`, never argv). Whitespace
# inside `${{ }}` is optional and unbounded, hence `\s*`.
_FORBIDDEN_RUN_EXPRESSION_RE = re.compile(
    r"\$\{\{\s*(?:github\.event\b|github\.head_ref\b|github\.ref_name\b|inputs\b|secrets\b)"
)

# The third-party actions this repo has reviewed and pinned. Asserted as a set
# so *adding* one is the reviewed event, rather than a count that silently
# tolerates a swap.
#
# `_action_name` keeps an action's sub-path (`owner/repo/sub`), so each CodeQL
# entry point is listed on its own: the set then pins exactly which parts of
# that repository CI runs, not merely that "some of codeql-action" does.
_EXPECTED_THIRD_PARTY_ACTIONS = frozenset(
    {
        "codecov/codecov-action",
        "github/codeql-action/analyze",
        "github/codeql-action/init",
        "github/codeql-action/upload-sarif",
        "google-github-actions/auth",
        "google-github-actions/setup-gcloud",
        "ossf/scorecard-action",
    }
)


def _action_name(ref: str) -> str:
    return ref.partition("@")[0]


def _is_first_party(ref: str) -> bool:
    return _action_name(ref).startswith(f"{_FIRST_PARTY_OWNER}/")


def test_workflows_were_discovered() -> None:
    """Vacuity guard: an empty glob would green-light every rule below."""
    docs = _workflows.workflow_docs()
    assert len(docs) == _EXPECTED_WORKFLOW_COUNT, sorted(docs)
    assert len(_workflows.run_bodies()) >= _MIN_RUN_BODIES
    assert len(_workflows.uses_refs()) >= _MIN_THIRD_PARTY_USES


def test_every_workflow_declares_at_least_one_run_or_uses_step() -> None:
    """Per-file guard: the aggregate floor above cannot see one file emptying.

    Without this, `deploy.yml` and `eval-gate.yml` could lose every step and
    the forbidden-expression rule would still pass by scanning `ci.yml` alone.
    """
    seen = {where.split(":", 1)[0] for where, _ in _workflows.run_bodies()}
    seen |= {where.split(":", 1)[0] for where, _ in _workflows.uses_refs()}
    assert seen == set(_workflows.workflow_docs())


def test_no_run_body_interpolates_forbidden_expressions() -> None:
    """Event payload and secrets reach shells via env: bindings, never ${{ }}."""
    offenders = [
        (where, match.group(0))
        for where, body in _workflows.run_bodies()
        for match in [_FORBIDDEN_RUN_EXPRESSION_RE.search(body)]
        if match is not None
    ]
    assert offenders == []


def test_forbidden_expression_pattern_is_whitespace_insensitive() -> None:
    """Mutation proof for the guard itself (the bug this file shipped with).

    The original substring check missed `${{github.event.x}}`. If the pattern
    is ever narrowed back to a literal, this fails — so the guard's own
    weakness is now a tested property rather than a latent bypass.
    """
    for probe in (
        "IMAGE=${{github.event.release.tag_name}}",
        "IMAGE=${{  github.event.release.tag_name }}",
        "TAG=${{ github.ref_name }}",
        "X=${{ inputs.thing }}",
        "K=${{ secrets.TOKEN }}",
    ):
        assert _FORBIDDEN_RUN_EXPRESSION_RE.search(probe) is not None, probe
    # And it must not fire on a workflow-defined literal, which is safe.
    assert _FORBIDDEN_RUN_EXPRESSION_RE.search("git fetch ${{ env.BASE_BRANCH }}") is None


def test_third_party_actions_are_sha_pinned() -> None:
    """Every non-actions/* `uses:` ref must be a full 40-hex commit SHA."""
    third_party = [(w, u) for w, u in _workflows.uses_refs() if not _is_first_party(u)]
    assert len(third_party) >= _MIN_THIRD_PARTY_USES, third_party
    unpinned = [(w, u) for w, u in third_party if not _SHA_REF_RE.fullmatch(u.partition("@")[2])]
    assert unpinned == []


def test_third_party_action_set_is_the_reviewed_one() -> None:
    """Adding a third-party action is a reviewed event, not a silent one."""
    names = {_action_name(u) for _, u in _workflows.uses_refs() if not _is_first_party(u)}
    assert names == _EXPECTED_THIRD_PARTY_ACTIONS


# ── Supply-chain baseline (roadmap item 0.3) ─────────────────────────────────

# The one non-first-party, non-action code source a workflow installs: the
# ianshank/Agents eval harness, installed straight from git.
_EXTERNAL_INSTALL_MARKER = "github.com/ianshank/Agents"
_EVAL_GATE_WORKFLOW = "eval-gate.yml"
_DEPENDABOT = _workflows.REPO_ROOT / ".github" / "dependabot.yml"
# Every half of the dependency surface: the actions the workflows run, the
# Python packages the project itself resolves, and the pre-commit hook revs.
# `pre-commit` is not optional garnish — `ruff` and `mypy` are exact-pinned in
# BOTH pyproject.toml and .pre-commit-config.yaml, and
# tests/tooling/test_toolchain_pin_parity.py fails when the two drift. Without
# this ecosystem only the pip half gets update PRs, so every bump arrives
# pre-desynced.
_EXPECTED_DEPENDABOT_ECOSYSTEMS = frozenset({"github-actions", "pip", "pre-commit"})


def test_eval_gate_external_install_has_no_floating_default_ref() -> None:
    """The external harness install must fail closed, never default to `main`.

    ``AGENTS_REF`` used to fall back to the moving ``main`` branch of
    ianshank/Agents, so an unconfigured repo installed whatever that branch
    held at run time. The env expression must carry no branch-name fallback,
    and the run body must refuse an empty ref outright (the fallback's absence
    alone would otherwise install ``Agents@`` — git's syntax error — with a
    message pointing nowhere useful).
    """
    steps = _workflows.jobs(_EVAL_GATE_WORKFLOW)["eval-gate"]["steps"]
    install = [s for s in steps if _EXTERNAL_INSTALL_MARKER in str(s.get("run", ""))]
    assert len(install) == 1, f"expected exactly one external-install step: {install!r}"
    step = install[0]
    assert "'main'" not in step["env"]["AGENTS_REF"], (
        "AGENTS_REF falls back to the moving 'main' branch — pin via AGENTS_HARNESS_REF instead"
    )
    assert 'if [ -z "${AGENTS_REF}" ]' in step["run"], (
        "the install step must refuse an empty AGENTS_REF with a clear error"
    )


def test_dependabot_covers_actions_and_python_ecosystems() -> None:
    """The three expected ecosystems are configured.

    That is the whole of what this test verifies — an earlier docstring claimed
    more. The SHA-pinned actions rot without the ``github-actions`` entry;
    pyproject's ranges rot without ``pip``; and the exact-pinned ruff/mypy revs
    desync from pyproject without ``pre-commit``. Asserted as a superset so
    adding an ecosystem is not a failure.

    **Not** claimed here: that ``requirements.lock`` stays fresh. Dependabot's
    ``pip`` ecosystem reads the manifests it recognises — ``requirements.txt``,
    ``requirements/*.txt``, ``pyproject.toml``, ``setup.py``, ``Pipfile``,
    ``poetry.lock`` — and a file named ``requirements.lock`` is not among them.
    ``git log -- requirements.lock`` bears this out: one commit, the one that
    created it, under a ``pip`` ecosystem configured since. Lockfile freshness is
    enforced mechanically by
    ``tests/deploy/test_lockfile_freshness.py::test_every_runtime_distribution_is_pinned_in_the_lockfile``
    instead, and its CVE exposure by ``make pip-audit``'s second pass. A green
    test asserting an absent control is worse than prose, because prose does not
    read as verified.
    """
    doc = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    ecosystems = {entry["package-ecosystem"] for entry in doc["updates"]}
    assert ecosystems >= _EXPECTED_DEPENDABOT_ECOSYSTEMS, ecosystems


def test_dependabot_watches_integration_contracts() -> None:
    """A contracts-only pydantic bump must be its own Dependabot PR."""
    doc = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    pip_dirs = {
        entry["directory"] for entry in doc["updates"] if entry["package-ecosystem"] == "pip"
    }
    assert "/" in pip_dirs
    assert "/mango-integration-contracts" in pip_dirs


# The lock-stepped toolchain pins (ruff, mypy) have one half in `pip` and the
# other in `pre-commit`. Groups cannot span ecosystems, so a grouped bump of
# either always fails tests/tooling/test_toolchain_pin_parity.py — and takes
# every other package in its monthly group down with it.
_LOCKSTEPPED_EXCLUDES = {
    ("pip", "/"): {"ruff", "mypy"},
    ("pre-commit", "/"): {"*ruff-pre-commit*", "*mirrors-mypy*"},
}


def test_dependabot_groups_exclude_the_lockstepped_toolchain() -> None:
    doc = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    entries = {(e["package-ecosystem"], e["directory"]): e for e in doc["updates"]}
    for key, expected in _LOCKSTEPPED_EXCLUDES.items():
        groups = entries[key].get("groups") or {}
        assert groups, f"{key}: no groups — this guard is moot, delete it with the groups"
        for name, group in groups.items():
            excluded = set(group.get("exclude-patterns") or [])
            assert expected <= excluded, (
                f"{key} group {name!r} must exclude {sorted(expected - excluded)}: a "
                "grouped toolchain bump fails pin parity and blocks the whole group"
            )


# A job that reports a scheduled run's failure somewhere a human will see.
# Recognised by its `if:` guard rather than its name, so renaming the job is
# fine and deleting the guard is not.
_FAILURE_GUARD = "failure()"


def _scheduled_workflows() -> list[str]:
    return [name for name in _workflows.workflow_docs() if "schedule" in _workflows.triggers(name)]


def test_every_scheduled_workflow_reports_its_own_failure() -> None:
    """A scheduled run nobody watches is a gate that reports to no one.

    Push-triggered workflows surface on the PR; a cron run surfaces nowhere.
    GitHub emails only the account that last touched the cron, and only on the
    *first* failure of a consecutive run — so a suite that breaks and stays
    broken goes quiet after night one, which is exactly the shape of the
    long-lived defect a nightly suite exists to catch.

    Checked by the `if:` guard, so the reporting job can be renamed or
    reimplemented freely; only removing the failure path fails this.
    """
    scheduled = _scheduled_workflows()
    assert scheduled, "no scheduled workflow found — has the cron trigger moved?"
    for name in scheduled:
        guarded = [
            job
            for job, spec in _workflows.jobs(name).items()
            if _FAILURE_GUARD in str(spec.get("if", ""))
        ]
        assert guarded, (
            f"{name} runs on a schedule but no job is guarded by `if: {_FAILURE_GUARD}`, "
            "so a failing nightly run notifies nobody"
        )


def test_the_failure_reporter_waits_on_every_scheduled_job() -> None:
    """A reporting job must `needs:` every job it is meant to report on.

    The guard above proves a reporter *exists*; it cannot see that the reporter
    is watching the right things. `needs:` is what makes `if: failure()` fire —
    a job absent from that list can fail every night and the reporter, having
    never depended on it, still succeeds and files nothing.

    That is the fail-open shape, and it costs one line to introduce: adding a
    nightly job and forgetting the `needs:` entry. Adding `embeddings-local`
    (spec-0029 R6) is exactly when it would have happened.
    """
    for name in _scheduled_workflows():
        jobs = _workflows.jobs(name)
        reporters = {job for job, spec in jobs.items() if _FAILURE_GUARD in str(spec.get("if", ""))}
        watched: set[str] = set()
        for reporter in reporters:
            needs = jobs[reporter].get("needs") or []
            watched.update([needs] if isinstance(needs, str) else needs)

        unwatched = sorted(set(jobs) - reporters - watched)
        assert unwatched == [], (
            f"{name}: job(s) {unwatched} are not in any failure reporter's `needs:`, "
            "so their failures notify nobody"
        )


# A shallow checkout is the default. The `git` gitleaks pass walks committed
# history, so on `fetch-depth: 1` it scans a single commit, finds nothing, and
# exits 0 — the same fail-open shape as every other defect on this branch.
_FULL_HISTORY = 0
_HISTORY_SCANNING_STEP = "make secret-scan"


def _jobs_running(command: str) -> list[tuple[str, str, dict[str, Any]]]:
    """Every (workflow, job name, job spec) whose steps run ``command``."""
    found = []
    for workflow, doc in _workflows.workflow_docs().items():
        for name, spec in (doc.get("jobs") or {}).items():
            runs = [str(step.get("run", "")) for step in (spec.get("steps") or [])]
            if any(command in run for run in runs):
                found.append((workflow, name, spec))
    return found


def test_history_scanning_jobs_check_out_full_history() -> None:
    """`make secret-scan`'s git pass needs every commit, not just the tip.

    Nothing asserted this. A `fetch-depth` left at its default turns the
    history pass into a one-commit scan that reports "no leaks found" and goes
    green, which is indistinguishable from a clean history — and the pass
    exists precisely because a credential can be committed and then removed
    from the working tree.
    """
    jobs = _jobs_running(_HISTORY_SCANNING_STEP)
    assert jobs, f"no job runs {_HISTORY_SCANNING_STEP!r} — has the target been renamed?"

    shallow = []
    for workflow, name, spec in jobs:
        checkouts = [
            step
            for step in (spec.get("steps") or [])
            if str(step.get("uses", "")).startswith("actions/checkout")
        ]
        assert checkouts, f"{workflow}:{name} scans history without checking anything out"
        for step in checkouts:
            depth = (step.get("with") or {}).get("fetch-depth")
            if depth != _FULL_HISTORY:
                shallow.append(f"{workflow}:{name} (fetch-depth={depth!r})")

    assert shallow == [], (
        "history-scanning job(s) use a shallow checkout, so the gitleaks `git` "
        f"pass would scan one commit and pass vacuously: {shallow}"
    )


# ── Runner hygiene: bounded jobs, superseded runs cancelled ──────────────────
#
# GitHub's default job timeout is 360 minutes. Two jobs set their own bound
# (docker-smoke, guard-probe) and recorded why: a hung registry or a hung gate
# must not hold a runner for six hours. The other eighteen inherited the
# default, so the same hang anywhere else was six hours of queue for every PR
# behind it. The bound is asserted as a *ceiling* too: `timeout-minutes: 360`
# would satisfy a presence check while bounding nothing.
#
# Without a `concurrency` group, every push to a PR branch queued a fresh run
# behind the previous one instead of replacing it, so a three-push review
# round paid for three full pipelines and the reviewer read the stalest. The
# deploy workflow is the one exception — a half-applied deploy must finish,
# never be cancelled — and that exception is pinned here so a copy-paste of
# the CI block into deploy.yml fails loudly.
#
# Cancelling is for PR churn, not for trunk history. A workflow that runs on
# `push` (to the trunks) may cancel only its `pull_request` runs: an
# unconditional `true` lets the second of two quick merges cancel the first
# merge commit's run, leaving it with no completed result.
_MAX_JOB_TIMEOUT_MINUTES = 60
_DEPLOY_WORKFLOW = "deploy.yml"
_CANCEL_PULL_REQUESTS_ONLY = "${{ github.event_name == 'pull_request' }}"
_CANCELS_SUPERSEDED_RUNS: tuple[object, ...] = (True, _CANCEL_PULL_REQUESTS_ONLY)


def _all_jobs() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (workflow, name, spec)
        for workflow, doc in _workflows.workflow_docs().items()
        for name, spec in (doc.get("jobs") or {}).items()
    ]


def test_every_job_declares_a_bounded_timeout() -> None:
    """Each job carries `timeout-minutes` in (0, 60]: present, and a real bound."""
    jobs = _all_jobs()
    assert jobs, "no jobs discovered — has the workflows directory moved?"
    unbounded = [
        f"{workflow}:{name} (timeout-minutes={spec.get('timeout-minutes')!r})"
        for workflow, name, spec in jobs
        if not isinstance(spec.get("timeout-minutes"), int)
        or not 0 < spec["timeout-minutes"] <= _MAX_JOB_TIMEOUT_MINUTES
    ]
    assert unbounded == [], (
        "job(s) without a bounded `timeout-minutes` inherit GitHub's 360-minute "
        f"default and hold a runner for six hours on a hang: {unbounded}"
    )


def test_every_workflow_declares_a_concurrency_group() -> None:
    """A superseded run is cancelled rather than queued (except a deploy)."""
    docs = _workflows.workflow_docs()
    assert docs, "no workflows discovered"
    for name, doc in docs.items():
        concurrency = doc.get("concurrency")
        assert isinstance(concurrency, dict), f"{name} declares no workflow-level concurrency"
        group = concurrency.get("group")
        assert isinstance(group, str) and "github.ref" in group, (
            f"{name}: the concurrency group must be keyed on github.ref so runs for "
            f"different branches never cancel each other (got {group!r})"
        )
        cancel = concurrency.get("cancel-in-progress")
        assert isinstance(cancel, bool) or cancel == _CANCEL_PULL_REQUESTS_ONLY, (
            f"{name}: cancel-in-progress must be stated explicitly — true, false, or "
            f"{_CANCEL_PULL_REQUESTS_ONLY} (got {cancel!r})"
        )


def test_only_the_deploy_workflow_refuses_to_cancel_in_progress() -> None:
    """CI runs are disposable; a deploy that has started must run to completion."""
    docs = _workflows.workflow_docs()
    assert _DEPLOY_WORKFLOW in docs
    cancels = {name: doc["concurrency"]["cancel-in-progress"] for name, doc in docs.items()}
    assert cancels[_DEPLOY_WORKFLOW] is False, "a deploy must never be cancelled mid-apply"
    cancelling = {name for name, cancel in cancels.items() if cancel in _CANCELS_SUPERSEDED_RUNS}
    assert cancelling == set(docs) - {_DEPLOY_WORKFLOW}, cancels


def test_a_push_triggered_workflow_never_cancels_a_push_run() -> None:
    """A trunk merge keeps a completed run; only PR runs are disposable."""
    docs = _workflows.workflow_docs()
    push_triggered = [name for name in docs if "push" in _workflows.triggers(name)]
    assert push_triggered, "no push-triggered workflow — has ci.yml lost its trunk trigger?"
    unconditional = [
        name for name in push_triggered if docs[name]["concurrency"]["cancel-in-progress"] is True
    ]
    assert unconditional == [], (
        f"{unconditional}: `cancel-in-progress: true` also cancels trunk push runs; "
        f"use {_CANCEL_PULL_REQUESTS_ONLY} (or false, for a deploy)"
    )


# ── Security-scanning workflows: least privilege, asserted ────────────────────
#
# Scorecard and CodeQL are the first workflows outside `deploy.yml` that need
# a write-scoped token (`security-events: write` to upload SARIF; Scorecard's
# `id-token: write` to publish results). The grants belong on the *job*, so a
# compromised or misbehaving step in any other job of the same workflow never
# inherits them, and the workflow-level block stays read-only. `contents:
# write` is granted nowhere: no workflow in this repository pushes, tags or
# releases through GITHUB_TOKEN, so any appearance of it is a mistake.
_SECURITY_SCANNING_WORKFLOWS = ("scorecard.yml", "codeql.yml")
_SARIF_UPLOAD_SCOPE = "security-events"
_WRITE = "write"
_FORBIDDEN_TOKEN_GRANT = ("contents", _WRITE)


def _permission_grants(block: object) -> dict[str, str]:
    """Normalise a `permissions:` block to `{scope: level}`.

    GitHub accepts a mapping, or the shorthand strings `read-all` /
    `write-all`. `write-all` is flattened to a single synthetic grant so the
    forbidden-grant check below cannot be dodged by spelling the mapping as a
    string.
    """
    if isinstance(block, dict):
        return {str(scope): str(level) for scope, level in block.items()}
    if block == "write-all":
        return {_FORBIDDEN_TOKEN_GRANT[0]: _WRITE}
    return {}


def test_security_scanning_workflows_declare_minimal_write_permissions() -> None:
    """`security-events: write` is job-scoped; nothing anywhere gets `contents: write`."""
    docs = _workflows.workflow_docs()
    for name in _SECURITY_SCANNING_WORKFLOWS:
        assert name in docs, f"{name} is missing — the security-scanning workflow set has shrunk"
        top = _permission_grants(docs[name].get("permissions"))
        assert _WRITE not in top.values(), (
            f"{name}: workflow-level permissions grant write ({top}); scope the grant to the job"
        )
        uploading = [
            job
            for job, spec in _workflows.jobs(name).items()
            if _permission_grants(spec.get("permissions")).get(_SARIF_UPLOAD_SCOPE) == _WRITE
        ]
        assert uploading, f"{name}: no job grants `{_SARIF_UPLOAD_SCOPE}: {_WRITE}` to upload SARIF"

    offenders = [
        f"{workflow}:{job}"
        for workflow, job, spec in _all_jobs()
        if _permission_grants(spec.get("permissions")).get(_FORBIDDEN_TOKEN_GRANT[0]) == _WRITE
    ] + [
        f"{workflow} (workflow-level)"
        for workflow, doc in docs.items()
        if _permission_grants(doc.get("permissions")).get(_FORBIDDEN_TOKEN_GRANT[0]) == _WRITE
    ]
    assert offenders == [], f"`contents: write` is granted by {offenders}; no workflow needs it"
