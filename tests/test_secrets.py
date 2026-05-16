"""Tests for the SecretsProvider seam.

Covers the env-var backend, the secrets registry, the protocol
isinstance check, and end-to-end substitution at orchestrator-build
time in :func:`mangomas.composition.build_orchestrator`.
"""

from __future__ import annotations

import pytest

from mangomas.composition import _resolve_llm_secrets, build_orchestrator, llm_registry
from mangomas.config import DBSettings, LLMSettings, SecretsSettings, Settings
from mangomas.secrets import EnvSecretsProvider, SecretsProvider, secrets_registry
from tests.fakes import FakeSecretsProvider

# ── Protocol + env backend ───────────────────────────────────────────────────


def test_env_provider_satisfies_protocol() -> None:
    assert isinstance(EnvSecretsProvider(), SecretsProvider)


def test_env_provider_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_TEST_SECRET", "top-secret-value")
    assert EnvSecretsProvider().get("MANGOMAS_TEST_SECRET") == "top-secret-value"


def test_env_provider_returns_none_when_var_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MANGOMAS_TEST_MISSING", raising=False)
    assert EnvSecretsProvider().get("MANGOMAS_TEST_MISSING") is None


# ── Registry ─────────────────────────────────────────────────────────────────


def test_secrets_registry_seeded_with_env_backend() -> None:
    provider = secrets_registry.get("env")
    assert isinstance(provider, EnvSecretsProvider)


def test_fake_provider_satisfies_protocol() -> None:
    assert isinstance(FakeSecretsProvider(), SecretsProvider)


# ── Resolution helper ────────────────────────────────────────────────────────


def test_resolve_returns_original_when_no_secret_ref() -> None:
    cfg = LLMSettings(api_key="inline-key", secret_ref=None)
    resolved = _resolve_llm_secrets(cfg, "env")
    assert resolved is cfg
    assert resolved.api_key == "inline-key"


def test_resolve_replaces_api_key_when_secret_ref_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MANGOMAS_SECRET_KEY", "vault-key")
    cfg = LLMSettings(api_key="inline-fallback", secret_ref="MANGOMAS_SECRET_KEY")  # noqa: S106  -- env var name, not a secret
    resolved = _resolve_llm_secrets(cfg, "env")
    assert resolved.api_key == "vault-key"
    # Original instance must not be mutated.
    assert cfg.api_key == "inline-fallback"


def test_resolve_keeps_inline_api_key_when_secret_ref_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MANGOMAS_SECRET_MISSING", raising=False)
    cfg = LLMSettings(api_key="inline-fallback", secret_ref="MANGOMAS_SECRET_MISSING")  # noqa: S106  -- env var name, not a secret
    resolved = _resolve_llm_secrets(cfg, "env")
    assert resolved.api_key == "inline-fallback"


def test_resolve_uses_registered_provider() -> None:
    fake = FakeSecretsProvider(values={"VAULT_KEY": "from-fake"})
    with secrets_registry.scoped("fake", fake):
        cfg = LLMSettings(api_key="inline", secret_ref="VAULT_KEY")  # noqa: S106  -- ref string, not a secret
        resolved = _resolve_llm_secrets(cfg, "fake")
    assert resolved.api_key == "from-fake"
    assert fake.calls == ["VAULT_KEY"]


# ── End-to-end via build_orchestrator ────────────────────────────────────────


def test_build_orchestrator_substitutes_api_key_from_secrets_provider() -> None:
    captured: dict[str, str] = {}

    def _capturing_factory(cfg: LLMSettings) -> object:
        captured["api_key"] = cfg.api_key

        # Return a minimal object with aclose so cleanup doesn't crash.
        class _Stub:
            async def aclose(self) -> None: ...

        return _Stub()

    fake = FakeSecretsProvider(values={"SECRET_FROM_VAULT": "resolved-via-vault"})
    settings = Settings(
        llm=LLMSettings(
            provider="lmstudio",
            api_key="inline",
            secret_ref="SECRET_FROM_VAULT",  # noqa: S106  -- ref string, not a secret
        ),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
        secrets=SecretsSettings(provider="fake"),
    )

    with (
        llm_registry.scoped("lmstudio", _capturing_factory),
        secrets_registry.scoped("fake", fake),
    ):
        orch = build_orchestrator(settings)
        # Ensure cleanup happens.
        if orch.context.repo is not None:
            orch.context.repo.close()

    assert captured["api_key"] == "resolved-via-vault"
    assert fake.calls == ["SECRET_FROM_VAULT"]
