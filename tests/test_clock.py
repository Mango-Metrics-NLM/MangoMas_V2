"""Tests for the deterministic clock seam (mangomas.utils.clock).

Guards:
1. ``now()`` returns a timezone-aware UTC datetime.
2. The seam is patchable — monkeypatching ``mangomas.utils.clock.now`` in a
   consumer freezes time as expected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from mangomas.utils import clock


def test_now_returns_aware_utc_datetime() -> None:
    """The clock must return a timezone-aware datetime in UTC."""
    result = clock.now()
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result.tzinfo == UTC


def test_clock_is_patchable_through_module() -> None:
    """Monkeypatching the clock seam freezes time for consumers."""
    frozen = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    with patch.object(clock, "now", return_value=frozen):
        assert clock.now() == frozen
