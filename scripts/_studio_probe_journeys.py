"""The Studio screens, as probe journeys over the real HTTP surface (spec-0041).

Each journey is one Studio screen's contract. Journeys record checks and never
raise on a failed assertion; the runner turns an unexpected exception into a
failed check, so one broken screen never hides the others.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Final

import httpx
from _studio_probe_model import (
    BROKEN_PROFILE,
    DRAFT_PROFILE,
    PROFILE_KEY,
    REVIEW_PROFILE,
    STUDIO_ORIGIN,
    UNKNOWN_PROFILE,
    UNLISTED_ORIGIN,
    Journey,
    JourneyContext,
)

import mangomas.eval.scorers
import mangomas.eval.sinks
import mangomas.eval.targets  # noqa: F401 — the three imports seed the eval registries
from mangomas.core.agent import Message
from mangomas.errors import ConfigError, LLMBadResponse
from mangomas.eval import DatasetRow, EvalRunner, scorer_registry, sink_registry, target_registry

PROMPT: Final[str] = "Reply with one short sentence about mangoes."
INVOKE_PROMPT: Final[str] = "Reply with one short sentence about mango orchards."
STREAM_PROMPT: Final[str] = "Reply with one short sentence about mango season."
HISTORY_SCAN_LIMIT: Final[int] = 20
SECRET_FIELDS: Final[tuple[str, ...]] = ("api_key", "secret_ref", "credentials_path")
LIVE_ROUTING_SKIP_REASON: Final[str] = "live replies do not name the serving model"
EVAL_SCORER: Final[str] = "exact_match"
EVAL_TARGET: Final[str] = "echo"
EVAL_SINK: Final[str] = "json_file"
EVAL_ROWS: Final[tuple[tuple[str, str, str], ...]] = (
    ("r1", "mango", "mango"),
    ("r2", "papaya", "guava"),
)
WORKFLOW_GRAPH: Final[dict[str, Any]] = {
    "schema_version": 1,
    "name": "studio-draft-then-review",
    "root": {
        "kind": "sequence",
        "steps": [
            {
                "kind": "fan_out",
                "join": "concat",
                "branches": [
                    {"kind": "agent", "agent": "chat"},
                    {"kind": "agent", "agent": "summarize"},
                ],
            },
            {"kind": "agent", "agent": "chat"},
        ],
    },
}
_DETAIL_CHARS: Final[int] = 120
_SSE_DATA_PREFIX: Final[str] = "data: "


# ── Helpers ──────────────────────────────────────────────────────────────────


def build_request(content: str, profile: str | None = None) -> dict[str, Any]:
    """An ``AgentRequest`` body with one user turn and an optional profile."""
    metadata = {PROFILE_KEY: profile} if profile is not None else {}
    return {"messages": [{"role": "user", "content": content}], "metadata": metadata}


def expect_routed(ctx: JourneyContext, name: str, content: str, model: str) -> None:
    """Check *content* came from *model*, or skip where no reply can say."""
    if not ctx.cfg.echoes_model:
        ctx.rec.skip(name, LIVE_ROUTING_SKIP_REASON)
        return
    ctx.rec.expect(name, content.startswith(f"[{model}]"), content[:_DETAIL_CHARS])


async def stored_turn(client: httpx.AsyncClient, prompt: str) -> dict[str, Any] | None:
    """The newest stored turn whose last message is *prompt*, if any.

    Binding to the prompt, not to "newest", keeps a check from passing on an
    older turn when the run under test persisted nothing.
    """
    resp = await client.get("/history", params={"limit": HISTORY_SCAN_LIMIT})
    for turn in resp.json().get("turns", []):
        messages = turn.get("request", {}).get("messages", [])
        if messages and messages[-1].get("content") == prompt:
            return dict(turn)
    return None


def turn_metadata(turn: dict[str, Any] | None) -> dict[str, Any]:
    """The stored response metadata of *turn*, or ``{}`` when there is none."""
    return dict(turn["response"].get("metadata", {})) if turn else {}


def sse_events(text: str) -> list[dict[str, Any]]:
    """Every ``data:`` frame of an SSE body, decoded."""
    return [
        json.loads(line[len(_SSE_DATA_PREFIX) :])
        for line in text.splitlines()
        if line.startswith(_SSE_DATA_PREFIX)
    ]


def _status(resp: httpx.Response) -> str:
    return str(resp.status_code)


# ── Journeys ─────────────────────────────────────────────────────────────────


async def journey_profile_picker(ctx: JourneyContext) -> None:
    """The model picker lists the roster and never a credential."""
    resp = await ctx.client.get("/llm/profiles")
    names = sorted(p["name"] for p in resp.json().get("profiles", []))
    ctx.rec.expect("status 200", resp.status_code == httpx.codes.OK, _status(resp))
    expected = sorted([BROKEN_PROFILE, DRAFT_PROFILE, REVIEW_PROFILE])
    ctx.rec.expect("roster", names == expected, str(names))
    leaked = [f for f in SECRET_FIELDS if f in resp.text]
    ctx.rec.expect("no secret fields", not leaked, str(leaked))


async def journey_invoke_stamp(ctx: JourneyContext) -> None:
    """An invoke answers from the chosen profile and the stored turn says so."""
    resp = await ctx.client.post(
        "/agents/chat/invoke", json=build_request(INVOKE_PROMPT, REVIEW_PROFILE)
    )
    body = resp.json()
    ctx.rec.expect("status 200", resp.status_code == httpx.codes.OK, _status(resp))
    stamp = body.get("metadata", {}).get(PROFILE_KEY)
    ctx.rec.expect("response stamp", stamp == REVIEW_PROFILE, str(stamp))
    content = str(body.get("content", ""))
    expect_routed(ctx, "served by the review model", content, ctx.cfg.review_model)
    stored = turn_metadata(await stored_turn(ctx.client, INVOKE_PROMPT)).get(PROFILE_KEY)
    ctx.rec.expect("history stamp", stored == REVIEW_PROFILE, str(stored))


async def journey_routing_oracle(ctx: JourneyContext) -> None:
    """The broken profile fails while the default client still answers."""
    broken = await ctx.client.post(
        "/agents/chat/invoke", json=build_request(PROMPT, BROKEN_PROFILE)
    )
    code = broken.json().get("error")
    ctx.rec.expect(
        f"broken profile is 502 {LLMBadResponse.code}",
        broken.status_code == httpx.codes.BAD_GATEWAY and code == LLMBadResponse.code,
        f"{broken.status_code} {code}",
    )
    default = await ctx.client.post("/agents/chat/invoke", json=build_request(PROMPT))
    body = default.json()
    ctx.rec.expect("default answers", default.status_code == httpx.codes.OK, _status(default))
    ctx.rec.expect("default is unstamped", PROFILE_KEY not in body.get("metadata", {}))
    content = str(body.get("content", ""))
    expect_routed(ctx, "served by the base model", content, ctx.cfg.draft_model)


async def journey_run_timeline(ctx: JourneyContext) -> None:
    """The stream names its profile up front; the stored turn does not (D-12)."""
    resp = await ctx.client.post(
        "/agents/chat/stream", json=build_request(STREAM_PROMPT, REVIEW_PROFILE)
    )
    events = sse_events(resp.text)
    kinds = [e.get("event") for e in events]
    ctx.rec.expect("status 200", resp.status_code == httpx.codes.OK, _status(resp))
    frames = [e.get("data", {}) for e in events if e.get("event") == "metadata"]
    ctx.rec.expect(
        "one metadata frame names the profile",
        len(frames) == 1 and frames[0].get(PROFILE_KEY) == REVIEW_PROFILE,
        json.dumps(frames)[:_DETAIL_CHARS],
    )
    ctx.rec.expect("tokens then done", "token" in kinds and kinds[-1] == "done", str(kinds[-3:]))
    tokens = (
        str(e.get("data", {}).get("content", "")) for e in events if e.get("event") == "token"
    )
    expect_routed(ctx, "tokens served by the review model", "".join(tokens), ctx.cfg.review_model)
    turn = await stored_turn(ctx.client, STREAM_PROMPT)
    ctx.rec.expect("streamed turn persisted", turn is not None)
    stored = turn_metadata(turn)
    ctx.rec.expect(
        "D-12 limitation: streamed turn is unstamped (update spec-0041 if this fails)",
        turn is not None and PROFILE_KEY not in stored,
        json.dumps(stored)[:_DETAIL_CHARS],
    )


async def journey_unknown_profile(ctx: JourneyContext) -> None:
    """An unknown profile is a JSON 400 before any SSE header is sent."""
    resp = await ctx.client.post("/agents/chat/stream", json=build_request(PROMPT, UNKNOWN_PROFILE))
    content_type = resp.headers.get("content-type", "")
    is_json = content_type.startswith("application/json")
    ctx.rec.expect("status 400", resp.status_code == httpx.codes.BAD_REQUEST, _status(resp))
    ctx.rec.expect("JSON, not SSE", is_json, content_type)
    code = resp.json().get("error") if is_json else None
    ctx.rec.expect(f"{ConfigError.code} envelope", code == ConfigError.code, str(code))


async def journey_workflow(ctx: JourneyContext) -> None:
    """The template validates, then runs with the profile carried to the end."""
    definition = json.dumps(WORKFLOW_GRAPH)
    check = await ctx.client.post("/workflows/validate", json={"definition": definition})
    root = check.json().get("root_kind")
    expected_root = WORKFLOW_GRAPH["root"]["kind"]
    ctx.rec.expect(
        "validate ok", check.status_code == httpx.codes.OK and root == expected_root, str(root)
    )
    run = await ctx.client.post(
        "/workflows/run",
        json={"definition": definition, "request": build_request(PROMPT, REVIEW_PROFILE)},
    )
    body = run.json()
    ctx.rec.expect("run 200", run.status_code == httpx.codes.OK, _status(run))
    stamp = body.get("metadata", {}).get(PROFILE_KEY)
    ctx.rec.expect("profile survives concat fan_out", stamp == REVIEW_PROFILE, str(stamp))
    content = str(body.get("content", ""))
    expect_routed(ctx, "final stage served by the review model", content, ctx.cfg.review_model)


async def journey_eval_report(ctx: JourneyContext) -> None:
    """The report viewer reads a ``json_file`` report with per-row verdicts."""
    rows = [
        DatasetRow(id=row_id, messages=[Message(role="user", content=prompt)], expected=expected)
        for row_id, prompt, expected in EVAL_ROWS
    ]
    expected_passes = sum(prompt == expected for _, prompt, expected in EVAL_ROWS)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.json"
        runner = EvalRunner(ctx.orch, scorer_registry.get(EVAL_SCORER)({}))
        report = await runner.run(rows, target=target_registry.get(EVAL_TARGET)({}))
        await sink_registry.get(EVAL_SINK)({"path": str(path)}).emit(report)
        payload = json.loads(path.read_text(encoding="utf-8"))
    ctx.rec.expect("report written", isinstance(payload, dict), type(payload).__name__)
    row_count = len(payload.get("rows", []))
    ctx.rec.expect("row count", row_count == len(EVAL_ROWS), str(row_count))
    passed = payload.get("passed")
    ctx.rec.expect("echo passes only matching rows", passed == expected_passes, str(passed))


async def _preflight(client: httpx.AsyncClient, origin: str) -> httpx.Response:
    return await client.options(
        "/agents/chat/invoke",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


async def journey_cors(ctx: JourneyContext) -> None:
    """The browser plane: the Studio origin is allowed, any other is refused.

    Starlette echoes an allowed origin even on a preflight it refuses for the
    requested method, so "allowed" needs the 200 as well as the header.
    """
    listed = await _preflight(ctx.client, STUDIO_ORIGIN)
    allowed = listed.headers.get("access-control-allow-origin")
    ctx.rec.expect(
        "listed origin allowed",
        listed.status_code == httpx.codes.OK and allowed == STUDIO_ORIGIN,
        f"{listed.status_code} {allowed}",
    )
    unlisted = await _preflight(ctx.client, UNLISTED_ORIGIN)
    echoed = unlisted.headers.get("access-control-allow-origin")
    ctx.rec.expect(
        "unlisted origin refused",
        unlisted.status_code == httpx.codes.BAD_REQUEST and echoed is None,
        f"{unlisted.status_code} {echoed}",
    )


JOURNEYS: Final[tuple[Journey, ...]] = (
    journey_profile_picker,
    journey_invoke_stamp,
    journey_routing_oracle,
    journey_run_timeline,
    journey_unknown_profile,
    journey_workflow,
    journey_eval_report,
    journey_cors,
)
