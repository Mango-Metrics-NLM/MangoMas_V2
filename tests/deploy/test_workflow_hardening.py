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
  lower blast radius) — this split is asserted, not implied, so a Dependabot
  or human edit that flips it fails here by name.

Discovery is guarded (file and run-body counts) so a moved workflow directory
degrades to a loud failure, never a vacuously green pass — the same fail-open
defect class ``tests/deploy/test_env_example_contract.py`` documents.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"

_EXPECTED_WORKFLOW_COUNT = 3
_MIN_RUN_BODIES = 10
_MIN_THIRD_PARTY_USES = 3
_FIRST_PARTY_PREFIX = "actions/"
_SHA_REF_RE = re.compile(r"[0-9a-f]{40}")

# Expression fragments that must never appear inside a run: body. Anything
# under github.event.* is webhook payload; head_ref is attacker-named on
# fork PRs; a ${{ secrets.* }} inlined into a command line can leak through
# argv/process listings and invites the same injection shape.
_FORBIDDEN_RUN_FRAGMENTS = (
    "${{ github.event.",
    "${{ github.head_ref",
    "${{ secrets.",
)


def _workflow_docs() -> dict[str, dict[str, Any]]:
    # PyYAML parses the top-level `on:` key as boolean True (YAML 1.1) — only
    # `jobs` is indexed here, so that quirk is irrelevant but worth noting for
    # the next editor (see test_ci_make_parity.py for the full story).
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(_WORKFLOWS_DIR.glob("*.yml"))
    }


def _run_bodies() -> list[tuple[str, str]]:
    bodies: list[tuple[str, str]] = []
    for name, doc in _workflow_docs().items():
        for job_name, job in doc["jobs"].items():
            for step in job.get("steps", []):
                if "run" in step:
                    bodies.append((f"{name}:{job_name}", str(step["run"])))
    return bodies


def _uses_refs() -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for name, doc in _workflow_docs().items():
        for job_name, job in doc["jobs"].items():
            for step in job.get("steps", []):
                if "uses" in step:
                    refs.append((f"{name}:{job_name}", str(step["uses"])))
    return refs


def test_workflows_were_discovered() -> None:
    """Vacuity guard: an empty glob would green-light every rule below."""
    docs = _workflow_docs()
    assert len(docs) == _EXPECTED_WORKFLOW_COUNT, sorted(docs)
    assert len(_run_bodies()) >= _MIN_RUN_BODIES


def test_no_run_body_interpolates_forbidden_expressions() -> None:
    """Event payload and secrets reach shells via env: bindings, never ${{ }}."""
    offenders = [
        (where, fragment)
        for where, body in _run_bodies()
        for fragment in _FORBIDDEN_RUN_FRAGMENTS
        if fragment in body
    ]
    assert offenders == []


def test_third_party_actions_are_sha_pinned() -> None:
    """Every non-actions/* `uses:` ref must be a full 40-hex commit SHA."""
    third_party = [
        (where, uses) for where, uses in _uses_refs() if not uses.startswith(_FIRST_PARTY_PREFIX)
    ]
    assert len(third_party) >= _MIN_THIRD_PARTY_USES, third_party
    unpinned = [
        (where, uses)
        for where, uses in third_party
        if not _SHA_REF_RE.fullmatch(uses.partition("@")[2])
    ]
    assert unpinned == []
