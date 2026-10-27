"""Tests for ``mangomas.utils.exc.truncated_exc_detail`` — the one spelling of
the bounded ``"<TypeName>: <message>"`` detail every typed-error boundary uses.

Mutation (proved while writing): dropping the slice makes
``test_detail_is_bounded_by_the_default_limit`` fail.
"""

from __future__ import annotations

import pytest

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.utils.exc import truncated_exc_detail


def test_detail_carries_type_name_and_message() -> None:
    assert truncated_exc_detail(ValueError("bad value")) == "ValueError: bad value"


def test_detail_is_bounded_by_the_default_limit() -> None:
    long = RuntimeError("x" * (DEFAULT_ERROR_DETAIL_TRUNCATE * 3))
    detail = truncated_exc_detail(long)
    assert len(detail) == DEFAULT_ERROR_DETAIL_TRUNCATE
    assert detail.startswith("RuntimeError: ")


@pytest.mark.parametrize("limit", [1, 8, 64])
def test_explicit_limit_is_honoured(limit: int) -> None:
    full = "OSError: disk full and then some more text"
    detail = truncated_exc_detail(OSError("disk full and then some more text"), limit=limit)
    assert len(detail) == min(limit, len(full))
    assert detail == full[:limit]


def test_empty_message_still_names_the_type() -> None:
    assert truncated_exc_detail(KeyError()) == "KeyError: "
