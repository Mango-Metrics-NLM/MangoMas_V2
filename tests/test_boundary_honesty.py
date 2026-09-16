"""Guards for the two settings PR D adds (ADR-0033).

Both close a gap where a name promised a control the code did not implement:
``MANGOMAS_WORKFLOW__ENABLED=false`` did not actually stop workflow execution,
and ``model_override`` accepted any string at all.
"""

from __future__ import annotations

import pytest

from mangomas.composition.llm import build_agent_llm_overrides
from mangomas.config import AgentSettings, LLMSettings, WorkflowSettings
from mangomas.errors import ConfigError
from mangomas.workflow.loader import resolve_workflow_source

_GRAPH = '{"name": "g", "root": {"kind": "agent", "agent": "chat"}}'


# ── D2: an inline definition is a per-request opt-in a deployment can refuse ──


def test_inline_definition_runs_by_default() -> None:
    """Back-compat: today's documented behaviour is the default.

    ``POST /workflows/run`` has always executed a caller-supplied graph even
    with the feature disabled. Changing that silently would break deployments
    relying on it, so the switch defaults to permitting it.
    """
    cfg = WorkflowSettings(enabled=False)

    assert resolve_workflow_source(_GRAPH, cfg) == _GRAPH


def test_inline_definition_is_refused_when_disallowed() -> None:
    """A deployment can require server-configured graphs only.

    One shared bearer token otherwise authorises composing and running an
    arbitrary agent graph — fan-outs, loops, branch trees — with no approval
    bound to that graph or its revision.
    """
    cfg = WorkflowSettings(enabled=False, allow_inline_definition=False)

    with pytest.raises(ConfigError, match="inline workflow definitions are disabled"):
        resolve_workflow_source(_GRAPH, cfg)


def test_a_configured_graph_still_runs_when_inline_is_disallowed() -> None:
    """Refusing inline graphs must not disable the feature itself.

    The discriminating direction: a switch that refused everything would pass
    the test above while breaking every configured workflow deployment.
    """
    cfg = WorkflowSettings(enabled=True, definition=_GRAPH, allow_inline_definition=False)

    assert resolve_workflow_source(None, cfg) == _GRAPH


# ── D3: model selection is constrained, and therefore reviewable ─────────────


def test_empty_allowlist_permits_any_model() -> None:
    """Empty means "unconstrained", so existing deployments are unaffected."""
    base = LLMSettings(model="base-model")
    agents = {"planner": AgentSettings(model_override="some-other-model")}

    overrides = build_agent_llm_overrides(agents, base)

    assert set(overrides) == {"planner"}


def test_model_override_outside_the_allowlist_is_refused() -> None:
    """An unlisted model must not be silently built.

    ``model_override`` was an unconstrained ``str | None``: nothing declared
    which models were approved, and nothing rejected one that was not.
    """
    base = LLMSettings(model="base-model", allowed_models=["base-model", "approved-model"])
    agents = {"planner": AgentSettings(model_override="rogue-model")}

    with pytest.raises(ConfigError, match="rogue-model"):
        build_agent_llm_overrides(agents, base)


def test_an_allowlisted_override_is_still_built() -> None:
    """The allowlist must permit what it lists, or it is just an off switch."""
    base = LLMSettings(model="base-model", allowed_models=["base-model", "approved-model"])
    agents = {"planner": AgentSettings(model_override="approved-model")}

    overrides = build_agent_llm_overrides(agents, base)

    assert set(overrides) == {"planner"}


def test_the_base_model_must_itself_be_allowlisted() -> None:
    """An allowlist that exempts the default is a loophole, not a control."""
    with pytest.raises(ValueError, match="base-model"):
        LLMSettings(model="base-model", allowed_models=["approved-model"])
