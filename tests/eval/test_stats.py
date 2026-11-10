"""Wilson interval (spec-0045 B6): closed-form, clamped, informational."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mangomas.eval.stats import wilson_interval


def test_no_trials_makes_no_claim() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_known_values() -> None:
    low, high = wilson_interval(7, 8)
    assert 0.5 < low < 0.875 < high <= 1.0
    low_all, high_all = wilson_interval(8, 8)
    assert high_all == 1.0 and 0.6 < low_all < 1.0, "8/8 is not certainty at n=8"


def test_invalid_counts_raise() -> None:
    for successes, trials in ((-1, 5), (6, 5), (0, -1)):
        with pytest.raises(ValueError, match="successes"):
            wilson_interval(successes, trials)


@given(trials=st.integers(min_value=1, max_value=500), data=st.data())
def test_interval_contains_the_point_estimate_and_narrows_with_n(
    trials: int, data: st.DataObject
) -> None:
    successes = data.draw(st.integers(min_value=0, max_value=trials))
    low, high = wilson_interval(successes, trials)
    assert 0.0 <= low <= successes / trials <= high <= 1.0
    low_more, high_more = wilson_interval(successes * 4, trials * 4)
    assert (high_more - low_more) <= (high - low) + 1e-12
