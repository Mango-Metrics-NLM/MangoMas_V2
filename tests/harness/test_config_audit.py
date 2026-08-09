"""Tests for ``mangomas.harness.config_audit`` (ADR-0021 / spec-0017).

Ported from ``origin/main``'s equivalent suite — pure decision-table logic,
unaffected by the branch's differing protected-path design.
"""

from __future__ import annotations

import pytest

from mangomas.harness.config_audit import ConfigAuditDecision, evaluate_config_change


def test_off_mode_always_allows_regardless_of_source() -> None:
    decision = evaluate_config_change("project_settings", "off")
    assert decision == ConfigAuditDecision("allow", "project_settings", "config_audit_mode is off")


def test_off_mode_allows_even_an_unrecognized_source() -> None:
    decision = evaluate_config_change(None, "off")
    assert decision.action == "allow"


@pytest.mark.parametrize("mode", ["audit", "block"])
def test_policy_settings_always_allows_regardless_of_mode(mode: str) -> None:
    """Claude Code cannot block policy_settings changes regardless of mode —
    treating it as blockable would be a false promise."""
    decision = evaluate_config_change("policy_settings", mode)  # type: ignore[arg-type]
    assert decision.action == "allow"
    assert "cannot be blocked" in decision.reason


@pytest.mark.parametrize("mode", ["audit", "block"])
def test_missing_source_fails_open_regardless_of_mode(mode: str) -> None:
    """An absent/unrecognized source must never silently escalate to a block."""
    decision = evaluate_config_change(None, mode)  # type: ignore[arg-type]
    assert decision.action == "allow"
    assert decision.source == "<unknown>"


def test_audit_mode_logs_and_allows_a_governed_source() -> None:
    decision = evaluate_config_change("project_settings", "audit")
    assert decision.action == "audit"
    assert decision.source == "project_settings"


def test_block_mode_blocks_a_governed_source() -> None:
    decision = evaluate_config_change("local_settings", "block")
    assert decision.action == "block"
    assert decision.source == "local_settings"


def test_block_mode_blocks_project_settings_too() -> None:
    decision = evaluate_config_change("project_settings", "block")
    assert decision.action == "block"
