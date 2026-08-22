"""Shared reader for the GitHub Actions workflow files.

Three suites need the same three things — parse the workflow YAML, walk every
``run:`` body, walk every ``uses:`` reference — and before this each one
re-implemented them. ``tests/deploy/test_ci_make_parity.py`` and
``tests/deploy/test_workflow_hardening.py`` had independent copies of the
``yaml.safe_load`` call, the repo-root anchor, and the step-walking loop, which
meant a fix to one (for example: also globbing ``*.yaml``) silently left the
other blind.

Two subtleties the callers must not re-learn:

* **``*.yaml`` counts.** GitHub honours both extensions. Globbing only ``*.yml``
  makes a ``deploy.yaml`` invisible to every rule — a fail-open of exactly the
  kind these contracts exist to prevent.
* **Job-level ``uses:``.** A reusable-workflow call lives at
  ``jobs.<id>.uses``, not under ``steps``. Walking steps alone misses it, so an
  unpinned ``org/repo/.github/workflows/x.yml@main`` would slip past the
  SHA-pin rule.

PyYAML parses the top-level ``on:`` key as the boolean ``True`` (YAML 1.1).
Nothing here indexes it, but a caller that wants triggers must accept either
form.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
# Both extensions are honoured by GitHub; see the module docstring.
WORKFLOW_GLOBS: tuple[str, ...] = ("*.yml", "*.yaml")


def workflow_paths() -> list[Path]:
    """Every workflow file, both extensions, sorted by name."""
    found: list[Path] = []
    for pattern in WORKFLOW_GLOBS:
        found.extend(WORKFLOWS_DIR.glob(pattern))
    return sorted(found, key=lambda p: p.name)


def workflow_docs() -> dict[str, dict[str, Any]]:
    """Parsed workflow documents keyed by filename."""
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in workflow_paths()}


def jobs(name: str) -> dict[str, Any]:
    """Return the ``jobs`` mapping of one workflow file."""
    return dict(workflow_docs()[name]["jobs"])


def _job_items() -> list[tuple[str, str, dict[str, Any]]]:
    items: list[tuple[str, str, dict[str, Any]]] = []
    for filename, doc in workflow_docs().items():
        for job_name, job in (doc.get("jobs") or {}).items():
            items.append((filename, job_name, job))
    return items


def run_bodies() -> list[tuple[str, str]]:
    """``(where, body)`` for every ``run:`` step across every workflow."""
    bodies: list[tuple[str, str]] = []
    for filename, job_name, job in _job_items():
        for step in job.get("steps") or []:
            if "run" in step:
                bodies.append((f"{filename}:{job_name}", str(step["run"])))
    return bodies


def uses_refs() -> list[tuple[str, str]]:
    """``(where, ref)`` for every ``uses:``, step-level *and* job-level."""
    refs: list[tuple[str, str]] = []
    for filename, job_name, job in _job_items():
        # Job-level `uses:` is a reusable-workflow call — not under `steps`.
        if "uses" in job:
            refs.append((f"{filename}:{job_name}", str(job["uses"])))
        for step in job.get("steps") or []:
            if "uses" in step:
                refs.append((f"{filename}:{job_name}", str(step["uses"])))
    return refs
