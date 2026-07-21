"""Shared literals for the bridge test suite.

Deliberately does NOT import ``mangomas`` (or ``tests.constants``) — the bridge
is a decoupled black-box client, and the tests keep that boundary. Centralised
here so no magic strings/numbers leak into the test bodies.
"""

from __future__ import annotations

from typing import Any

BRIDGE_BASE_URL = "http://mango.test"
DEFAULT_AGENT = "chat"
SUMMARIZE_AGENT = "summarize"

HTTP_OK = 200
HTTP_SERVICE_UNAVAILABLE = 503


def invoke_url(agent: str = DEFAULT_AGENT, *, base: str = BRIDGE_BASE_URL) -> str:
    """Full URL of the Mango-Mas invoke route for *agent*."""
    return f"{base}/agents/{agent}/invoke"


def mango_response(content: str = "hello", *, agent: str = DEFAULT_AGENT) -> dict[str, Any]:
    """A well-formed ``AgentResponse``-shaped JSON body."""
    return {"content": content, "agent": agent, "metadata": {}}
