"""In-package governance primitives for the Claude Code harness/hook layer.

Ported from ``origin/main``'s ``harness/governance.py`` (ADR-0011 there) and
adapted for ADR-0021 / spec-0017: rather than hardcoding the protected-path
set and marker aliases as module constants, both are read from
``pyproject.toml``'s ``[tool.mangomas.governance]`` table via stdlib
``tomllib`` — the single source of truth shared with
``scripts/check_protected_paths.py`` (the CI gate) and
``scripts/lint_agent_frontmatter.py`` (the ``PreToolUse`` advisory hook).

That last point is why this module is **not** imported by either script:
both must work in an interpreter without ``mangomas`` installed (the
``PreToolUse`` hook in particular must run before ``pip install -e .`` has
ever happened), so each keeps its own small, self-contained TOML-reading
helper (``scripts/_governance.py``) rather than depending on this package.
This module currently has no in-package functional consumer either — its
symbols are re-exported via ``harness/__init__.py`` for external/API-surface
use and exercised only by ``tests/harness/test_governance.py`` — and it
exists in ``src/mangomas/`` for coverage visibility: logic left only in
``scripts/`` is invisible to the ``--cov=mangomas`` gate (``pyproject.toml``'s
``[tool.coverage.run] source = ["mangomas"]``).
"""

from __future__ import annotations

import logging
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_DEFAULT_PYPROJECT_PATH: Final[Path] = Path("pyproject.toml")

# Fallback values, used only if pyproject.toml's [tool.mangomas.governance]
# table can't be read. Never an empty protected-path set — that would
# silently disable protection instead of degrading safely. Kept in lock-step
# with the "File Ownership" documentation in CLAUDE.md.
_FALLBACK_PROTECTED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "src/mangomas/core/agent.py",
        "src/mangomas/core/orchestrator/__init__.py",
        "src/mangomas/core/orchestrator/_client.py",
        "src/mangomas/core/structured.py",
        "src/mangomas/core/tools.py",
        "src/mangomas/errors.py",
        "src/mangomas/registry.py",
        ".mcp.json",
        "pyproject.toml",
        "scripts/_governance.py",
        "scripts/check_protected_paths.py",
        "sitecustomize.py",
        "src/mangomas/harness/governance.py",
    }
)
_FALLBACK_BREAKING_CHANGE_MARKER_ALIASES: Final[frozenset[str]] = frozenset(
    {"BREAKING-CHANGE", "# approved-breaking-change"}
)


def _load_governance(
    pyproject_path: Path = _DEFAULT_PYPROJECT_PATH,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(protected_paths, marker_aliases)`` from *pyproject_path*."""
    try:
        doc = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        governance = doc["tool"]["mangomas"]["governance"]
        protected = frozenset(governance["protected_paths"])
        aliases = frozenset(governance["breaking_change_marker_aliases"])
        if protected and aliases:
            return protected, aliases
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        logger.warning(
            "Could not read [tool.mangomas.governance] from %s; using fallback defaults (%s)",
            pyproject_path,
            exc,
        )
    return _FALLBACK_PROTECTED_PATHS, _FALLBACK_BREAKING_CHANGE_MARKER_ALIASES


PROTECTED_PATHS, BREAKING_CHANGE_MARKER_ALIASES = _load_governance()
BREAKING_CHANGE_MARKER: Final[str] = "BREAKING-CHANGE"

# The files the protected-path mechanism is *itself* made of. A gate whose own
# definition is editable without leaving a record is a gate the governed tree
# can quietly retire, so every entry here is expected to appear in
# :data:`PROTECTED_PATHS` — the containment is asserted by
# ``tests/harness/test_governance.py``, not assumed.
#
# Deliberately narrower than "everything the gate touches". Three files were
# considered and left out, because a marker requirement on a high-churn file
# devalues the marker on the six core contracts (a trailer reviewers stop
# reading governs nothing):
#
# * ``Makefile`` and ``.github/workflows/ci.yml`` — they *invoke* the gate but
#   do not define it, they change with routine CI work, and the control that
#   actually defends them is branch protection (a required status check),
#   which lives outside this repository entirely.
# * ``scripts/lint_agent_frontmatter.py`` — advisory-only by ADR-0021 and the
#   highest-churn script in the tree; its copy of the fallback set is already
#   pinned against drift by ``test_scripts_fallback_matches_the_pyproject_table``.
#
# The load-bearing control is not this set: it is
# ``scripts/check_protected_paths.py`` reading the policy from the *base ref*
# (ADR-0030), so a branch cannot shrink the set it is judged by. This set makes
# a change to the mechanism visible in ``git log``; that one makes shrinking it
# ineffective.
GOVERNANCE_SURFACE: Final[frozenset[str]] = frozenset(
    {
        # The policy table itself — the single source of truth every consumer reads.
        "pyproject.toml",
        # The authoritative CI gate.
        "scripts/check_protected_paths.py",
        # The shared, stdlib-only loader + marker matcher both scripts use.
        "scripts/_governance.py",
        # This module: the in-package mirror, and the coverage-visible copy.
        "src/mangomas/harness/governance.py",
        # Auto-imported by every interpreter that can see the repo root, and it
        # mutates PYTEST_ADDOPTS — which carries --cov-fail-under and -k, so an
        # edit here can disarm the test and coverage gates without touching them.
        "sitecustomize.py",
        # Declares the MCP servers a session loads; cloud sessions load them
        # with no approval step.
        ".mcp.json",
    }
)


def unprotected_governance_surface(
    protected_paths: frozenset[str] | None = None,
) -> frozenset[str]:
    """Return the :data:`GOVERNANCE_SURFACE` entries *not* in *protected_paths*.

    Empty is the healthy answer. Returns the offenders rather than a bool so a
    failing assertion names the files a contributor has to act on, instead of
    reporting ``False``.

    *protected_paths* defaults to the live :data:`PROTECTED_PATHS`; it is a
    parameter so a caller can check a *candidate* policy — e.g. the base ref's
    table — without reimporting the module.
    """
    resolved = PROTECTED_PATHS if protected_paths is None else protected_paths
    return GOVERNANCE_SURFACE - {normalize_path(path) for path in resolved}


def normalize_path(path: str) -> str:
    """Normalize path separators and strip a leading ``./`` without destroying ``.github``."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_protected_path(path: str) -> bool:
    """Return ``True`` when *path* (any separator style) is a protected core contract."""
    return normalize_path(path) in PROTECTED_PATHS


def has_breaking_change_marker(diff_text: str) -> str | None:
    """Return the first marker alias found as its own line, a
    ``Marker:``-style trailer, or an *added* diff line (an optional leading
    ``+``), in *diff_text*, or ``None``.

    A bare substring test (``marker in diff_text``) would treat e.g. "This
    is NOT a BREAKING-CHANGE, just an internal cleanup." as a match, since
    the literal marker text still appears mid-sentence — and would also
    accept a *deleted* marker line (``-BREAKING-CHANGE...``), since a
    deletion still contains the string in the diff's ``-`` context.
    Anchoring to the start of an added or unprefixed line closes both.
    Mirrors ``scripts/_governance.py``'s ``find_breaking_change_marker`` —
    duplicated rather than imported, since ``scripts/`` must stay
    independent of ``mangomas`` (see this module's own docstring).
    """
    for marker in BREAKING_CHANGE_MARKER_ALIASES:
        pattern = re.compile(rf"^\+?[ \t]*{re.escape(marker)}[ \t]*(:|$)", re.MULTILINE)
        if pattern.search(diff_text):
            return marker
    return None


def read_staged_diff(path: str) -> str:
    """Return ``git diff --staged -- <path>`` output (empty string on git failure).

    Calls ``subprocess.run`` as a module attribute (not ``from subprocess
    import run``) so a test that monkeypatches the global ``subprocess.run``
    still observes the patch regardless of which module calls this function.
    """
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell, not attacker-controlled
            ["git", "diff", "--staged", "--", path],  # noqa: S607 — "git" resolved via PATH
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.warning(
            "git executable unavailable for staged-diff read",
            extra={"path": path, "error": str(exc)},
        )
        return ""
    if result.returncode != 0:
        logger.warning(
            "git diff failed for staged-diff read",
            extra={"path": path, "stderr": result.stderr.strip()},
        )
        return ""
    return result.stdout
