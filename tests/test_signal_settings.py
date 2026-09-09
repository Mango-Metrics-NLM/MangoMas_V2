"""Tests for ``SignalSettings`` (``MANGOMAS_SIGNAL__*``)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mangomas.config import Settings, SignalSettings, get_settings, policy_snapshot_hash_for
from tests.constants import (
    DEFAULT_SIGNAL_DIR,
    DEFAULT_SIGNAL_ENABLED,
    DEFAULT_SIGNAL_GENAI_SPANS,
    DEFAULT_SIGNAL_HTTP_TIMEOUT_SECONDS,
    DEFAULT_SIGNAL_HTTP_URL,
    DEFAULT_SIGNAL_POLICY_ID,
    DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH,
    DEFAULT_SIGNAL_POLICY_VERSION,
    DEFAULT_SIGNAL_SCHEMA_VERSION,
    SIGNAL_CUSTOM_POLICY_ID,
    SIGNAL_DIR_ENV,
    SIGNAL_ENABLED_ENV,
    SIGNAL_POLICY_ID_ENV,
    SIGNAL_POLICY_SNAPSHOT_HASH_ENV,
)


def test_signal_defaults_match_constants() -> None:
    s = SignalSettings()
    assert s.enabled is DEFAULT_SIGNAL_ENABLED
    assert s.enabled is False
    assert s.dir == DEFAULT_SIGNAL_DIR
    assert s.schema_version == DEFAULT_SIGNAL_SCHEMA_VERSION
    assert s.genai_spans is DEFAULT_SIGNAL_GENAI_SPANS
    assert s.policy_id == DEFAULT_SIGNAL_POLICY_ID
    assert s.policy_version == DEFAULT_SIGNAL_POLICY_VERSION
    assert s.policy_snapshot_hash == DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH
    assert s.http_url is DEFAULT_SIGNAL_HTTP_URL
    assert s.http_timeout_seconds == DEFAULT_SIGNAL_HTTP_TIMEOUT_SECONDS


def test_settings_includes_signal_disabled_by_default() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert isinstance(s.signal, SignalSettings)
    assert s.signal.enabled is False


def test_signal_env_override_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIGNAL_ENABLED_ENV, "true")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.signal.enabled is True
        assert s.signal.dir == DEFAULT_SIGNAL_DIR
    finally:
        get_settings.cache_clear()


def test_signal_env_override_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIGNAL_DIR_ENV, "custom-signals")
    get_settings.cache_clear()
    try:
        assert get_settings().signal.dir == "custom-signals"
    finally:
        get_settings.cache_clear()


def test_signal_schema_version_rejects_1_0_0() -> None:
    with pytest.raises(ValidationError):
        SignalSettings(schema_version="1.0.0")  # type: ignore[arg-type]


def test_signal_policy_hash_must_be_sha256() -> None:
    with pytest.raises(ValidationError):
        SignalSettings(policy_snapshot_hash="not-a-hash")


def test_signal_http_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SignalSettings(http_timeout_seconds=0)


def test_signal_does_not_overload_harness_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__ENABLED", "true")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.harness.enabled is True
        assert s.signal.enabled is False
    finally:
        get_settings.cache_clear()


def test_policy_id_change_rebinds_default_hash() -> None:
    settings = SignalSettings(policy_id=SIGNAL_CUSTOM_POLICY_ID)
    assert settings.policy_snapshot_hash == policy_snapshot_hash_for(
        SIGNAL_CUSTOM_POLICY_ID, DEFAULT_SIGNAL_POLICY_VERSION
    )
    assert settings.policy_snapshot_hash != DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH


def test_explicit_policy_hash_is_kept_when_policy_id_changes() -> None:
    custom = policy_snapshot_hash_for("other.policy", "9")
    settings = SignalSettings(policy_id=SIGNAL_CUSTOM_POLICY_ID, policy_snapshot_hash=custom)
    assert settings.policy_snapshot_hash == custom


def test_explicit_default_policy_hash_is_kept_when_policy_id_changes() -> None:
    settings = SignalSettings(
        policy_id=SIGNAL_CUSTOM_POLICY_ID,
        policy_snapshot_hash=DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH,
    )
    assert settings.policy_snapshot_hash == DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH
    assert settings.policy_id == SIGNAL_CUSTOM_POLICY_ID


def test_signal_env_policy_id_rebinds_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIGNAL_POLICY_ID_ENV, SIGNAL_CUSTOM_POLICY_ID)
    get_settings.cache_clear()
    try:
        signal = get_settings().signal
        assert signal.policy_id == SIGNAL_CUSTOM_POLICY_ID
        assert signal.policy_snapshot_hash == policy_snapshot_hash_for(
            SIGNAL_CUSTOM_POLICY_ID, DEFAULT_SIGNAL_POLICY_VERSION
        )
    finally:
        get_settings.cache_clear()


def test_signal_env_explicit_default_hash_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SIGNAL_POLICY_ID_ENV, SIGNAL_CUSTOM_POLICY_ID)
    monkeypatch.setenv(SIGNAL_POLICY_SNAPSHOT_HASH_ENV, DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH)
    get_settings.cache_clear()
    try:
        signal = get_settings().signal
        assert signal.policy_id == SIGNAL_CUSTOM_POLICY_ID
        assert signal.policy_snapshot_hash == DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH
    finally:
        get_settings.cache_clear()
