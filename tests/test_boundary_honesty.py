"""Guards for the two settings PR D adds (ADR-0033).

Both close a gap where a name promised a control the code did not implement:
``MANGOMAS_WORKFLOW__ENABLED=false`` did not actually stop workflow execution,
and ``model_override`` accepted any string at all.
"""

from __future__ import annotations

import json
from typing import cast

import pytest
from pydantic import ValidationError

from mangomas.composition.llm import build_agent_llm_overrides
from mangomas.config import AgentSettings, LLMSettings, WorkflowSettings, get_settings
from mangomas.core.tools import ToolEffects, ToolSpec
from mangomas.errors import ConfigError
from mangomas.rag.retrieval import RetrievalTool, Retriever
from mangomas.workflow.loader import resolve_workflow_source
from tests.constants import (
    ALLOWLISTED_MODEL,
    DEFAULT_LLM_MODEL,
    DEFAULT_SIGNAL_TTL_SECONDS,
    DEFAULT_WORKFLOW_ALLOW_INLINE_DEFINITION,
    LLM_ALLOWED_MODELS_ENV,
    LLM_MODEL_ENV,
    MAX_SIGNAL_TTL_SECONDS,
    SHORT_SIGNAL_TTL_SECONDS,
    SIGNAL_TTL_SECONDS_ENV,
    WORKFLOW_ALLOW_INLINE_DEFINITION_ENV,
)

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


# ── D4: a tool declares whether it changes anything ─────────────────────────


def test_tool_spec_effects_default_to_undeclared() -> None:
    """A tool that says nothing must not be *assumed* safe.

    ``read_only: bool = True`` was the obvious shape and is a lie: it would
    label every existing tool read-only on the strength of its author never
    having considered the question. ``UNDECLARED`` is the honest default, and
    lets a future broker fail closed without the field having misrepresented
    anything.
    """
    spec = ToolSpec(name="whatever", description="does a thing")

    assert spec.effects is ToolEffects.UNDECLARED


def test_tool_spec_effects_round_trip() -> None:
    """The field is additive and carries the value it was given."""
    spec = ToolSpec(name="writer", description="writes", effects=ToolEffects.MUTATES)

    assert spec.effects is ToolEffects.MUTATES
    assert spec.model_dump()["effects"] == "mutates"


def test_the_retrieval_tool_declares_itself_read_only() -> None:
    """The one built-in tool knows the answer, so it should say it.

    A default that means "unknown" is only honest if the tools that *do* know
    declare — otherwise the vocabulary exists and nothing ever uses it, which
    is the unwired-control pattern this whole audit is about.
    """
    # No importorskip: ``RetrievalTool`` imports without chromadb (the store is
    # injected, not constructed here), and a skipped test proves nothing.
    tool = RetrievalTool(retriever=cast("Retriever", object()))

    assert tool.spec.effects is ToolEffects.READ_ONLY


# ── Every switch above must survive the trip through the environment ──
#
# The tests above construct the settings models directly, which proves the
# *logic* and nothing about the *name*. These three settings all live in nested
# groups, where the env name carries a double-underscore delimiter: get it
# wrong — ``MANGOMAS_LLM_ALLOWED_MODELS``, ``MANGOMAS_SIGNAL__TTL`` — and
# pydantic-settings does not raise. It sees no override at all, and the field
# quietly keeps its default. For these three that default is *permissive*:
# an empty roster, an inline definition allowed, a full-day TTL. So the
# operator sets the control, the deployment reports no error, and the control
# is not applied. Only a round trip through the real env can catch that.


def test_allowed_models_round_trips_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The roster arrives as a JSON list under the real env name."""
    monkeypatch.setenv(LLM_MODEL_ENV, ALLOWLISTED_MODEL)
    monkeypatch.setenv(LLM_ALLOWED_MODELS_ENV, json.dumps([ALLOWLISTED_MODEL]))
    get_settings.cache_clear()
    try:
        llm = get_settings().llm
        assert llm.allowed_models == [ALLOWLISTED_MODEL], (
            f"{LLM_ALLOWED_MODELS_ENV} did not reach LLMSettings.allowed_models; "
            "the roster would be empty and every model permitted"
        )
    finally:
        get_settings.cache_clear()


def test_an_env_roster_that_excludes_the_base_model_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator fires on the env path too, not only on direct construction.

    The two-sided half: without this, the test above passes against a build
    that parses the roster and never checks it.
    """
    monkeypatch.setenv(LLM_MODEL_ENV, DEFAULT_LLM_MODEL)
    monkeypatch.setenv(LLM_ALLOWED_MODELS_ENV, json.dumps([ALLOWLISTED_MODEL]))
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="allowed_models"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_allow_inline_definition_round_trips_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``false`` under the real env name actually refuses a caller's graph."""
    monkeypatch.setenv(WORKFLOW_ALLOW_INLINE_DEFINITION_ENV, "false")
    get_settings.cache_clear()
    try:
        cfg = get_settings().workflow
        assert cfg.allow_inline_definition is False, (
            f"{WORKFLOW_ALLOW_INLINE_DEFINITION_ENV} did not reach "
            "WorkflowSettings; a caller-supplied graph would still run"
        )
        with pytest.raises(ConfigError, match="inline workflow definitions are disabled"):
            resolve_workflow_source(_GRAPH, cfg)
    finally:
        get_settings.cache_clear()


def test_allow_inline_definition_defaults_permissive_without_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction: absent the variable, today's behaviour is unchanged."""
    monkeypatch.delenv(WORKFLOW_ALLOW_INLINE_DEFINITION_ENV, raising=False)
    get_settings.cache_clear()
    try:
        cfg = get_settings().workflow
        assert cfg.allow_inline_definition is DEFAULT_WORKFLOW_ALLOW_INLINE_DEFINITION
        assert resolve_workflow_source(_GRAPH, cfg) == _GRAPH
    finally:
        get_settings.cache_clear()


def test_signal_ttl_round_trips_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shortened TTL under the real env name reaches ``SignalSettings``."""
    monkeypatch.setenv(SIGNAL_TTL_SECONDS_ENV, str(SHORT_SIGNAL_TTL_SECONDS))
    get_settings.cache_clear()
    try:
        assert get_settings().signal.ttl_seconds == SHORT_SIGNAL_TTL_SECONDS, (
            f"{SIGNAL_TTL_SECONDS_ENV} did not reach SignalSettings.ttl_seconds; "
            f"envelopes would stay valid for the default {DEFAULT_SIGNAL_TTL_SECONDS}s"
        )
    finally:
        get_settings.cache_clear()


def test_a_signal_ttl_over_the_ceiling_is_refused_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling is enforced on the env path, not only on direct construction."""
    monkeypatch.setenv(SIGNAL_TTL_SECONDS_ENV, str(MAX_SIGNAL_TTL_SECONDS + 1))
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="ttl_seconds"):
            get_settings()
    finally:
        get_settings.cache_clear()
