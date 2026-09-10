"""Per-agent MODEL_OVERRIDE wiring (spec-0028 / ADR-0028)."""

from __future__ import annotations

import pytest

from mangomas.composition import (
    _HarnessOrchestrator,
    build_agent_llm_overrides,
    build_orchestrator,
    llm_registry,
)
from mangomas.config import AgentSettings, LLMSettings, Settings
from tests.composition.helpers import close_repo
from tests.fakes import FakeLLM


def test_build_agent_llm_overrides_returns_empty_dict_when_no_overrides_set() -> None:
    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    assert build_agent_llm_overrides({"chat": AgentSettings()}, base_cfg) == {}


def test_build_agent_llm_overrides_skips_blank_and_whitespace_override() -> None:
    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {
        "chat": AgentSettings(model_override=""),
        "tool": AgentSettings(model_override="   "),
    }
    assert build_agent_llm_overrides(agents_cfg, base_cfg) == {}


def test_build_agent_llm_overrides_skips_override_equal_to_base_model() -> None:
    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {"chat": AgentSettings(model_override="local-model")}
    assert build_agent_llm_overrides(agents_cfg, base_cfg) == {}


def test_build_agent_llm_overrides_builds_client_for_differing_override() -> None:
    captured: list[LLMSettings] = []

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        captured.append(cfg)
        return FakeLLM()

    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {"planner": AgentSettings(model_override="other-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        result = build_agent_llm_overrides(agents_cfg, base_cfg)

    assert set(result) == {"planner"}
    assert isinstance(result["planner"], FakeLLM)
    assert captured[0].model == "other-model"
    assert captured[0].provider == "lmstudio"


def test_build_agent_llm_overrides_dedups_clients_across_agents() -> None:
    call_count = 0

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return FakeLLM()

    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {
        "planner": AgentSettings(model_override="shared-model"),
        "reviewer": AgentSettings(model_override="shared-model"),
    }
    with llm_registry.scoped("lmstudio", _fake_factory):
        result = build_agent_llm_overrides(agents_cfg, base_cfg)

    assert call_count == 1
    assert result["planner"] is result["reviewer"]


def test_agent_settings_model_override_env_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE` must reach `Settings().agents[<name>]`.

    Mirrors `test_agent_settings_max_tool_steps_env_round_trip` in
    `tests/test_tool_agent.py` — this env var shape had no test at all before
    spec-0028 wired the field it populates.
    """
    monkeypatch.setenv("MANGOMAS_AGENTS__CHAT__MODEL_OVERRIDE", "override-model")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.agents["chat"].model_override == "override-model"


def test_build_orchestrator_populates_agent_llm_overrides_extra() -> None:
    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        return FakeLLM(reply=f"model={cfg.model}")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.agents = {"chat": AgentSettings(model_override="override-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)
        try:
            overrides = orch.context.extras["agent_llm_overrides"]
            assert set(overrides) == {"chat"}
            assert isinstance(overrides["chat"], FakeLLM)
        finally:
            close_repo(orch)


def test_build_orchestrator_no_overrides_extra_is_empty_dict() -> None:
    """Backward-compat guard: no MODEL_OVERRIDE configured -> extras key is `{}`."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.extras["agent_llm_overrides"] == {}
    finally:
        close_repo(orch)


async def test_orchestrator_aclose_closes_agent_llm_override_clients() -> None:
    shared_llm = FakeLLM()
    override_llm = FakeLLM()
    built: dict[str, FakeLLM] = {"local-model": shared_llm, "override-model": override_llm}

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        return built[cfg.model]

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.agents = {"chat": AgentSettings(model_override="override-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)

    await orch.aclose()

    assert shared_llm.closed is True
    assert override_llm.closed is True


async def test_orchestrator_aclose_without_overrides_matches_prior_behavior() -> None:
    """No MODEL_OVERRIDE configured -> aclose() closes only the shared LLM, as before."""
    shared_llm = FakeLLM()

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:  # noqa: ARG001
        return shared_llm

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)

    await orch.aclose()  # must not raise

    assert shared_llm.closed is True


async def test_harness_orchestrator_aclose_closes_agent_llm_override_clients() -> None:
    """The harness-enabled branch (_HarnessOrchestrator) also picks up the mixin."""
    shared_llm = FakeLLM()
    override_llm = FakeLLM()
    built: dict[str, FakeLLM] = {"local-model": shared_llm, "override-model": override_llm}

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        return built[cfg.model]

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.harness.enabled = True
    settings.agents = {"chat": AgentSettings(model_override="override-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)

    assert isinstance(orch, _HarnessOrchestrator)
    await orch.aclose()

    assert shared_llm.closed is True
    assert override_llm.closed is True
