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
pass — the same fail-open defect class ``test_env_example_contract.py``
documents. Parsing lives in ``tests/deploy/_workflows.py`` so this suite and
``test_ci_make_parity.py`` cannot drift apart in what they can see.
"""

from __future__ import annotations

import re
from typing import Any

from tests.deploy import _workflows

_EXPECTED_WORKFLOW_COUNT = 4
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
_EXPECTED_THIRD_PARTY_ACTIONS = frozenset(
    {
        "codecov/codecov-action",
        "google-github-actions/auth",
        "google-github-actions/setup-gcloud",
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
