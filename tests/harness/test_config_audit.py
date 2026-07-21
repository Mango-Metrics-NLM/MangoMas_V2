"""Tests for ``mangomas.harness.config_audit``."""

from __future__ import annotations

import pytest

from mangomas.harness import config_audit
from tests import constants


@pytest.mark.parametrize(
    "source",
    [
        constants.CONFIG_CHANGE_PROJECT_SETTINGS_SOURCE,
        constants.CONFIG_CHANGE_POLICY_SETTINGS_SOURCE,
        None,
    ],
)
def test_off_mode_always_allows_regardless_of_source(source: str | None) -> None:
    decision = config_audit.evaluate_config_change(source, "off")
    assert decision.action == "allow"


def test_policy_settings_always_allows_under_audit_mode() -> None:
    decision = config_audit.evaluate_config_change(
        constants.CONFIG_CHANGE_POLICY_SETTINGS_SOURCE, "audit"
    )
    assert decision.action == "allow"
    assert "cannot be blocked" in decision.reason


def test_policy_settings_always_allows_under_block_mode() -> None:
    decision = config_audit.evaluate_config_change(
        constants.CONFIG_CHANGE_POLICY_SETTINGS_SOURCE, "block"
    )
    assert decision.action == "allow"
    assert "cannot be blocked" in decision.reason


def test_missing_source_fails_open_under_audit_mode() -> None:
    decision = config_audit.evaluate_config_change(None, "audit")
    assert decision.action == "allow"
    assert "missing" in decision.reason


def test_missing_source_fails_open_under_block_mode() -> None:
    decision = config_audit.evaluate_config_change(None, "block")
    assert decision.action == "allow"


@pytest.mark.parametrize(
    "source",
    [
        constants.CONFIG_CHANGE_PROJECT_SETTINGS_SOURCE,
        constants.CONFIG_CHANGE_LOCAL_SETTINGS_SOURCE,
    ],
)
def test_audit_mode_audits_governed_sources(source: str) -> None:
    decision = config_audit.evaluate_config_change(source, "audit")
    assert decision.action == "audit"
    assert decision.source == source


@pytest.mark.parametrize(
    "source",
    [
        constants.CONFIG_CHANGE_PROJECT_SETTINGS_SOURCE,
        constants.CONFIG_CHANGE_LOCAL_SETTINGS_SOURCE,
    ],
)
def test_block_mode_blocks_governed_sources(source: str) -> None:
    decision = config_audit.evaluate_config_change(source, "block")
    assert decision.action == "block"
    assert decision.source == source


def test_decision_source_defaults_to_unknown_label_when_none() -> None:
    decision = config_audit.evaluate_config_change(None, "audit")
    assert decision.source == "<unknown>"
