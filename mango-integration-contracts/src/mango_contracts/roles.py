"""Fail-closed role and producer maps.

Unknown names raise. They never default to ``implementer`` or a write-capable
harness role.
"""

from __future__ import annotations

AGENT_PRODUCER_IDS: dict[str, str] = {
    "planner": "mangomas.planner.v2",
    "reviewer": "mangomas.reviewer.v2",
    "chat": "mangomas.chat.v2",
    "summarize": "mangomas.summarize.v2",
    "tool": "mangomas.tool.v2",
}

HARNESS_REVIEW_ROLES: frozenset[str] = frozenset(
    {
        "spec-analyst",
        "peer-reviewer",
        "security-reviewer",
        "release-auditor",
        "test-eval",
        "orchestrator",
        "harness-maintainer",
    }
)


class UnknownProducerError(ValueError):
    """Raised when a Mango-Mas agent name has no producer mapping."""


class UnknownReviewRoleError(ValueError):
    """Raised when a requested review role is not in the fail-closed registry."""


def producer_id_for_agent(agent_name: str) -> str:
    """Return the stable producer_id for a built-in Mango-Mas agent name."""
    try:
        return AGENT_PRODUCER_IDS[agent_name]
    except KeyError as exc:
        raise UnknownProducerError(
            f"unknown Mango-Mas agent {agent_name!r}; refusing to default"
        ) from exc


def map_requested_review_roles(names: list[str]) -> list[str]:
    """Return *names* if every entry is a known harness review role."""
    unknown = [name for name in names if name not in HARNESS_REVIEW_ROLES]
    if unknown:
        raise UnknownReviewRoleError(
            f"unknown review role(s) {unknown!r}; refusing to default to implementer"
        )
    return list(names)
