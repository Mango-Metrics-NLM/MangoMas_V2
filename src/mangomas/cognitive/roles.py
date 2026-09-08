"""Fail-closed Mango-Mas → harness observation-role map.

Unknown names raise. ``tool`` raises until a dedicated mapping is decided.
Chat and summarize are unmapped observation (no harness role, empty tool grant).
Nothing here defaults to ``implementer``.
"""

from __future__ import annotations

HARNESS_OBSERVATION_ROLES: dict[str, str] = {
    "planner": "planner",
    "reviewer": "verifier",
}

UNMAPPED_OBSERVATION_AGENTS: frozenset[str] = frozenset({"chat", "summarize"})

# Names this repo must never emit as a harness execution role.
FORBIDDEN_HARNESS_ROLES: frozenset[str] = frozenset(
    {
        "implementer",
        "destructive",
        "write_file",
        "shell",
    }
)


class UnmappedToolAgentError(ValueError):
    """Raised when the tool agent is asked for a harness execution role."""


class UnknownAgentRoleError(ValueError):
    """Raised when a Mango-Mas agent name has no observation-role mapping."""


def harness_role_for_agent(agent_name: str) -> str | None:
    """Return the harness observation role for a built-in agent, or ``None``.

    ``None`` means unmapped observation (chat/summarize): no tools, no
    execution role. ``tool`` raises — today's ``retrieve`` stays a local RAG
    tool and is not a harness grant.
    """
    if agent_name == "tool":
        raise UnmappedToolAgentError(
            "agent 'tool' has no harness execution role; retrieve stays local "
            "and unknown names never default to implementer"
        )
    if agent_name in UNMAPPED_OBSERVATION_AGENTS:
        return None
    try:
        role = HARNESS_OBSERVATION_ROLES[agent_name]
    except KeyError as exc:
        raise UnknownAgentRoleError(
            f"unknown Mango-Mas agent {agent_name!r}; refusing to default to implementer"
        ) from exc
    if role in FORBIDDEN_HARNESS_ROLES:
        raise UnknownAgentRoleError(f"mapped role {role!r} is a harness execution role; refusing")
    return role
