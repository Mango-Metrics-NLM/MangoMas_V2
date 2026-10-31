"""Named LLM provider profiles (spec-0039 / ADR-0038).

Composition-root half: building the per-profile clients from settings, the
credential-inheritance rule, secret resolution, the dispatch mixin that
validates and stamps the selected profile, and the close path. The config
validators are covered in ``tests/test_config.py``; the HTTP surface in
``tests/test_llm_profiles_api.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from mangomas.agents._prompt import (
    LLM_PROFILE_INFO_EXTRAS_KEY,
    LLM_PROFILE_METADATA_KEY,
    LLM_PROFILES_EXTRAS_KEY,
)
from mangomas.composition import (
    _HarnessOrchestrator,
    _LLMProfileDispatchMixin,
    build_llm_profiles,
    build_orchestrator,
    llm_registry,
    secrets_registry,
)
from mangomas.composition.builder import _Orchestrator
from mangomas.composition.recording import _FailureRecordingMixin
from mangomas.config import LLMProfileSettings, LLMSettings, Settings
from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import ConfigError
from tests.composition.helpers import close_repo
from tests.constants import (
    DEFAULT_LLM_MODEL,
    IN_MEMORY_SQLITE_URL,
    TEST_LLM_PROFILE_MODEL,
    TEST_LLM_PROFILE_NAME,
    UNKNOWN_LLM_PROFILE_NAME,
)
from tests.fakes import FakeLLM, FakeRepository, FakeSecretsProvider

_BASE_SECRET = "base-bearer"  # noqa: S105  -- test fixture, not a credential
_PROFILE_SECRET_REF = "PROFILE_KEY"  # noqa: S105  -- a ref name, not a secret
_BASE_SECRET_REF = "BASE_REF"  # noqa: S105  -- a ref name, not a secret
_PROFILE_SECRET = "profile-resolved-key"  # noqa: S105  -- test fixture, not a credential
_OTHER_PROVIDER = "vertex"


def _capturing_factory(captured: list[LLMSettings]) -> Callable[[LLMSettings], FakeLLM]:
    def _factory(cfg: LLMSettings) -> FakeLLM:
        captured.append(cfg)
        return FakeLLM(reply=f"{cfg.provider}:{cfg.model}")

    return _factory


def _request(profile: str | None = None) -> AgentRequest:
    metadata = {} if profile is None else {LLM_PROFILE_METADATA_KEY: profile}
    return AgentRequest(messages=[Message(role="user", content="hi")], metadata=metadata)


# ── build_llm_profiles ────────────────────────────────────────────────────────


def test_build_llm_profiles_empty_returns_empty_pair() -> None:
    """Default-OFF: no profile configured → no client, no summary, no factory call."""
    calls = 0

    def _factory(cfg: LLMSettings) -> FakeLLM:  # noqa: ARG001
        nonlocal calls
        calls += 1
        return FakeLLM()

    with llm_registry.scoped("lmstudio", _factory):
        result = build_llm_profiles({}, LLMSettings(), "env")
    assert result.clients == {}
    assert result.info == {}
    assert calls == 0


def test_build_llm_profiles_inherits_unset_fields_from_base() -> None:
    captured: list[LLMSettings] = []
    base = LLMSettings(
        base_url="http://base:1234/v1",
        api_key=_BASE_SECRET,
        timeout_seconds=12.5,
        temperature=0.7,
    )
    profiles = {TEST_LLM_PROFILE_NAME: LLMProfileSettings(provider="lmstudio", model="m")}
    with llm_registry.scoped("lmstudio", _capturing_factory(captured)):
        build_llm_profiles(profiles, base, "env")
    (cfg,) = captured
    assert cfg.model == "m"
    assert cfg.base_url == "http://base:1234/v1"
    assert cfg.timeout_seconds == 12.5
    assert cfg.temperature == 0.7
    # Same provider: the credential inherits.
    assert cfg.api_key == _BASE_SECRET


def test_build_llm_profiles_profile_fields_override_base() -> None:
    captured: list[LLMSettings] = []
    base = LLMSettings(base_url="http://base:1234/v1", temperature=0.7)
    profiles = {
        TEST_LLM_PROFILE_NAME: LLMProfileSettings(
            provider="lmstudio",
            model="m",
            base_url="http://host.docker.internal:1234/v1",
            temperature=0.0,
        )
    }
    with llm_registry.scoped("lmstudio", _capturing_factory(captured)):
        build_llm_profiles(profiles, base, "env")
    (cfg,) = captured
    assert cfg.base_url == "http://host.docker.internal:1234/v1"
    assert cfg.temperature == 0.0


def test_build_llm_profiles_rosters_are_always_the_bases() -> None:
    """``allowed_models``/``provider_allowlist`` are never per-profile."""
    captured: list[LLMSettings] = []
    base = LLMSettings(
        model=DEFAULT_LLM_MODEL,
        allowed_models=[DEFAULT_LLM_MODEL, "m"],
        provider_allowlist=["lmstudio"],
    )
    profiles = {TEST_LLM_PROFILE_NAME: LLMProfileSettings(provider="lmstudio", model="m")}
    with llm_registry.scoped("lmstudio", _capturing_factory(captured)):
        build_llm_profiles(profiles, base, "env")
    (cfg,) = captured
    assert cfg.allowed_models == [DEFAULT_LLM_MODEL, "m"]
    assert cfg.provider_allowlist == ["lmstudio"]


def test_cross_provider_profile_does_not_inherit_credentials() -> None:
    """A Vertex profile under an LM Studio base never receives the local bearer."""
    captured: list[LLMSettings] = []
    base = LLMSettings(
        provider="lmstudio",
        api_key=_BASE_SECRET,
        secret_ref=_BASE_SECRET_REF,
        credentials_path="/base/sa.json",
    )
    profiles = {
        TEST_LLM_PROFILE_NAME: LLMProfileSettings(
            provider=_OTHER_PROVIDER, model="gemini", project_id="p"
        )
    }
    with llm_registry.scoped(_OTHER_PROVIDER, _capturing_factory(captured)):
        build_llm_profiles(profiles, base, "env")
    (cfg,) = captured
    assert cfg.provider == _OTHER_PROVIDER
    assert cfg.api_key == LLMSettings.model_fields["api_key"].get_default()
    assert cfg.secret_ref is None
    assert cfg.credentials_path is None
    assert cfg.project_id == "p"


def test_same_provider_profile_inherits_credentials() -> None:
    """The accepting half of the rule above."""
    captured: list[LLMSettings] = []
    base = LLMSettings(provider="lmstudio", api_key=_BASE_SECRET, credentials_path="/p")
    profiles = {TEST_LLM_PROFILE_NAME: LLMProfileSettings(provider="lmstudio", model="m")}
    with llm_registry.scoped("lmstudio", _capturing_factory(captured)):
        build_llm_profiles(profiles, base, "env")
    (cfg,) = captured
    assert cfg.api_key == _BASE_SECRET
    assert cfg.credentials_path == "/p"


def test_profile_api_key_without_secret_ref_clears_inherited_secret_ref() -> None:
    """An explicit profile key must not be overwritten by the base's resolved secret."""
    captured: list[LLMSettings] = []
    base = LLMSettings(provider="lmstudio", api_key=_BASE_SECRET, secret_ref=_BASE_SECRET_REF)
    profiles = {
        TEST_LLM_PROFILE_NAME: LLMProfileSettings(
            provider="lmstudio", model="m", api_key="explicit"
        )
    }
    fake = FakeSecretsProvider(values={_BASE_SECRET_REF: "base-resolved"})
    with (
        secrets_registry.scoped("fake", fake),
        llm_registry.scoped("lmstudio", _capturing_factory(captured)),
    ):
        build_llm_profiles(profiles, base, "fake")
    (cfg,) = captured
    assert cfg.api_key == "explicit"
    assert cfg.secret_ref is None
    assert fake.calls == [], "the base secret must not be resolved for this profile"


def test_build_llm_profiles_resolves_profile_secret_ref() -> None:
    captured: list[LLMSettings] = []
    base = LLMSettings(provider="lmstudio")
    profiles = {
        TEST_LLM_PROFILE_NAME: LLMProfileSettings(
            provider="lmstudio", model="m", secret_ref=_PROFILE_SECRET_REF
        )
    }
    fake = FakeSecretsProvider(values={_PROFILE_SECRET_REF: _PROFILE_SECRET})
    with (
        secrets_registry.scoped("fake", fake),
        llm_registry.scoped("lmstudio", _capturing_factory(captured)),
    ):
        result = build_llm_profiles(profiles, base, "fake")
    (cfg,) = captured
    assert cfg.api_key == _PROFILE_SECRET
    assert fake.calls == [_PROFILE_SECRET_REF]
    # R6 at the composition seam: the summary carries no secret material.
    serialised = json.dumps(result.info)
    assert _PROFILE_SECRET not in serialised
    assert _PROFILE_SECRET_REF not in serialised
    assert "api_key" not in serialised
    assert "secret_ref" not in serialised
    assert result.info == {TEST_LLM_PROFILE_NAME: {"provider": "lmstudio", "model": "m"}}


def test_build_llm_profiles_uses_the_profiles_provider_factory() -> None:
    """Cross-provider is the point: the profile's provider picks the factory."""
    lm: list[LLMSettings] = []
    vx: list[LLMSettings] = []
    base = LLMSettings(provider="lmstudio")
    profiles = {
        "local": LLMProfileSettings(provider="lmstudio", model="a"),
        "cloud": LLMProfileSettings(provider=_OTHER_PROVIDER, model="b", project_id="p"),
    }
    with (
        llm_registry.scoped("lmstudio", _capturing_factory(lm)),
        llm_registry.scoped(_OTHER_PROVIDER, _capturing_factory(vx)),
    ):
        result = build_llm_profiles(profiles, base, "env")
    assert [c.model for c in lm] == ["a"]
    assert [c.model for c in vx] == ["b"]
    assert set(result.clients) == {"local", "cloud"}
    assert result.info["cloud"] == {"provider": _OTHER_PROVIDER, "model": "b"}


# ── build_orchestrator wiring ─────────────────────────────────────────────────


def _settings_with_profile() -> Settings:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = IN_MEMORY_SQLITE_URL
    settings.llm_profiles = {
        TEST_LLM_PROFILE_NAME: LLMProfileSettings(provider="lmstudio", model=TEST_LLM_PROFILE_MODEL)
    }
    return settings


def test_build_orchestrator_publishes_profile_extras() -> None:
    def _factory(cfg: LLMSettings) -> FakeLLM:
        return FakeLLM(reply=f"model={cfg.model}")

    with llm_registry.scoped("lmstudio", _factory):
        orch = build_orchestrator(_settings_with_profile())
        try:
            clients = orch.context.extras[LLM_PROFILES_EXTRAS_KEY]
            info = orch.context.extras[LLM_PROFILE_INFO_EXTRAS_KEY]
            assert set(clients) == {TEST_LLM_PROFILE_NAME}
            assert isinstance(clients[TEST_LLM_PROFILE_NAME], FakeLLM)
            assert clients[TEST_LLM_PROFILE_NAME] is not orch.context.llm
            assert info == {
                TEST_LLM_PROFILE_NAME: {"provider": "lmstudio", "model": TEST_LLM_PROFILE_MODEL}
            }
        finally:
            close_repo(orch)


def test_build_orchestrator_without_profiles_publishes_empty_extras() -> None:
    """Backward-compat guard: no profile configured → both keys present and empty."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = IN_MEMORY_SQLITE_URL
    with llm_registry.scoped("lmstudio", lambda cfg: FakeLLM()):  # noqa: ARG005
        orch = build_orchestrator(settings)
        try:
            assert orch.context.extras[LLM_PROFILES_EXTRAS_KEY] == {}
            assert orch.context.extras[LLM_PROFILE_INFO_EXTRAS_KEY] == {}
        finally:
            close_repo(orch)


async def test_build_orchestrator_dispatch_routes_to_the_profile_client() -> None:
    """End to end through the real composition: the profile client serves the run."""

    def _factory(cfg: LLMSettings) -> FakeLLM:
        return FakeLLM(reply=f"model={cfg.model}")

    with llm_registry.scoped("lmstudio", _factory):
        orch = build_orchestrator(_settings_with_profile())
        try:
            response = await orch.dispatch("chat", _request(TEST_LLM_PROFILE_NAME))
            assert response.content == f"model={TEST_LLM_PROFILE_MODEL}"
            assert response.metadata[LLM_PROFILE_METADATA_KEY] == TEST_LLM_PROFILE_NAME
            plain = await orch.dispatch("chat", _request())
            assert plain.content == f"model={DEFAULT_LLM_MODEL}"
            assert LLM_PROFILE_METADATA_KEY not in plain.metadata
        finally:
            close_repo(orch)


# ── close path ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("harness_enabled", [False, True], ids=["plain", "harness"])
async def test_aclose_closes_profile_clients(harness_enabled: bool) -> None:
    """Mutation proof: revert the ``_close_hooks`` extension and this goes red."""
    settings = _settings_with_profile()
    settings.harness.enabled = harness_enabled
    with llm_registry.scoped("lmstudio", lambda cfg: FakeLLM()):  # noqa: ARG005
        orch = build_orchestrator(settings)
    profile_llm = orch.context.extras[LLM_PROFILES_EXTRAS_KEY][TEST_LLM_PROFILE_NAME]
    assert isinstance(profile_llm, FakeLLM)
    assert not profile_llm.closed
    await orch.aclose()
    assert profile_llm.closed
    assert isinstance(orch, _HarnessOrchestrator) is harness_enabled


# ── _LLMProfileDispatchMixin ──────────────────────────────────────────────────


class _ProfileOrchestrator(_LLMProfileDispatchMixin, Orchestrator):
    """The mixin over the bare orchestrator, matching the composition shape."""


class _RecordingProfileOrchestrator(_LLMProfileDispatchMixin, _FailureRecordingMixin, Orchestrator):
    """The composition ordering: profile check *outside* failure recording."""


class _EchoAgent:
    name = "echo"

    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:  # noqa: ARG002
        self.calls += 1
        return AgentResponse(content="ok", agent=self.name, metadata={"kept": True})


def _mixin_orchestrator(
    cls: type[Orchestrator] = _ProfileOrchestrator, *, repo: FakeRepository | None = None
) -> tuple[Orchestrator, _EchoAgent]:
    ctx = AgentContext(
        llm=FakeLLM(),
        repo=repo,
        extras={LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: FakeLLM()}},
    )
    orch = cls(ctx)
    agent = _EchoAgent()
    orch.register(agent)
    return orch, agent


async def test_dispatch_stamps_llm_profile_on_response() -> None:
    orch, _ = _mixin_orchestrator()
    response = await orch.dispatch("echo", _request(TEST_LLM_PROFILE_NAME))
    assert response.metadata[LLM_PROFILE_METADATA_KEY] == TEST_LLM_PROFILE_NAME
    assert response.metadata["kept"] is True, "the agent's own metadata survives the stamp"


async def test_dispatch_without_profile_leaves_metadata_unchanged() -> None:
    """Default-OFF: no selector, no stamp — the response is the agent's own."""
    orch, _ = _mixin_orchestrator()
    response = await orch.dispatch("echo", _request())
    assert LLM_PROFILE_METADATA_KEY not in response.metadata
    assert response.metadata == {"kept": True, "loop": response.metadata.get("loop")} or (
        LLM_PROFILE_METADATA_KEY not in response.metadata
    )


async def test_dispatch_unknown_profile_raises_before_any_agent_call_and_records_no_row() -> None:
    """Position rule: an unknown profile is not a turn.

    Mutation proof: list the mixin *after* ``_FailureRecordingMixin`` and the
    ``ConfigError`` is recorded as a failed turn (repo row appears).
    """
    repo = FakeRepository()
    orch, agent = _mixin_orchestrator(_RecordingProfileOrchestrator, repo=repo)
    with pytest.raises(ConfigError, match=UNKNOWN_LLM_PROFILE_NAME):
        await orch.dispatch("echo", _request(UNKNOWN_LLM_PROFILE_NAME))
    assert agent.calls == 0
    assert repo._turns == []


def test_both_composition_orchestrators_carry_the_profile_mixin_first() -> None:
    """Both classes ``build_orchestrator`` can return must carry the mixin, ahead
    of failure recording and of ``Orchestrator`` — or the stamp is bypassed and
    an unknown profile becomes a recorded turn."""
    for cls in (_Orchestrator, _HarnessOrchestrator):
        mro = cls.__mro__
        assert _LLMProfileDispatchMixin in mro, f"{cls.__name__} lacks the profile mixin"
        assert mro.index(_LLMProfileDispatchMixin) < mro.index(_FailureRecordingMixin), (
            f"{cls.__name__} lists the profile mixin after failure recording"
        )
        assert mro.index(_LLMProfileDispatchMixin) < mro.index(Orchestrator)


async def test_pipeline_propagates_profile_to_every_stage() -> None:
    """``_pipeline_pass`` seeds each stage with ``response.metadata``, so the
    stamp is what carries the profile to stage two — a behaviour, not an
    accident, and one the spec documents."""
    default_llm = FakeLLM(reply="default")
    profile_llm = FakeLLM(reply="profile")
    ctx = AgentContext(
        llm=default_llm,
        repo=None,
        extras={LLM_PROFILES_EXTRAS_KEY: {TEST_LLM_PROFILE_NAME: profile_llm}},
    )
    orch = _ProfileOrchestrator(ctx)
    from mangomas.agents import ChatAgent, SummarizeAgent  # noqa: PLC0415

    orch.register(ChatAgent())
    orch.register(SummarizeAgent())
    response = await orch.dispatch_pipeline(["chat", "summarize"], _request(TEST_LLM_PROFILE_NAME))
    assert len(profile_llm.calls) == 2, "both stages must run on the profile client"
    assert default_llm.calls == []
    assert response.metadata[LLM_PROFILE_METADATA_KEY] == TEST_LLM_PROFILE_NAME
