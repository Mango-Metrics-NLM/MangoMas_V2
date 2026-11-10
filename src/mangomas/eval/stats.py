"""Closed-form interval for a pass rate (spec-0045 B6) — informational only.

On eight to fifty rows a pass rate is a coarse instrument; printing it bare
invites a reader to treat 7/8 and 8/8 as a regression. The Wilson score
interval says how coarse without a knob, a seed or a bootstrap. It is
*reported* by the console sink and never gated on: the only honest regression
signal at this sample size is ``diff_reports(...).regressed_rows``.
"""

from __future__ import annotations

import math

# Two-sided 95 % normal quantile. Fixed on purpose: a configurable confidence
# level would invite tuning the interval until a drop looks "within noise".
Z_95 = 1.959963984540054


def wilson_interval(successes: int, trials: int, *, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for ``successes / trials``, clamped to ``[0, 1]``.

    ``trials == 0`` yields the whole unit interval: no observations, no claim.
    """
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError(f"need 0 <= successes <= trials; got {successes}/{trials}")
    if trials == 0:
        return 0.0, 1.0
    p_hat = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p_hat + z * z / (2 * trials)) / denominator
    half_width = (z / denominator) * math.sqrt(
        p_hat * (1 - p_hat) / trials + z * z / (4 * trials * trials)
    )
    low = 0.0 if successes == 0 else max(0.0, centre - half_width)
    high = 1.0 if successes == trials else min(1.0, centre + half_width)
    return low, high
