"""Exercise a declarative workflow graph against LM Studio.

Run with LM Studio listening on the host/port configured via
``MANGOMAS_LLM__BASE_URL``::

    MANGOMAS_LLM__MODEL='google/gemma-4-e4b' \
        python scripts/run_workflow_e2e.py

The graph below composes a ``sequence`` of a single agent, a parallel
``fan_out`` (joined by ``concat``), and an acceptance ``loop`` — proving the
declarative layer drives the imperative dispatch primitives from a JSON
definition. Point ``MANGOMAS_DB__URL`` at a throwaway database for a clean run.
"""

from __future__ import annotations

import asyncio
import json
import time

import _stdio

from mangomas.cli.exit_codes import EXIT_RUNTIME_ERROR
from mangomas.composition import build_orchestrator
from mangomas.core.agent import AgentRequest, Message
from mangomas.errors import MaxStepsExceeded
from mangomas.workflow import execute_workflow, load_workflow

# ── Demo configuration constants ─────────────────────────────────────────────

_HR_WIDTH: int = 78
_PREVIEW_CHARS: int = 400
_LOOP_SENTINEL: str = "DONE"
_LOOP_MAX_STEPS: int = 4

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
                "agent": "chat",
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


def _hr(title: str) -> None:
    print()
    print("=" * _HR_WIDTH)
    print(title)
    print("=" * _HR_WIDTH)


def _preview(text: str, limit: int = _PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


async def _main() -> int:
    _stdio.configure_utf8_stdio()
    _hr("Declarative workflow — sequence(agent → fan_out ∥ → loop)")
    graph = load_workflow(json.dumps(_GRAPH))
    print(f"graph: {graph.name}  root={graph.root.kind}")

    orch = build_orchestrator()
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
        # A loop that never converged is a failed run, not a tolerated one: a
        # zero exit here let a CI step calling this script pass with the
        # sentinel never emitted. Same code the CLI uses for a runtime failure.
        print(f"[loop] MaxStepsExceeded after {exc.steps} steps — sentinel never emitted.")
        return EXIT_RUNTIME_ERROR
    finally:
        # Always release the LLM httpx pool — even when execute_workflow raises
        # something other than MaxStepsExceeded (e.g. an adapter/network error).
        await orch.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
