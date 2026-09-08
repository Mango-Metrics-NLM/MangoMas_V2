"""Fail-closed observation-role map."""

from __future__ import annotations

import pytest

from mangomas.cognitive import roles as roles_mod
from mangomas.cognitive.roles import (
    FORBIDDEN_HARNESS_ROLES,
    UnknownAgentRoleError,
    UnmappedToolAgentError,
    harness_role_for_agent,
)


def test_planner_maps_to_planner() -> None:
    assert harness_role_for_agent("planner") == "planner"


def test_reviewer_maps_to_verifier() -> None:
    assert harness_role_for_agent("reviewer") == "verifier"


def test_chat_and_summarize_are_unmapped_observation() -> None:
    assert harness_role_for_agent("chat") is None
    assert harness_role_for_agent("summarize") is None


def test_tool_raises_and_never_defaults_to_implementer() -> None:
    with pytest.raises(UnmappedToolAgentError, match="implementer"):
        harness_role_for_agent("tool")


def test_unknown_agent_raises() -> None:
    with pytest.raises(UnknownAgentRoleError, match="implementer"):
        harness_role_for_agent("quality agent")


def test_mapped_roles_are_not_execution_roles() -> None:
    for agent in ("planner", "reviewer"):
        role = harness_role_for_agent(agent)
        assert role is not None
        assert role not in FORBIDDEN_HARNESS_ROLES
        assert role != "implementer"


def test_execution_role_in_the_map_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(roles_mod.HARNESS_OBSERVATION_ROLES, "planner", "implementer")
    with pytest.raises(UnknownAgentRoleError, match="execution role"):
        harness_role_for_agent("planner")
