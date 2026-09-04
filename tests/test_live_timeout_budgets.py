"""Contract tests for the live-suite timeout budget helpers (spec-0029 R2.1).

``resolve_live_timeout`` and ``client_timeout_for`` are what make the live
suites hardware-agnostic: the adapter budget comes from the environment, and
the client budget is *derived* from it so the 60s-client-around-a-240s-adapter
inversion cannot be re-expressed. They shipped with no direct tests — the live
suites that consume them are all env-gated and never run in CI, so a defect
here would be invisible until someone ran LM Studio by hand.

The validation these pin was added after review: the derivation's promise
("always strictly greater") is *false* for a non-finite budget, because
``nan + 30 > nan`` and ``inf + 30 > inf`` are both ``False``. A claim that
reads true and is not is exactly what this repo's guards exist to stop.
"""

from __future__ import annotations

import pytest

from tests.constants import (
    DEFAULT_LIVE_E2E_TIMEOUT_SECONDS,
    LIVE_CLIENT_TIMEOUT_HEADROOM_SECONDS,
    LMSTUDIO_E2E_TIMEOUT_ENV,
    client_timeout_for,
    resolve_live_timeout,
)

_A_SLOW_BOX_BUDGET = 600.0


# ── resolve_live_timeout ──────────────────────────────────────────────────────


def test_unset_env_falls_back_to_the_cpu_sized_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LMSTUDIO_E2E_TIMEOUT_ENV, raising=False)
    assert resolve_live_timeout(LMSTUDIO_E2E_TIMEOUT_ENV) == DEFAULT_LIVE_E2E_TIMEOUT_SECONDS


def test_blank_env_falls_back_rather_than_erroring(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported-but-empty variable is "unset" — the common shell accident."""
    monkeypatch.setenv(LMSTUDIO_E2E_TIMEOUT_ENV, "   ")
    assert resolve_live_timeout(LMSTUDIO_E2E_TIMEOUT_ENV) == DEFAULT_LIVE_E2E_TIMEOUT_SECONDS


def test_a_set_budget_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the knob: a slow box can raise the budget."""
    monkeypatch.setenv(LMSTUDIO_E2E_TIMEOUT_ENV, str(_A_SLOW_BOX_BUDGET))
    assert resolve_live_timeout(LMSTUDIO_E2E_TIMEOUT_ENV) == _A_SLOW_BOX_BUDGET


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("abc", id="not-a-number"),
        pytest.param("nan", id="nan"),
        pytest.param("inf", id="inf"),
        pytest.param("-5", id="negative"),
        pytest.param("0", id="zero"),
    ],
)
def test_a_bad_budget_names_the_env_var(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """The error must say *which* variable to fix.

    A bare ``float("abc")`` raises "could not convert string to float: 'abc'",
    which names neither the variable nor the fix — and this knob is reached for
    precisely when a suite is already misbehaving, so an unhelpful error lands
    at the worst moment.
    """
    monkeypatch.setenv(LMSTUDIO_E2E_TIMEOUT_ENV, raw)
    with pytest.raises(ValueError, match=LMSTUDIO_E2E_TIMEOUT_ENV):
        resolve_live_timeout(LMSTUDIO_E2E_TIMEOUT_ENV)


# ── client_timeout_for ────────────────────────────────────────────────────────


def test_the_client_budget_exceeds_the_adapter_budget() -> None:
    """The invariant the whole derivation exists for."""
    derived = client_timeout_for(DEFAULT_LIVE_E2E_TIMEOUT_SECONDS)
    assert derived > DEFAULT_LIVE_E2E_TIMEOUT_SECONDS
    assert derived == DEFAULT_LIVE_E2E_TIMEOUT_SECONDS + LIVE_CLIENT_TIMEOUT_HEADROOM_SECONDS


@pytest.mark.parametrize(
    "budget",
    [pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf")],
)
def test_a_non_finite_budget_is_rejected(budget: float) -> None:
    """These are the inputs that make the invariant silently *false*.

    Asserted alongside the arithmetic that motivates it: for ``nan`` and
    ``inf`` the derived value is genuinely **not** greater than its input, so
    without this guard ``client_timeout_for`` would return a budget violating
    the property its own docstring promises. That is a different failure from
    the non-positive case below, and worth separating: this one breaks a
    stated invariant, that one is merely nonsense.
    """
    assert not (budget + LIVE_CLIENT_TIMEOUT_HEADROOM_SECONDS > budget), (
        "this test is only meaningful for inputs that defeat the comparison"
    )

    with pytest.raises(ValueError, match="finite positive"):
        client_timeout_for(budget)


@pytest.mark.parametrize(
    "budget",
    [pytest.param(-5.0, id="negative"), pytest.param(0.0, id="zero")],
)
def test_a_non_positive_budget_is_rejected(budget: float) -> None:
    """Rejected for a different reason: the arithmetic works, the value doesn't.

    ``-5 + 30 = 25`` really is greater than ``-5``, so the invariant survives —
    but a zero or negative timeout is meaningless to httpx and almost always a
    mis-set env var. Failing loudly beats handing a nonsense budget to the
    transport.
    """
    assert budget + LIVE_CLIENT_TIMEOUT_HEADROOM_SECONDS > budget

    with pytest.raises(ValueError, match="finite positive"):
        client_timeout_for(budget)


def test_a_resolved_budget_feeds_the_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two helpers compose: env → adapter budget → client budget.

    The pair is the contract, and neither half alone shows it holds
    end-to-end — which is how the original inversion survived: each side was
    individually reasonable.
    """
    monkeypatch.setenv(LMSTUDIO_E2E_TIMEOUT_ENV, str(_A_SLOW_BOX_BUDGET))
    adapter = resolve_live_timeout(LMSTUDIO_E2E_TIMEOUT_ENV)
    assert client_timeout_for(adapter) > adapter
