"""Process- and orchestrator-level seam for the CLI.

The base layer: imports nothing else under `mangomas.cli`, so every command
module can depend on it without a cycle.

`_build` and `_close_orchestrator` are the **patch seam** the CLI test suites
replace. Command modules must call them through this module object
(`_runtime._build()`), never via `from ._runtime import _build` — a bound name
would need a separate patch target per command module, and the one that got
missed would fail silently. `tests/_seam_guards.forbid_real_orchestrator`
enforces that a missed patch is loud rather than quiet.

The Windows stdout reconfiguration lives here rather than in the facade because
it is a process-level side effect that must run before any `typer.echo`, and
this module is imported by every command path.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from mangomas.composition import build_orchestrator

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator

# Windows default console codec is cp1252; LLM replies routinely contain
# em-dashes, smart quotes, etc. that cp1252 cannot encode, which crashes
# typer.echo. Reconfigure to UTF-8 with replacement so output never crashes.
if sys.platform == "win32":  # pragma: no cover — platform-gated
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _build() -> Orchestrator:
    """Construct the orchestrator with full settings + adapter wiring."""
    return build_orchestrator()


async def _close_orchestrator(orch: Orchestrator) -> None:
    """Release adapter resources cleanly via :meth:`Orchestrator.aclose`.

    Retained as a thin wrapper so existing tests can monkeypatch the CLI's
    close path without reaching into core. The real teardown logic lives on
    :class:`~mangomas.core.Orchestrator` so every entry point (CLI, FastAPI
    lifespan, demo scripts) shares one tested code path.
    """
    await orch.aclose()
