"""Shared governance primitives for the Claude Code harness/hook layer.

Relocated from ``scripts/lint_agent_frontmatter.py`` (see ADR-0011) for two
reasons: (1) ``pyproject.toml`` scopes coverage to ``source = ["mangomas"]``,
so logic left in ``scripts/`` is invisible to the 95% coverage gate; (2) more
than one hook now needs the same ``BREAKING-CHANGE`` marker convention (the
existing ``PreToolUse`` protected-path check and the new ``ConfigChange``
audit hook), and duplicating the marker/constant would reintroduce exactly
the kind of drift this module exists to eliminate.

``scripts/lint_agent_frontmatter.py`` imports these names rather than
redefining them, so its behaviour (and its existing test suite) is unchanged.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Final

logger = logging.getLogger(__name__)

# Stable core contracts gated by the protected-path hook. Kept in lock-step
# with the "File Ownership" / protected-paths documentation in CLAUDE.md.
PROTECTED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "src/mangomas/core/agent.py",
        "src/mangomas/core/orchestrator.py",
        "src/mangomas/core/tools.py",
        "src/mangomas/errors.py",
        "src/mangomas/registry.py",
    }
)

# Marker a committer adds to the staged diff to approve a breaking change to a
# protected path. ``BREAKING_CHANGE_MARKER`` is the documented (CLAUDE.md)
# string; the legacy ``# approved-breaking-change`` form is kept as an
# accepted alias so any in-flight staged diffs are not retroactively blocked.
BREAKING_CHANGE_MARKER: Final[str] = "BREAKING-CHANGE"
BREAKING_CHANGE_MARKER_ALIASES: Final[frozenset[str]] = frozenset(
    {BREAKING_CHANGE_MARKER, "# approved-breaking-change"}
)


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
    """Return the first matching marker alias found in *diff_text*, or ``None``."""
    return next((marker for marker in BREAKING_CHANGE_MARKER_ALIASES if marker in diff_text), None)


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
