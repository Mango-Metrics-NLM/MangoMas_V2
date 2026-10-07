"""Composition wiring for the document parser (spec-0035 R10 / ADR-0036 §10)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from mangomas.adapters.parsers import PARSER_EXTRAS_KEY, DoclingServeParser
from mangomas.composition import (
    _HarnessOrchestrator,
    _parser_registry,
    _parser_secrets_provider,
    build_orchestrator,
    build_parser,
    llm_registry,
    secrets_registry,
)
from mangomas.config import LLMSettings, ParserSettings, Settings
from mangomas.core import Orchestrator
from tests.constants import DEFAULT_PARSER_PROVIDER, PARSER_ENABLED_ENV
from tests.constants.docling import (
    DB_URL_ENV,
    HARNESS_ENABLED_ENV,
    PARSER_BASE_URL_ENV,
    SPEC_DOCLING_PARSER_CLOSE_LABEL,
    SPEC_DOCLING_PROVIDER,
    TEST_DOCLING_BASE_HOST,
    TEST_DOCLING_BASE_URL,
    TEST_DOCLING_RESOLVED_KEY,
    TEST_DOCLING_SECRET_REF,
    TEST_DOCLING_SENTINEL_KEY,
    TEST_DOCLING_UNRESOLVED_REF,
    TEST_IN_MEMORY_DB_URL,
)
from tests.fakes import FakeDocumentParser, FakeLLM, FakeSecretsProvider

_BUILDER_LOGGER = "mangomas.composition.builder"


class _RecordingFactory:
    """Parser factory double: records each call and returns a fake parser."""

    def __init__(self, parser: Any | None = None) -> None:
        self.calls: list[tuple[ParserSettings, str | None]] = []
        self.parser = parser if parser is not None else FakeDocumentParser()

    def __call__(self, settings: ParserSettings, *, api_key: str | None) -> Any:
        self.calls.append((settings, api_key))
        return self.parser


class _RaisingCloseParser(FakeDocumentParser):
    async def aclose(self) -> None:
        await super().aclose()
        raise RuntimeError("parser close failed")


class _RaisingCloseLLM(FakeLLM):
    async def aclose(self) -> None:
        await super().aclose()
        raise RuntimeError("llm close failed")


def _parser_settings(**overrides: Any) -> ParserSettings:
    values: dict[str, Any] = {"enabled": True, "base_url": TEST_DOCLING_BASE_URL}
    values.update(overrides)
    return ParserSettings(**values)


def _settings(parser: ParserSettings | None = None, *, harness: bool = False) -> Settings:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = TEST_IN_MEMORY_DB_URL
    settings.harness.enabled = harness
    if parser is not None:
        settings.parser = parser
    return settings


def _fake_llm_factory(llm: FakeLLM) -> Any:
    def _factory(cfg: LLMSettings) -> FakeLLM:  # noqa: ARG001
        return llm

    return _factory


def _build(settings: Settings, llm: FakeLLM | None = None) -> Orchestrator:
    with llm_registry.scoped("lmstudio", _fake_llm_factory(llm or FakeLLM())):
        return build_orchestrator(settings)


# ── Registry ──────────────────────────────────────────────────────────────────


def test_registry_lists_docling_serve() -> None:
    assert SPEC_DOCLING_PROVIDER in _parser_registry.available()
    assert DEFAULT_PARSER_PROVIDER == SPEC_DOCLING_PROVIDER


# ── Disabled: nothing constructed ─────────────────────────────────────────────


async def test_disabled_builds_nothing_and_opens_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[httpx.AsyncClient] = []
    original_init = httpx.AsyncClient.__init__

    def _counting_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        clients.append(self)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _counting_init)
    factory = _RecordingFactory()
    secrets = FakeSecretsProvider(values={TEST_DOCLING_SECRET_REF: TEST_DOCLING_RESOLVED_KEY})
    disabled = ParserSettings(secret_ref=TEST_DOCLING_SECRET_REF)

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory):
        assert build_parser(disabled, secrets) is None
        orch = _build(_settings())

    assert factory.calls == []
    assert secrets.calls == []
    assert clients == []
    assert PARSER_EXTRAS_KEY not in orch.context.extras
    await orch.aclose()


def test_disabled_parser_never_looks_up_a_secrets_provider() -> None:
    disabled = ParserSettings(secret_ref=TEST_DOCLING_SECRET_REF)
    assert _parser_secrets_provider(disabled, "no-such-provider") is None


def test_enabled_parser_without_secret_ref_needs_no_secrets_provider() -> None:
    assert _parser_secrets_provider(_parser_settings(), "no-such-provider") is None


# ── Enabled: attached ─────────────────────────────────────────────────────────


async def test_enabled_attaches_real_parser_to_extras(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_BUILDER_LOGGER):
        orch = _build(_settings(_parser_settings()))

    try:
        assert isinstance(orch.context.extras[PARSER_EXTRAS_KEY], DoclingServeParser)
    finally:
        await orch.aclose()

    [record] = [r for r in caplog.records if getattr(r, "event", None) == "parser_enabled"]
    assert record.provider == SPEC_DOCLING_PROVIDER  # type: ignore[attr-defined]
    assert record.base_url_host == TEST_DOCLING_BASE_HOST  # type: ignore[attr-defined]
    assert record.auth_mode == "none"  # type: ignore[attr-defined]
    assert TEST_DOCLING_BASE_URL not in record.getMessage()


def test_enabled_api_key_mode_without_any_key_fails_at_build() -> None:
    from mangomas.errors import ConfigError  # noqa: PLC0415

    settings = _parser_settings(auth_mode="api_key", secret_ref=TEST_DOCLING_UNRESOLVED_REF)

    with pytest.raises(ConfigError):
        build_parser(settings, FakeSecretsProvider())


# ── Secret resolution ─────────────────────────────────────────────────────────


def test_secret_ref_overrides_api_key() -> None:
    factory = _RecordingFactory()
    secrets = FakeSecretsProvider(values={TEST_DOCLING_SECRET_REF: TEST_DOCLING_RESOLVED_KEY})
    settings = _parser_settings(
        auth_mode="api_key",
        api_key=TEST_DOCLING_SENTINEL_KEY,
        secret_ref=TEST_DOCLING_SECRET_REF,
    )

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory):
        parser = build_parser(settings, secrets)

    assert parser is factory.parser
    assert [api_key for _, api_key in factory.calls] == [TEST_DOCLING_RESOLVED_KEY]
    assert secrets.calls == [TEST_DOCLING_SECRET_REF]


def test_unresolved_secret_ref_keeps_inline_api_key() -> None:
    factory = _RecordingFactory()
    settings = _parser_settings(
        auth_mode="api_key",
        api_key=TEST_DOCLING_SENTINEL_KEY,
        secret_ref=TEST_DOCLING_UNRESOLVED_REF,
    )

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory):
        build_parser(settings, FakeSecretsProvider())

    assert [api_key for _, api_key in factory.calls] == [TEST_DOCLING_SENTINEL_KEY]


def test_inline_api_key_used_without_secret_ref() -> None:
    factory = _RecordingFactory()
    settings = _parser_settings(auth_mode="api_key", api_key=TEST_DOCLING_SENTINEL_KEY)

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory):
        build_parser(settings, None)

    assert [api_key for _, api_key in factory.calls] == [TEST_DOCLING_SENTINEL_KEY]


async def test_builder_resolves_secret_ref_through_the_configured_provider() -> None:
    factory = _RecordingFactory()
    secrets = FakeSecretsProvider(values={TEST_DOCLING_SECRET_REF: TEST_DOCLING_RESOLVED_KEY})
    settings = _settings(
        _parser_settings(
            auth_mode="api_key",
            api_key=TEST_DOCLING_SENTINEL_KEY,
            secret_ref=TEST_DOCLING_SECRET_REF,
        )
    )

    with (
        _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory),
        secrets_registry.scoped(settings.secrets.provider, secrets),
    ):
        orch = _build(settings)

    assert [api_key for _, api_key in factory.calls] == [TEST_DOCLING_RESOLVED_KEY]
    assert orch.context.extras[PARSER_EXTRAS_KEY] is factory.parser
    await orch.aclose()


# ── Teardown through orch.aclose() ────────────────────────────────────────────


@pytest.mark.parametrize("harness", [False, True], ids=["plain", "harness"])
async def test_orchestrator_aclose_closes_parser_exactly_once(harness: bool) -> None:
    parser = FakeDocumentParser()
    factory = _RecordingFactory(parser)

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory):
        orch = _build(_settings(_parser_settings(), harness=harness))

    assert isinstance(orch, _HarnessOrchestrator) is harness
    labels = [label for label, _ in orch._close_hooks()]
    assert labels.count(SPEC_DOCLING_PARSER_CLOSE_LABEL) == 1

    await orch.aclose()
    await orch.aclose()  # second call is a no-op

    assert parser.close_count == 1


@pytest.mark.parametrize("harness", [False, True], ids=["plain", "harness"])
async def test_raising_parser_aclose_does_not_stop_other_hooks(harness: bool) -> None:
    parser = _RaisingCloseParser()
    llm = FakeLLM()
    factory = _RecordingFactory(parser)

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory):
        orch = _build(_settings(_parser_settings(), harness=harness), llm)
    assert isinstance(orch, _HarnessOrchestrator) is harness

    with pytest.raises(RuntimeError):
        await orch.aclose()

    assert parser.close_count == 1
    assert llm.closed is True
    await orch.aclose()  # latched: no second attempt, no raise
    assert parser.close_count == 1


@pytest.mark.parametrize("harness", [False, True], ids=["plain", "harness"])
async def test_raising_earlier_hook_does_not_stop_the_parser_close(harness: bool) -> None:
    """The other direction: the parser hook runs after a failing LLM hook."""
    parser = FakeDocumentParser()
    llm = _RaisingCloseLLM()
    factory = _RecordingFactory(parser)

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, factory):
        orch = _build(_settings(_parser_settings(), harness=harness), llm)
    assert isinstance(orch, _HarnessOrchestrator) is harness

    with pytest.raises(RuntimeError):
        await orch.aclose()

    assert llm.closed is True
    assert parser.close_count == 1


@pytest.mark.parametrize("harness_env", ["false", "true"])
async def test_env_driven_parser_closes_once_on_both_orchestrators(
    monkeypatch: pytest.MonkeyPatch, harness_env: str
) -> None:
    """Same contract, configured purely through MANGOMAS_* env vars."""
    monkeypatch.setenv(HARNESS_ENABLED_ENV, harness_env)
    monkeypatch.setenv(PARSER_ENABLED_ENV, "true")
    monkeypatch.setenv(PARSER_BASE_URL_ENV, TEST_DOCLING_BASE_URL)
    monkeypatch.setenv(DB_URL_ENV, TEST_IN_MEMORY_DB_URL)
    parser = FakeDocumentParser()

    with _parser_registry.scoped(SPEC_DOCLING_PROVIDER, _RecordingFactory(parser)):
        orch = _build(Settings(_env_file=None))  # type: ignore[call-arg]

    assert isinstance(orch, _HarnessOrchestrator) is (harness_env == "true")
    await orch.aclose()
    await orch.aclose()
    assert parser.close_count == 1


async def test_no_parser_hook_when_disabled() -> None:
    orch = _build(_settings())
    try:
        labels = [label for label, _ in orch._close_hooks()]
        assert SPEC_DOCLING_PARSER_CLOSE_LABEL not in labels
    finally:
        await orch.aclose()
