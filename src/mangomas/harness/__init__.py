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
    GOVERNANCE_SURFACE,
    PROTECTED_PATHS,
    has_breaking_change_marker,
    is_protected_path,
    normalize_path,
    read_staged_diff,
    unprotected_governance_surface,
)
from mangomas.harness.isolation import (
    DockerIsolationBackend,
    IsolationResult,
    NoIsolationBackend,
    OracleIsolationBackend,
    OracleIsolationFactory,
    isolation_registry,
    resolve_sandbox_argv,
    select_sandbox_env,
)
from mangomas.harness.repair_sandbox import (
    DEFAULT_ORACLE_TIMEOUT_SECONDS,
    FORBIDDEN_PROMOTION_FILENAMES,
    FORBIDDEN_PROMOTION_PREFIXES,
    LOOPBACK_HOSTS,
    PromotionOutcome,
    build_scrubbed_oracle_env,
    run_oracle_and_promote,
    stage_candidate_file,
    validate_loopback_llm_url,
    validate_promotion_target,
)
from mangomas.harness.tool_policy import (
    DEFAULT_MAX_ARGUMENT_COUNT,
    DEFAULT_MAX_STRING_ARGUMENT_CHARS,
    enforce_tool_invocation_policy,
    validate_tool_arguments,
)

__all__ = [
    "BREAKING_CHANGE_MARKER",
    "BREAKING_CHANGE_MARKER_ALIASES",
    "DEFAULT_MAX_ARGUMENT_COUNT",
    "DEFAULT_MAX_STRING_ARGUMENT_CHARS",
    "DEFAULT_ORACLE_TIMEOUT_SECONDS",
    "FORBIDDEN_PROMOTION_FILENAMES",
    "FORBIDDEN_PROMOTION_PREFIXES",
    "GOVERNANCE_SURFACE",
    "LOOPBACK_HOSTS",
    "PROTECTED_PATHS",
    "ConfigAuditDecision",
    "ConfigChangeAction",
    "ConfigChangeMode",
    "DockerIsolationBackend",
    "IsolationResult",
    "NoIsolationBackend",
    "OracleIsolationBackend",
    "OracleIsolationFactory",
    "PromotionOutcome",
    "build_scrubbed_oracle_env",
    "enforce_tool_invocation_policy",
    "evaluate_config_change",
    "has_breaking_change_marker",
    "is_protected_path",
    "isolation_registry",
    "normalize_path",
    "read_staged_diff",
    "resolve_sandbox_argv",
    "run_oracle_and_promote",
    "select_sandbox_env",
    "stage_candidate_file",
    "unprotected_governance_surface",
    "validate_loopback_llm_url",
    "validate_promotion_target",
    "validate_tool_arguments",
]
