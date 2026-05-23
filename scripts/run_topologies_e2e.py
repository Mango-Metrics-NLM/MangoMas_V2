"""Exercise fan-out, acceptance-loop, and streaming dispatch against LM Studio.

Run with LM Studio listening on localhost:1234::

    MANGOMAS_LLM__MODEL='google/gemma-4-e4b' \
        python scripts/run_topologies_e2e.py

Note on SummarizeAgent
----------------------
``SummarizeAgent`` is designed to enrich the user prompt with up to ten prior
turns pulled from ``ctx.repo`` (see ``src/mangomas/agents/summarize.py``). If
``mangomas.db`` is non-empty from previous runs, those turns will appear in
the prompt and bias the summary toward stale topics. To get a clean
fan-out demo, point ``MANGOMAS_DB__URL`` at a throwaway database, e.g.::

    MANGOMAS_DB__URL='sqlite:///./mangomas-demo.db' \
        python scripts/run_topologies_e2e.py
"""

from __future__ import annotations

import asyncio
import time

from mangomas.composition import build_orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.errors import MaxStepsExceeded


def _hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


async def _fan_out(orch: object) -> None:
    """Send one prompt to summarize + reviewer in parallel; compare against serial."""
    _hr("Topology 1/3 — fan-out (summarize ∥ reviewer)")

    article = (
        "Large language models predict the next token given prior context. "
        "They are pretrained on massive text corpora and then fine-tuned for "
        "specific tasks. Recent advances rely on transformer architectures, "
        "long-context attention variants, and reinforcement learning from "
        "human feedback to align outputs with user intent."
    )
    request = AgentRequest(messages=[Message(role="user", content=article)])

    # Parallel
    t0 = time.perf_counter()
    parallel = await orch.dispatch_fan_out(["summarize", "reviewer"], request)  # type: ignore[attr-defined]
    parallel_secs = time.perf_counter() - t0

    # Serial (for comparison)
    t0 = time.perf_counter()
    serial = [
        await orch.dispatch("summarize", request),  # type: ignore[attr-defined]
        await orch.dispatch("reviewer", request),  # type: ignore[attr-defined]
    ]
    serial_secs = time.perf_counter() - t0

    preview_chars = 400
    for resp in parallel:
        print(f"--- {resp.agent} ({len(resp.content)} chars) ---")
        truncated = len(resp.content) > preview_chars
        print(resp.content[:preview_chars] + ("..." if truncated else ""))
        print()

    print(f"[timing] parallel={parallel_secs:.2f}s  serial={serial_secs:.2f}s  "
          f"speedup={serial_secs / parallel_secs:.2f}x")
    _ = serial  # keep the result for sanity but do not re-print


async def _acceptance_loop(orch: object) -> None:
    """Iterate the chat agent until its response contains the sentinel 'DONE'."""
    _hr("Topology 2/3 — acceptance-loop (chat until 'DONE')")

    prompt = (
        "Refine this haiku one revision at a time. Reply with EXACTLY the new "
        "haiku on three lines, then on a final line write the single word DONE "
        "once you believe the haiku is finished. Do not write DONE before the "
        "final revision.\n\n"
        "Initial haiku:\n"
        "Old pond\n"
        "A frog jumps in\n"
        "Splash sound"
    )

    def accept(resp: AgentResponse) -> bool:
        return "DONE" in resp.content.upper()

    request = AgentRequest(messages=[Message(role="user", content=prompt)])
    t0 = time.perf_counter()
    try:
        resp = await orch.dispatch(  # type: ignore[attr-defined]
            "chat",
            request,
            acceptance_fn=accept,
            max_steps=4,
        )
        elapsed = time.perf_counter() - t0
        steps = resp.metadata.get("loop", {}).get("steps_taken")
        accepted = resp.metadata.get("loop", {}).get("accepted")
        print(resp.content)
        print()
        print(f"[loop] steps_taken={steps} accepted={accepted} elapsed={elapsed:.2f}s")
    except MaxStepsExceeded as exc:
        elapsed = time.perf_counter() - t0
        print(f"[loop] MaxStepsExceeded after {exc.steps} steps ({elapsed:.2f}s) — "
              "model never emitted the sentinel.")


async def _streaming(orch: object) -> None:
    """Stream the chat agent's response token-by-token; measure TTFT and total."""
    _hr("Topology 3/3 — streaming (chat token-by-token)")

    request = AgentRequest(messages=[Message(
        role="user",
        content=(
            "Explain the difference between bias and variance in machine "
            "learning. Keep it under 100 words."
        ),
    )])

    stream = await orch.stream_dispatch("chat", request)  # type: ignore[attr-defined]
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
    print()
    print(f"[stream] chunks={chunks} chars={total_chars} "
          f"ttft={ttft:.3f}s total={elapsed:.2f}s "
          f"throughput={total_chars / max(elapsed, 1e-6):.0f} chars/s")


async def _close(orch: object) -> None:
    ctx = orch.context  # type: ignore[attr-defined]
    if hasattr(ctx.llm, "aclose"):
        await ctx.llm.aclose()
    if ctx.repo is not None:
        if hasattr(ctx.repo, "aclose"):
            await ctx.repo.aclose()
        else:
            ctx.repo.close()


async def _run() -> int:
    orch = build_orchestrator()
    try:
        await _fan_out(orch)
        await _acceptance_loop(orch)
        await _streaming(orch)
        return 0
    finally:
        await _close(orch)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
