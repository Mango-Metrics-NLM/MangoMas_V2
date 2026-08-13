"""Claude Code harness/hook governance (ADR-0021 / spec-0017).

Ported from ``origin/main``'s ``harness/`` package (ADR-0011 there), minus
``coverage.py`` and the ``Stop``-hook gate logic it backed — dropped as
tautological on this branch, where ``scripts/check_coverage.py`` is already
the documented single source of truth for coverage floors and
``tests/deploy/test_ci_make_parity.py`` already asserts it matches
``pyproject.toml``'s ``--cov-fail-under``. See spec-0017 R7.
"""

from __future__ import annotations

from mangomas.harness.config_audit import (
    ConfigAuditDecision,
    ConfigChangeAction,
    ConfigChangeMode,
    evaluate_config_change,
)
from mangomas.harness.governance import (
    BREAKING_CHANGE_MARKER,
    BREAKING_CHANGE_MARKER_ALIASES,
    PROTECTED_PATHS,
    has_breaking_change_marker,
    is_protected_path,
    normalize_path,
    read_staged_diff,
)

__all__ = [
    "BREAKING_CHANGE_MARKER",
    "BREAKING_CHANGE_MARKER_ALIASES",
    "PROTECTED_PATHS",
    "ConfigAuditDecision",
    "ConfigChangeAction",
    "ConfigChangeMode",
    "evaluate_config_change",
    "has_breaking_change_marker",
    "is_protected_path",
    "normalize_path",
    "read_staged_diff",
]
