"""Exercise fan-out, acceptance-loop, and streaming dispatch against LM Studio.

Run with LM Studio listening on the host/port configured via
``MANGOMAS_LLM__BASE_URL``::

    MANGOMAS_LLM__MODEL='google/gemma-4-e4b' \
        python scripts/run_topologies_e2e.py

Note on SummarizeAgent
----------------------
``SummarizeAgent`` is designed to enrich the user prompt with up to ten prior
turns pulled from ``ctx.repo`` (see ``src/mangomas/agents/summarize.py``). If
``mangomas.db`` is non-empty from previous runs, those turns will appear in
the prompt and bias the summary toward stale topics. To get a clean fan-out
demo, point ``MANGOMAS_DB__URL`` at a throwaway database, e.g.::

    MANGOMAS_DB__URL='sqlite:///./mangomas-demo.db' \
        python scripts/run_topologies_e2e.py
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from mangomas.composition import build_orchestrator
from mangomas.core import Orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import MaxStepsExceeded

# ── Demo configuration constants ─────────────────────────────────────────────
# Everything below this block is structure; everything above can be changed
# without touching the topology drivers.

_HR_WIDTH: int = 78
_PREVIEW_CHARS: int = 400
_LOOP_MAX_STEPS: int = 4
_ACCEPTANCE_SENTINEL: str = "DONE"
_FAN_OUT_AGENTS: tuple[str, ...] = ("summarize", "reviewer")
_LOOP_AGENT: str = "chat"
_STREAM_AGENT: str = "chat"

_FAN_OUT_ARTICLE: str = (
    "Large language models predict the next token given prior context. "
    "They are pretrained on massive text corpora and then fine-tuned for "
    "specific tasks. Recent advances rely on transformer architectures, "
    "long-context attention variants, and reinforcement learning from "
    "human feedback to align outputs with user intent."
)

_HAIKU_REFINEMENT_PROMPT: str = (
    "Refine this haiku one revision at a time. Reply with EXACTLY the new "
    "haiku on three lines, then on a final line write the single word "
    "{sentinel} once you believe the haiku is finished. Do not write "
    "{sentinel} before the final revision.\n\n"
    "Initial haiku:\n"
    "Old pond\n"
    "A frog jumps in\n"
    "Splash sound"
)

_BIAS_VARIANCE_PROMPT: str = (
    "Explain the difference between bias and variance in machine learning. "
    "Keep it under 100 words."
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


def build_acceptance_fn(
    sentinel: str = _ACCEPTANCE_SENTINEL,
) -> Callable[[AgentResponse], bool]:
    """Return an acceptance predicate that matches *sentinel* case-insensitively.

    Exposed as a builder so tests can verify the predicate without spinning up
    an orchestrator, and so callers can swap the sentinel without touching
    topology code.
    """
    sentinel_upper = sentinel.upper()

    def accept(resp: AgentResponse) -> bool:
        return sentinel_upper in resp.content.upper()

    return accept


# ── Topology drivers ─────────────────────────────────────────────────────────


async def run_fan_out(orch: Orchestrator) -> None:
    """Send one prompt to the fan-out agents in parallel; compare against serial."""
    _hr(f"Topology 1/3 — fan-out ({' ∥ '.join(_FAN_OUT_AGENTS)})")
    request = AgentRequest(messages=[Message(role="user", content=_FAN_OUT_ARTICLE)])

    t0 = time.perf_counter()
    parallel = await orch.dispatch_fan_out(list(_FAN_OUT_AGENTS), request)
    parallel_secs = time.perf_counter() - t0

    t0 = time.perf_counter()
    for name in _FAN_OUT_AGENTS:
        await orch.dispatch(name, request)
    serial_secs = time.perf_counter() - t0

    for resp in parallel:
        print(f"--- {resp.agent} ({len(resp.content)} chars) ---")
        print(_preview(resp.content))
        print()

    speedup = serial_secs / parallel_secs if parallel_secs > 0 else float("inf")
    print(
        f"[timing] parallel={parallel_secs:.2f}s  serial={serial_secs:.2f}s  "
        f"speedup={speedup:.2f}x"
    )


async def run_acceptance_loop(orch: Orchestrator) -> None:
    """Iterate the loop agent until its response contains the sentinel."""
    _hr(f"Topology 2/3 — acceptance-loop ({_LOOP_AGENT} until '{_ACCEPTANCE_SENTINEL}')")

    prompt = _HAIKU_REFINEMENT_PROMPT.format(sentinel=_ACCEPTANCE_SENTINEL)
    request = AgentRequest(messages=[Message(role="user", content=prompt)])

    t0 = time.perf_counter()
    try:
        resp = await orch.dispatch(
            _LOOP_AGENT,
            request,
            acceptance_fn=build_acceptance_fn(),
            max_steps=_LOOP_MAX_STEPS,
        )
    except MaxStepsExceeded as exc:
        elapsed = time.perf_counter() - t0
        print(
            f"[loop] MaxStepsExceeded after {exc.steps} steps ({elapsed:.2f}s) — "
            "model never emitted the sentinel."
        )
        return

    elapsed = time.perf_counter() - t0
    loop_meta = resp.metadata.get("loop", {})
    steps = loop_meta.get("steps_taken")
    accepted = loop_meta.get("accepted")
    print(resp.content)
    print()
    print(f"[loop] steps_taken={steps} accepted={accepted} elapsed={elapsed:.2f}s")


async def run_streaming(orch: Orchestrator) -> None:
    """Stream the chat agent's response token-by-token; measure TTFT and throughput."""
    _hr(f"Topology 3/3 — streaming ({_STREAM_AGENT} token-by-token)")

    request = AgentRequest(
        messages=[Message(role="user", content=_BIAS_VARIANCE_PROMPT)]
    )
    stream = await orch.stream_dispatch(_STREAM_AGENT, request)

    t0 = time.perf_counter()
    ttft: float | None = None
    chunks = 0
    total_chars = 0
    async for chunk in stream:
        if ttft is None:
            ttft = time.perf_counter() - t0
        chunks += 1
        total_chars += len(chunk)
        print(chunk, end="", flush=True)

    elapsed = time.perf_counter() - t0
    ttft_display = f"{ttft:.3f}s" if ttft is not None else "n/a"
    throughput = total_chars / max(elapsed, 1e-6)
    print()
    print(
        f"[stream] chunks={chunks} chars={total_chars} ttft={ttft_display} "
        f"total={elapsed:.2f}s throughput={throughput:.0f} chars/s"
    )


async def _main() -> int:
    orch = build_orchestrator()
    try:
        await run_fan_out(orch)
        await run_acceptance_loop(orch)
        await run_streaming(orch)
        return 0
    finally:
        await orch.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
