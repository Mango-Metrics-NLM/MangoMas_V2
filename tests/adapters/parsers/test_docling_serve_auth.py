"""Tests for docling-serve authentication strategies and Google ID token management."""

from __future__ import annotations

import base64
import sys

import pytest
import respx

from mangomas.adapters.parsers import DoclingServeParser
from mangomas.adapters.parsers._auth import (
    GOOGLE_AUTH_INSTALL_HINT,
    GoogleIdTokenProvider,
    id_token_expiry,
)
from mangomas.errors import ConfigError
from tests.adapters.parsers.conftest import (
    _CONVERT_URL,
    _Clock,
    _jwt,
    _ok,
    _parser,
    _settings,
)
from tests.constants.docling import (
    DOCLING_FIXTURE_SUCCESS,
    SPEC_DOCLING_API_KEY_HEADER,
    TEST_DOCLING_AUDIENCE,
    TEST_DOCLING_BASE_URL,
    TEST_DOCLING_PDF_BYTES,
    TEST_DOCLING_PDF_NAME,
    TEST_DOCLING_SENTINEL_KEY,
    TEST_DOCLING_UNRESOLVED_REF,
    TEST_ID_TOKEN_FIRST,
    TEST_ID_TOKEN_LIFETIME,
    TEST_ID_TOKEN_NOW,
    TEST_ID_TOKEN_REFRESH_MARGIN,
    TEST_ID_TOKEN_SECOND,
    load_docling_fixture,
)
from tests.fakes import FakeIdTokenProvider

# ── API Key & None Modes ──────────────────────────────────────────────────────


@respx.mock
async def test_api_key_header_sent_in_api_key_mode() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    parser = DoclingServeParser(
        _settings(auth_mode="api_key", api_key=TEST_DOCLING_SENTINEL_KEY),
        api_key=TEST_DOCLING_SENTINEL_KEY,
    )

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    headers = route.calls.last.request.headers
    assert headers[SPEC_DOCLING_API_KEY_HEADER] == TEST_DOCLING_SENTINEL_KEY
    assert "authorization" not in headers


@respx.mock
async def test_no_auth_header_in_none_mode_even_with_a_key() -> None:
    """Two-sided: `none` mode never sends a key, even if one is supplied."""
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    parser = DoclingServeParser(_settings(), api_key=TEST_DOCLING_SENTINEL_KEY)

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    headers = route.calls.last.request.headers
    assert SPEC_DOCLING_API_KEY_HEADER not in headers
    assert "authorization" not in headers
    assert TEST_DOCLING_SENTINEL_KEY.encode() not in route.calls.last.request.read()


def test_api_key_mode_without_key_is_a_config_error() -> None:
    settings = _settings(auth_mode="api_key", secret_ref=TEST_DOCLING_UNRESOLVED_REF)

    with pytest.raises(ConfigError):
        DoclingServeParser(settings, api_key=None)


# ── Google ID Token Mode ──────────────────────────────────────────────────────


@respx.mock
async def test_id_token_bearer_header_cached_then_refreshed() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(
        tokens=[TEST_ID_TOKEN_FIRST, TEST_ID_TOKEN_SECOND],
        clock=clock,
        lifetime=TEST_ID_TOKEN_LIFETIME,
    )
    parser = DoclingServeParser(
        _settings(
            auth_mode="google_id_token",
            id_token_refresh_margin_seconds=TEST_ID_TOKEN_REFRESH_MARGIN,
        ),
        api_key=TEST_DOCLING_SENTINEL_KEY,  # ignored in this mode
        token_provider=provider,
        clock=clock,
    )
    refresh_at = TEST_ID_TOKEN_NOW + TEST_ID_TOKEN_LIFETIME - TEST_ID_TOKEN_REFRESH_MARGIN

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)
    clock.now = refresh_at - 1
    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)
    assert len(provider.audiences) == 1  # cached inside the margin

    clock.now = refresh_at
    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)
    assert len(provider.audiences) == 2  # re-minted at the margin

    sent = [call.request.headers["authorization"] for call in route.calls]
    assert sent == [
        f"Bearer {TEST_ID_TOKEN_FIRST}",
        f"Bearer {TEST_ID_TOKEN_FIRST}",
        f"Bearer {TEST_ID_TOKEN_SECOND}",
    ]
    for call in route.calls:
        assert SPEC_DOCLING_API_KEY_HEADER not in call.request.headers
    # Audience defaults to the (normalised) base URL.
    assert provider.audiences == [TEST_DOCLING_BASE_URL, TEST_DOCLING_BASE_URL]


@respx.mock
async def test_id_token_uses_explicit_audience() -> None:
    respx.post(_CONVERT_URL).mock(return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS)))
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(
        tokens=[TEST_ID_TOKEN_FIRST], clock=clock, lifetime=TEST_ID_TOKEN_LIFETIME
    )
    parser = DoclingServeParser(
        _settings(auth_mode="google_id_token", id_token_audience=TEST_DOCLING_AUDIENCE),
        token_provider=provider,
        clock=clock,
    )

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert provider.audiences == [TEST_DOCLING_AUDIENCE]


@respx.mock
async def test_id_token_provider_failure_is_config_error_without_network() -> None:
    route = respx.post(_CONVERT_URL)
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(
        tokens=[],
        clock=clock,
        lifetime=TEST_ID_TOKEN_LIFETIME,
        error=RuntimeError(TEST_DOCLING_SENTINEL_KEY),
    )
    parser = DoclingServeParser(
        _settings(auth_mode="google_id_token"), token_provider=provider, clock=clock
    )

    with pytest.raises(ConfigError) as excinfo:
        await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert route.call_count == 0
    assert TEST_DOCLING_SENTINEL_KEY not in str(excinfo.value)
    assert TEST_DOCLING_SENTINEL_KEY not in excinfo.value.detail
    assert "RuntimeError" in excinfo.value.detail


@respx.mock
async def test_id_token_provider_returning_empty_token_is_config_error() -> None:
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(tokens=[""], clock=clock, lifetime=TEST_ID_TOKEN_LIFETIME)
    parser = DoclingServeParser(
        _settings(auth_mode="google_id_token"), token_provider=provider, clock=clock
    )

    with pytest.raises(ConfigError):
        await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)


def test_google_id_token_mode_defaults_to_the_google_provider() -> None:
    parser = _parser(auth_mode="google_id_token")
    assert isinstance(parser.token_provider, GoogleIdTokenProvider)


def test_non_id_token_modes_hold_no_token_provider() -> None:
    assert _parser().token_provider is None


# ── GoogleIdTokenProvider Unit Tests ──────────────────────────────────────────


async def test_google_provider_reads_expiry_from_the_jwt() -> None:
    expiry = TEST_ID_TOKEN_NOW + TEST_ID_TOKEN_LIFETIME
    token = _jwt({"exp": expiry, "aud": TEST_DOCLING_AUDIENCE})
    seen: list[str] = []

    def _fetcher(audience: str) -> str:
        seen.append(audience)
        return token

    minted, minted_expiry = await GoogleIdTokenProvider(fetcher=_fetcher).fetch(
        TEST_DOCLING_AUDIENCE
    )

    assert (minted, minted_expiry) == (token, expiry)
    assert seen == [TEST_DOCLING_AUDIENCE]


async def test_google_provider_wraps_fetch_failure_by_class_name_only() -> None:
    def _fetcher(audience: str) -> str:  # noqa: ARG001
        raise ValueError(TEST_DOCLING_SENTINEL_KEY)

    with pytest.raises(ConfigError) as excinfo:
        await GoogleIdTokenProvider(fetcher=_fetcher).fetch(TEST_DOCLING_AUDIENCE)

    assert excinfo.value.detail == "ValueError"
    assert TEST_DOCLING_SENTINEL_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    "missing", ["google.oauth2", "google.auth.transport"], ids=["oauth2", "transport"]
)
async def test_google_provider_without_sdk_names_the_gcp_extra(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """No google-auth installed → ConfigError with the install hint, at first use."""
    monkeypatch.setitem(sys.modules, missing, None)

    with pytest.raises(ConfigError) as excinfo:
        await GoogleIdTokenProvider().fetch(TEST_DOCLING_AUDIENCE)

    assert str(excinfo.value) == GOOGLE_AUTH_INSTALL_HINT
    assert "pip install 'mangomas[gcp]'" in str(excinfo.value)


@respx.mock
async def test_id_token_mode_without_sdk_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the default provider: no SDK → ConfigError, zero calls."""
    monkeypatch.setitem(sys.modules, "google.oauth2", None)
    route = respx.post(_CONVERT_URL)

    with pytest.raises(ConfigError) as excinfo:
        await _parser(auth_mode="google_id_token").parse(
            filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES
        )

    assert "mangomas[gcp]" in str(excinfo.value)
    assert route.call_count == 0


async def test_google_provider_passes_config_errors_through() -> None:
    hint = ConfigError("install the extra")

    def _fetcher(audience: str) -> str:  # noqa: ARG001
        raise hint

    with pytest.raises(ConfigError) as excinfo:
        await GoogleIdTokenProvider(fetcher=_fetcher).fetch(TEST_DOCLING_AUDIENCE)

    assert excinfo.value is hint


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        "a.!!!.c",
        f"a.{base64.urlsafe_b64encode(b'[1, 2]').decode()}.c",
        _jwt({"aud": TEST_DOCLING_AUDIENCE}),
        _jwt({"exp": "soon"}),
        _jwt({"exp": True}),
    ],
    ids=["segments", "base64", "not-object", "no-exp", "str-exp", "bool-exp"],
)
def test_id_token_expiry_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        id_token_expiry(token)
    assert token not in str(excinfo.value)
    assert token not in excinfo.value.detail
