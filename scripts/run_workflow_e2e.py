"""Exercise a declarative workflow graph against LM Studio.

Run with LM Studio listening on the host/port configured via
``MANGOMAS_LLM__BASE_URL``::

    MANGOMAS_LLM__MODEL='google/gemma-4-e4b' \
        python scripts/run_workflow_e2e.py

The graph below composes a ``sequence`` of a single agent, a parallel
``fan_out`` (joined by ``concat``), and an acceptance ``loop`` — proving the
declarative layer drives the imperative dispatch primitives from a JSON
definition. Point ``MANGOMAS_DB__URL`` at a throwaway database for a clean run.

A ``sequence`` hands each step only the previous step's output, so the loop
agent never sees the user's prompt. The sentinel instruction therefore travels
in the loop agent's system prompt (``_with_loop_instruction``) unless the
operator already set one via ``MANGOMAS_AGENTS__CHAT__SYSTEM_PROMPT``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Iterable

from mangomas.composition import build_orchestrator
from mangomas.config import AgentSettings, Settings, get_settings
from mangomas.core.agent import AgentRequest, Message
from mangomas.errors import MaxStepsExceeded
from mangomas.workflow import execute_workflow, load_workflow

logger = logging.getLogger(__name__)

# ── Demo configuration constants ─────────────────────────────────────────────

_HR_WIDTH: int = 78
_PREVIEW_CHARS: int = 400
_LOOP_SENTINEL: str = "DONE"
_LOOP_MAX_STEPS: int = 4
_LOOP_AGENT: str = "chat"
_LOOP_SYSTEM_PROMPT: str = (
    "You refine a definition. Reply with the final one-sentence definition, "
    f"then end your reply with {_LOOP_SENTINEL}."
)

# Mirrors the CLI's process-level policy (``mangomas.cli._runtime``): Windows
# falls back to the cp1252 codec whenever stdout is redirected (a pipe, a log
# file, CI), and cp1252 cannot encode the arrows in the banner below or the
# em-dashes / smart quotes LLM replies routinely contain.
_WINDOWS_PLATFORM: str = "win32"
_STDIO_ENCODING: str = "utf-8"
_STDIO_ERRORS: str = "replace"

_GRAPH: dict[str, object] = {
    "schema_version": 1,
    "name": "plan-review-refine",
    "root": {
        "kind": "sequence",
        "steps": [
            {"kind": "agent", "agent": "summarize"},
            {
                "kind": "fan_out",
                "join": "concat",
                "branches": [
                    {"kind": "agent", "agent": "reviewer"},
                    {"kind": "agent", "agent": "chat"},
                ],
            },
            {
                "kind": "loop",
                "agent": _LOOP_AGENT,
                "max_steps": _LOOP_MAX_STEPS,
                "accept": {"kind": "contains", "value": _LOOP_SENTINEL, "case_sensitive": False},
            },
        ],
    },
}

_PROMPT: str = (
    "Summarise, then refine, a one-sentence definition of 'bias-variance "
    f"tradeoff'. When the definition is final, end your reply with {_LOOP_SENTINEL}."
)


# ── Pretty output helpers ────────────────────────────────────────────────────


def _ensure_utf8_stdio(
    streams: Iterable[object] | None = None, *, platform: str | None = None
) -> None:
    """Reconfigure *streams* (default: stdout + stderr) to UTF-8 on Windows.

    Without this the first ``print`` of a non-cp1252 character raised
    ``UnicodeEncodeError`` before the workflow ran. Streams lacking
    ``reconfigure`` (already-wrapped or captured ones) are left untouched;
    other platforms already default to UTF-8 and are not modified.
    """
    if (platform if platform is not None else sys.platform) != _WINDOWS_PLATFORM:
        return
    for stream in streams if streams is not None else (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding=_STDIO_ENCODING, errors=_STDIO_ERRORS)


def _hr(title: str) -> None:
    print()
    print("=" * _HR_WIDTH)
    print(title)
    print("=" * _HR_WIDTH)


def _preview(text: str, limit: int = _PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _with_loop_instruction(settings: Settings) -> Settings:
    """Return *settings* with the sentinel instruction on the loop agent.

    A ``sequence`` replaces the request with the previous step's output, so an
    instruction in the user prompt never reaches the loop agent; without this
    the loop could only end in ``MaxStepsExceeded``. An operator-set system
    prompt for the loop agent wins and is returned unchanged. Every other
    per-agent field and entry is preserved.
    """
    current = settings.agents.get(_LOOP_AGENT)
    if current is not None and current.system_prompt is not None:
        logger.info(
            "Keeping the operator-set system prompt for the loop agent",
            extra={"agent": _LOOP_AGENT, "sentinel": _LOOP_SENTINEL},
        )
        return settings
    agent = (current if current is not None else AgentSettings()).model_copy(
        update={"system_prompt": _LOOP_SYSTEM_PROMPT}
    )
    return settings.model_copy(update={"agents": {**settings.agents, _LOOP_AGENT: agent}})


async def _main() -> int:
    _ensure_utf8_stdio()
    _hr("Declarative workflow — sequence(agent → fan_out ∥ → loop)")
    graph = load_workflow(json.dumps(_GRAPH))
    print(f"graph: {graph.name}  root={graph.root.kind}")

    orch = build_orchestrator(_with_loop_instruction(get_settings()))
    request = AgentRequest(messages=[Message(role="user", content=_PROMPT)])

    t0 = time.perf_counter()
    try:
        response = await execute_workflow(graph, request, orch=orch)
        elapsed = time.perf_counter() - t0

        print(f"--- final ({response.agent}, {len(response.content)} chars) ---")
        print(_preview(response.content))
        loop_meta = response.metadata.get("loop", {})
        print(f"\n[workflow] elapsed={elapsed:.2f}s loop={loop_meta}")
        return 0
    except MaxStepsExceeded as exc:
        print(f"[loop] MaxStepsExceeded after {exc.steps} steps — sentinel never emitted.")
        return 0
    finally:
        # Always release the LLM httpx pool — even when execute_workflow raises
        # something other than MaxStepsExceeded (e.g. an adapter/network error).
        await orch.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
