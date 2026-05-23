"""Unit tests for :func:`mangomas.correlation.sanitize_inbound_correlation_id`.

Verifies the log-injection / oversize-header defences applied to inbound
``X-Request-ID`` values before they reach log records or the OTel baggage.
"""

from __future__ import annotations

import pytest

from mangomas.correlation import (
    MAX_CORRELATION_ID_LENGTH,
    resolve_correlation_id,
    sanitize_inbound_correlation_id,
)


@pytest.mark.parametrize("value", [None, "", "   ", "\t  \n  "])
def test_returns_none_for_empty_or_whitespace_only(value: str | None) -> None:
    assert sanitize_inbound_correlation_id(value) is None


def test_returns_value_unchanged_when_already_safe() -> None:
    assert sanitize_inbound_correlation_id("abc123") == "abc123"
    assert sanitize_inbound_correlation_id("trace-id_42") == "trace-id_42"
    # All allowed characters: alnum + _-./:
    safe = "a1_b2-c3.d4/e5:f6"
    assert sanitize_inbound_correlation_id(safe) == safe


def test_strips_surrounding_whitespace() -> None:
    assert sanitize_inbound_correlation_id("  abc123  ") == "abc123"


def test_drops_control_characters_to_block_log_injection() -> None:
    # CR/LF injection — the headline log-splitting attack.
    assert sanitize_inbound_correlation_id("good\r\nFAKE LOG LINE") == "goodFAKELOGLINE"
    # NUL bytes, tabs, escapes — all gone.
    assert sanitize_inbound_correlation_id("hello\x00world\tfoo\x1bbar") == "helloworldfoobar"


def test_drops_html_and_shell_metacharacters() -> None:
    # Angle brackets, parens, dollar, semicolons, pipes, ampersands stripped;
    # forward slash is intentionally in the allowed set (URLs, GCP secret refs).
    assert sanitize_inbound_correlation_id("<script>") == "script"
    assert sanitize_inbound_correlation_id("$(rm -rf /)") == "rm-rf/"
    assert sanitize_inbound_correlation_id("a;b|c&d") == "abcd"


def test_truncates_at_max_length() -> None:
    long = "a" * (MAX_CORRELATION_ID_LENGTH + 50)
    result = sanitize_inbound_correlation_id(long)
    assert result is not None
    assert len(result) == MAX_CORRELATION_ID_LENGTH


def test_returns_none_when_only_disallowed_chars_present() -> None:
    # All chars stripped → empty string → return None (caller mints fresh).
    assert sanitize_inbound_correlation_id("!@#$%^&*()") is None
    assert sanitize_inbound_correlation_id("\n\n\n") is None


def test_resolve_returns_inbound_when_safe() -> None:
    assert resolve_correlation_id("client-supplied-id") == "client-supplied-id"


def test_resolve_generates_fresh_id_when_inbound_empty() -> None:
    generated = resolve_correlation_id(None)
    assert generated is not None
    assert len(generated) == 8
    int(generated, 16)  # must parse as hex


def test_resolve_generates_fresh_id_when_inbound_is_all_garbage() -> None:
    generated = resolve_correlation_id("\n\r\x00")
    assert generated is not None
    assert len(generated) == 8


def test_resolve_truncates_oversize_inbound() -> None:
    long = "z" * 200
    result = resolve_correlation_id(long)
    assert len(result) == MAX_CORRELATION_ID_LENGTH
