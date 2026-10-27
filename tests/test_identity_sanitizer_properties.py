"""Property tests for the two header-identity wrappers.

``_headers.sanitize_header_token`` is already fuzzed directly
(``tests/test_headers_properties.py``). The two *wrappers* that every request
actually goes through — ``tenancy.sanitize_tenant`` (reaches SQL as a bound
parameter) and ``correlation.sanitize_inbound_correlation_id`` (reaches log
records and OTel baggage) — add their own contract on top: ``None`` in,
``None`` out; an empty survivor becomes ``None`` rather than ``""``; and each
caps at its own module constant. Those are what these state.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from mangomas._headers import _ALLOWED_CHAR_PATTERN
from mangomas.correlation import MAX_CORRELATION_ID_LENGTH, sanitize_inbound_correlation_id
from mangomas.tenancy import MAX_TENANT_ID_LENGTH, sanitize_tenant

_any_text = st.text(max_size=200)


@given(raw=_any_text)
def test_tenant_output_is_none_or_a_bounded_clean_token(raw: str) -> None:
    out = sanitize_tenant(raw)
    assert out is None or (
        0 < len(out) <= MAX_TENANT_ID_LENGTH and _ALLOWED_CHAR_PATTERN.search(out) is None
    )


@given(raw=_any_text)
def test_correlation_output_is_none_or_a_bounded_clean_token(raw: str) -> None:
    out = sanitize_inbound_correlation_id(raw)
    assert out is None or (
        0 < len(out) <= MAX_CORRELATION_ID_LENGTH and _ALLOWED_CHAR_PATTERN.search(out) is None
    )


@given(raw=_any_text)
def test_wrappers_are_idempotent(raw: str) -> None:
    tenant = sanitize_tenant(raw)
    assert sanitize_tenant(tenant) == tenant
    correlation = sanitize_inbound_correlation_id(raw)
    assert sanitize_inbound_correlation_id(correlation) == correlation


def test_none_in_none_out() -> None:
    assert sanitize_tenant(None) is None
    assert sanitize_inbound_correlation_id(None) is None
