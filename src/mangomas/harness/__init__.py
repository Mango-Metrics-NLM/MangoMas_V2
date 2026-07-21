"""Claude Code harness/hook governance: coverage-floor truth, marker/path
governance, and the ``Stop``/``ConfigChange`` hook decision logic (ADR-0011).
"""

from mangomas.harness.config_audit import (
    ConfigAuditDecision,
    ConfigChangeMode,
    evaluate_config_change,
)
from mangomas.harness.coverage import (
    StopGateMode,
    build_pytest_args,
    read_coverage_floor,
    resolve_stop_gate_exit_code,
    should_skip_active_stop_hook,
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
    "ConfigChangeMode",
    "StopGateMode",
    "build_pytest_args",
    "evaluate_config_change",
    "has_breaking_change_marker",
    "is_protected_path",
    "normalize_path",
    "read_coverage_floor",
    "read_staged_diff",
    "resolve_stop_gate_exit_code",
    "should_skip_active_stop_hook",
]
