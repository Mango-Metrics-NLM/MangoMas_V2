"""Postgres storage factory and GCP secrets wiring."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from mangomas.composition import (
    _build_gcp_secrets_provider,
    _resolve_llm_secrets,
    _storage_registry,
    build_orchestrator,
    llm_registry,
)
from mangomas.config import (
    DEFAULT_GCP_SECRET_VERSION,
    DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS,
    DBSettings,
    LLMSettings,
    SecretsSettings,
    Settings,
)
from mangomas.errors import ConfigError
from mangomas.secrets import secrets_registry
from tests.composition.helpers import close_repo
from tests.constants import IN_MEMORY_SQLITE_URL


def test_postgres_factory_registered_in_storage_registry() -> None:
    assert "postgres" in _storage_registry.available()


def test_postgres_factory_constructs_repo_without_io() -> None:
    """The postgres factory must not open a pool — preserves the sync shape."""
    factory = _storage_registry.get("postgres")
    cfg = DBSettings(provider="postgres", url="postgresql://h/db")
    repo = factory(cfg)
    # Lazy-pool invariant from PostgresRepository.
    assert repo._pool is None


# Test-scoped: a DSN whose userinfo carries a password (no config counterpart).
_DSN_WITH_PASSWORD = "postgresql://svc:s3cret-pw@db.internal:5432/mangomas"  # noqa: S105 — fixture, not a secret
_DSN_PASSWORD = "s3cret-pw"  # noqa: S105 — the fixture's userinfo, asserted absent from logs


def test_postgres_factory_never_logs_the_dsn_password(caplog: pytest.LogCaptureFixture) -> None:
    """The factory's DEBUG record carries the host only, never the URL.

    ``MANGOMAS_DB__URL`` is a Secret Manager secret on Cloud Run and dev runs
    at DEBUG with the JSON formatter forwarding every ``extra`` field, so a
    ``{"url": cfg.url}`` record (the previous code) shipped the password to
    Cloud Logging on every startup (spec-0038; Copilot review on PR #101).
    Mutation (proved while writing): restore ``extra={"url": cfg.url}`` and
    this fails on the password check.
    """
    factory = _storage_registry.get("postgres")
    with caplog.at_level(logging.DEBUG, logger="mangomas.composition.storage"):
        factory(DBSettings(provider="postgres", url=_DSN_WITH_PASSWORD))
    records = [r for r in caplog.records if r.name == "mangomas.composition.storage"]
    assert records, "the factory no longer logs at DEBUG — the guard is vacuous"
    for record in records:
        rendered = record.getMessage() + repr(vars(record))
        assert _DSN_PASSWORD not in rendered
        assert _DSN_WITH_PASSWORD not in rendered
    assert getattr(records[0], "dsn_host", None) == "db.internal"


def test_gcp_secrets_lazy_registers_on_build_when_provider_selected() -> None:
    """When secrets.provider='gcp', build_orchestrator must lazy-register it."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url=IN_MEMORY_SQLITE_URL),
        secrets=SecretsSettings(provider="gcp", project_id="test-proj"),
    )
    # Drop any prior gcp binding so we can observe the lazy registration,
    # restoring it in `finally` — build_orchestrator registers a *real*
    # GCPSecretManagerProvider into this process-wide singleton, and leaving
    # it there would leak into every test that runs after this one.
    prior = secrets_registry._store.pop("gcp", None)

    captured: dict[str, Any] = {}

    def _capturing_llm_factory(cfg: LLMSettings) -> object:
        captured["api_key"] = cfg.api_key

        class _Stub:
            async def aclose(self) -> None: ...

        return _Stub()

    try:
        with llm_registry.scoped("lmstudio", _capturing_llm_factory):
            orch = build_orchestrator(settings)
            try:
                assert "gcp" in secrets_registry.available()
            finally:
                close_repo(orch)
    finally:
        if prior is None:
            secrets_registry._store.pop("gcp", None)
        else:
            secrets_registry._store["gcp"] = prior


def test_gcp_secrets_lazy_register_raises_when_project_id_missing() -> None:
    """build_orchestrator must surface the ConfigError eagerly when misconfigured."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url=IN_MEMORY_SQLITE_URL),
        secrets=SecretsSettings(provider="gcp", project_id=None),
    )
    prior = secrets_registry._store.pop("gcp", None)
    try:
        with pytest.raises(ConfigError, match="MANGOMAS_SECRETS__PROJECT_ID"):
            build_orchestrator(settings)
    finally:
        if prior is None:
            secrets_registry._store.pop("gcp", None)
        else:
            secrets_registry._store["gcp"] = prior


def test_build_gcp_secrets_provider_returns_provider_when_valid() -> None:
    """Success branch of _build_gcp_secrets_provider — config valid, no SDK call."""
    cfg = SecretsSettings(
        provider="gcp",
        project_id="my-proj",
        timeout_seconds=DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS,
        default_version=DEFAULT_GCP_SECRET_VERSION,
    )
    provider = _build_gcp_secrets_provider(cfg)
    assert provider is not None
    assert provider._project_id == "my-proj"


def test_build_gcp_secrets_provider_raises_without_project_id() -> None:
    """_build_gcp_secrets_provider must raise ConfigError when project_id is None."""
    cfg = SecretsSettings(provider="gcp", project_id=None)
    with pytest.raises(ConfigError, match="MANGOMAS_SECRETS__PROJECT_ID"):
        _build_gcp_secrets_provider(cfg)


def test_resolve_llm_secrets_returns_unchanged_when_no_ref() -> None:
    """_resolve_llm_secrets returns input unchanged when secret_ref is unset."""
    cfg = LLMSettings(provider="lmstudio", api_key="inline-key")
    result = _resolve_llm_secrets(cfg, "env")
    assert result is cfg  # same object, unmodified
    assert result.api_key == "inline-key"


def test_resolve_llm_secrets_updates_when_provider_resolves() -> None:
    """_resolve_llm_secrets updates api_key when provider.get succeeds."""
    cfg = LLMSettings(
        provider="lmstudio",
        api_key="inline",
        secret_ref="my-secret",  # noqa: S106
    )

    class _FakeProvider:
        def get(self, ref: str) -> str | None:
            return "resolved-key" if ref == "my-secret" else None

    with secrets_registry.scoped("fake", _FakeProvider()):
        result = _resolve_llm_secrets(cfg, "fake")
    assert result.api_key == "resolved-key"
    assert result.secret_ref == "my-secret"  # unchanged  # noqa: S105


def test_resolve_llm_secrets_keeps_inline_key_when_provider_returns_none() -> None:
    """When the provider has no value for ``secret_ref``, the inline ``api_key``
    survives unchanged — local development with no vault entry configured
    must keep working rather than resolving to ``None``."""
    cfg = LLMSettings(
        provider="lmstudio",
        api_key="inline-fallback",
        secret_ref="unconfigured-ref",  # noqa: S106
    )

    class _EmptyProvider:
        def get(self, _ref: str) -> str | None:
            return None

    with secrets_registry.scoped("empty", _EmptyProvider()):
        result = _resolve_llm_secrets(cfg, "empty")
    assert result.api_key == "inline-fallback"
    assert result is cfg  # unresolved falls back to the original object, no copy
