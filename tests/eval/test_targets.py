"""Tests for the built-in eval targets and the target registry."""

from __future__ import annotations

import pytest

from mangomas.core import AgentRequest, Message, Orchestrator
from mangomas.errors import ConfigError
from mangomas.eval import target_registry
from mangomas.eval.targets import AgentTarget, EchoTarget, FanOutTarget, PipelineTarget
from tests.constants import STUB_REPLY

_REQUEST = AgentRequest(messages=[Message(role="user", content="hello")])


# ── Behaviour ─────────────────────────────────────────────────────────────────


async def test_agent_target_dispatches_agent(eval_orchestrator: Orchestrator) -> None:
    target = AgentTarget(agent="chat")
    assert target.name == "chat"
    assert await target.run(_REQUEST, orch=eval_orchestrator) == STUB_REPLY


async def test_echo_target_echoes_last_user_message(eval_orchestrator: Orchestrator) -> None:
    assert await EchoTarget().run(_REQUEST, orch=eval_orchestrator) == "hello"


async def test_echo_target_fixed_text(eval_orchestrator: Orchestrator) -> None:
    assert await EchoTarget(text="fixed").run(_REQUEST, orch=eval_orchestrator) == "fixed"


async def test_echo_target_no_user_message_returns_empty(eval_orchestrator: Orchestrator) -> None:
    req = AgentRequest(messages=[Message(role="system", content="s")])
    assert await EchoTarget().run(req, orch=eval_orchestrator) == ""


async def test_pipeline_target_runs(eval_orchestrator: Orchestrator) -> None:
    assert await PipelineTarget(agents=["chat"]).run(_REQUEST, orch=eval_orchestrator) == STUB_REPLY


async def test_fan_out_target_first(eval_orchestrator: Orchestrator) -> None:
    target = FanOutTarget(["chat"], join="first")
    assert await target.run(_REQUEST, orch=eval_orchestrator) == STUB_REPLY


async def test_fan_out_target_concat(eval_orchestrator: Orchestrator) -> None:
    target = FanOutTarget(["chat", "chat"], join="concat")
    assert await target.run(_REQUEST, orch=eval_orchestrator) == f"{STUB_REPLY}\n{STUB_REPLY}"


# ── Registry + factories ──────────────────────────────────────────────────────


def test_registry_exposes_builtin_targets() -> None:
    for name in ("agent", "echo", "fan_out", "pipeline"):
        assert name in target_registry.available()


def test_agent_factory_requires_agent_option() -> None:
    with pytest.raises(ConfigError):
        target_registry.get("agent")({})


def test_agent_factory_builds() -> None:
    assert target_registry.get("agent")({"agent": "chat"}).name == "chat"


def test_pipeline_factory_requires_non_empty_agents() -> None:
    with pytest.raises(ConfigError):
        target_registry.get("pipeline")({})
    with pytest.raises(ConfigError):
        target_registry.get("pipeline")({"agents": []})


def test_pipeline_factory_builds() -> None:
    target = target_registry.get("pipeline")({"agents": ["chat"]})
    assert isinstance(target, PipelineTarget)
    assert target.name == "pipeline"


def test_fan_out_factory_requires_agents_list() -> None:
    with pytest.raises(ConfigError):
        target_registry.get("fan_out")({"agents": "not-a-list"})
    with pytest.raises(ConfigError):
        target_registry.get("fan_out")({"agents": []})


def test_fan_out_factory_validates_join() -> None:
    with pytest.raises(ConfigError):
        target_registry.get("fan_out")({"agents": ["chat"], "join": "bogus"})


def test_fan_out_factory_builds() -> None:
    target = target_registry.get("fan_out")({"agents": ["chat"], "join": "concat"})
    assert isinstance(target, FanOutTarget)
    assert target.name == "fan_out"


def test_echo_factory_builds_with_and_without_text() -> None:
    assert isinstance(target_registry.get("echo")({}), EchoTarget)
    assert isinstance(target_registry.get("echo")({"text": "x"}), EchoTarget)
