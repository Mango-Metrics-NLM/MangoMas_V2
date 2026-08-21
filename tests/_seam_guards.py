"""Shared guards for test seams that fail silently when they break.

A "seam" here is a function a test replaces with `monkeypatch.setattr` so the
unit suite never touches the real world. The dangerous failure is not the patch
raising — it is the patch quietly missing its target while the test keeps
passing, because it asserts something the real system also produces.

A plain helper rather than a fixture on purpose: a fixture would have to be
requested as an unused parameter in every consumer, which is both what pytest
wants and what `ARG001` forbids. An explicit call reads better and shows up in
the fixture body where it takes effect.
"""

from __future__ import annotations

import sys

import pytest


def forbid_real_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a CLI unit test builds a *real* orchestrator.

    The CLI suites replace `_build()` with a fake via `monkeypatch.setattr` on
    the module that defines it. If that patch stops reaching its consumer, the
    command silently falls through to the real `build_orchestrator()` — and the
    tests keep passing. Measured on this suite: **13 of 15 patch sites fail
    exactly that way**. `test_agents_command` asserts only `"chat" in stdout`,
    and the real registry does contain `chat`; the two `*_exits_when_rag_disabled`
    tests assert exit 2 and "RAG is not enabled", which the real build also
    emits because RAG is off by default.

    A dead seam is not merely undetected, it is actively harmful: the affected
    tests open real `httpx` connections to the configured LLM endpoint and
    create a real SQLite file, turning a unit suite into a slow, flaky,
    network-dependent one — attributed to anything but the change that caused
    it.

    This converts that whole class from silent to loud in one place, rather
    than bolting an `assert stub_was_called` onto each of the 13 tests. It also
    covers tests added later, which per-test assertions would not.

    Resolved through `_build.__module__` rather than a hard-coded path, so it
    follows the function when spec-0015 moves it from `cli/main.py` into
    `cli/_runtime.py` — that split needs no edit here.
    """
    # Imported lazily, not at module scope: `mangomas.cli.main` pulls in the
    # four eval-registry side-effect imports, and hoisting it would pay that
    # cost — and take that coupling — in every module that imports this helper.
    from mangomas.cli import main as cli_main  # noqa: PLC0415

    home = sys.modules[cli_main._build.__module__]

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "build_orchestrator() was called from a CLI unit test. The `_build` "
            "seam patch did not reach its consumer — most likely it targets a "
            f"different module than {home.__name__!r}, where `_build` now lives. "
            "Left unfixed this test would pass while exercising the real "
            "orchestrator against a live LLM endpoint."
        )

    monkeypatch.setattr(home, "build_orchestrator", _explode)
