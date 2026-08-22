"""Property tests for the shared header-token sanitiser.

``mangomas._headers.sanitize_header_token`` is, in its own docstring's words,
"the single log-injection / SQL-parameter defence for every identity token read
from an inbound HTTP header" — it guards both ``X-Request-ID`` (which reaches
log records and OTel baggage) and ``X-Tenant-ID`` (which reaches SQL as a bound
parameter). Until now it was defended entirely by hand-picked examples, which
can only ever prove the attacks someone already thought of.

These state the invariant over *arbitrary* text — including control
characters, surrogates and astral-plane codepoints that no example list would
have enumerated. A security boundary is exactly what property testing is for.

Import-guarded like the repo's five other fuzz files: ``hypothesis`` is an
optional dev dependency.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given
from hypothesis import strategies as st

from mangomas._headers import _ALLOWED_CHAR_PATTERN, sanitize_header_token

# Deliberately unrestricted: the whole point is that no input escapes the
# charset, so the strategy must be able to produce CR/LF, NUL, quotes, shell
# metacharacters, RTL marks and lone surrogates.
_any_text = st.text(max_size=200)
_lengths = st.integers(min_value=1, max_value=128)

# The characters an attacker needs for the two attacks this guards.
_ATTACK_CHARS = "\r\n\x00\t'\";`$|<>&%() \\"


@given(raw=_any_text, max_length=_lengths)
def test_output_never_exceeds_max_length(raw: str, max_length: int) -> None:
    assert len(sanitize_header_token(raw, max_length=max_length)) <= max_length


@given(raw=_any_text, max_length=_lengths)
def test_no_disallowed_character_ever_survives(raw: str, max_length: int) -> None:
    """The core invariant: nothing outside the allowed charset gets through."""
    out = sanitize_header_token(raw, max_length=max_length)
    assert _ALLOWED_CHAR_PATTERN.search(out) is None


@given(raw=_any_text, max_length=_lengths)
def test_sanitisation_is_idempotent(raw: str, max_length: int) -> None:
    """Re-sanitising a sanitised value must be a no-op.

    A non-idempotent filter means the output is still "dirty" in some
    representation — the classic double-decoding bypass.
    """
    once = sanitize_header_token(raw, max_length=max_length)
    assert sanitize_header_token(once, max_length=max_length) == once


@given(
    prefix=_any_text,
    attack=st.text(alphabet=_ATTACK_CHARS, min_size=1, max_size=20),
    suffix=_any_text,
    max_length=_lengths,
)
def test_injection_payloads_never_survive_anywhere_in_the_value(
    prefix: str, attack: str, suffix: str, max_length: int
) -> None:
    """CR/LF, NUL, quotes and shell metacharacters cannot reach a consumer.

    Positioned between arbitrary prefixes/suffixes so the payload is exercised
    at the start, middle and end of the value, not only where an example test
    happened to put it.
    """
    out = sanitize_header_token(prefix + attack + suffix, max_length=max_length)
    assert not any(char in out for char in _ATTACK_CHARS)


@given(raw=_any_text, max_length=_lengths)
def test_output_is_a_subsequence_of_the_input(raw: str, max_length: int) -> None:
    """The filter only ever *removes* — it must never invent a character.

    Guards against a future "normalising" rewrite (case folding, unicode NFKC,
    percent-decoding) that could map a benign input onto a dangerous one.
    """
    out = sanitize_header_token(raw, max_length=max_length)
    remaining = iter(raw)
    assert all(char in remaining for char in out)
