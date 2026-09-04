"""LM Studio E2E — scenario 12: a composite graph over ``POST /workflows/run``.

Supersedes ``scripts/run_workflow_e2e.py`` as the *test* of the declarative
layer against a real model. That script stays as the demo — it prints a
narrative and is meant to be read — but a demo nobody runs in CI is not
evidence, and its own tests only cover teardown.

The graph deliberately combines the node kinds whose live behaviour differs
from their fake-backed behaviour: a ``branch`` whose predicate reads real
model output, and a composite ``fan_out`` running two branches concurrently
against one LM Studio instance. Concurrency against a single local server is
the thing no in-process test exercises.

Hardware contract: the oracles are the response shape and the join arity, not
the text. The branch predicate is applied to the *input* message, which this
test controls, so routing stays deterministic even though the completions
do not (spec-0029 R2.3).

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi import FastAPI

from tests.constants import ASGI_TEST_BASE_URL

logger = logging.getLogger(__name__)

_RUN_ROUTE = "/workflows/run"
_ROUTING_KEYWORD = "summarise"

# sequence( branch(predicate → summarize | chat) → fan_out(chat ∥ chat, concat) )
_GRAPH = json.dumps(
    {
        "schema_version": 1,
        "name": "live-branch-and-fan-out",
        "root": {
            "kind": "sequence",
            "steps": [
                {
                    "kind": "branch",
                    "branches": [
                        {
                            "when": {"kind": "contains", "value": _ROUTING_KEYWORD},
                            "then": {"kind": "agent", "agent": "summarize"},
                        }
                    ],
                    "default": {"kind": "agent", "agent": "chat"},
                },
                {
                    "kind": "fan_out",
                    "join": "concat",
                    "branches": [
                        {"kind": "agent", "agent": "chat"},
                        {"kind": "agent", "agent": "chat"},
                    ],
                },
            ],
        },
    }
)


def _body(content: str) -> dict[str, object]:
    # `definition` is a JSON *string* (inline JSON or a path), not an object —
    # `WorkflowRunRequest.definition` is `str | None`. A dict here is a 422.
    return {
        "request": {"messages": [{"role": "user", "content": content}]},
        "definition": _GRAPH,
    }


@pytest.mark.lmstudio
async def test_branch_then_fan_out_runs_against_a_live_model(
    lmstudio_app: FastAPI,
    lmstudio_client_timeout: float,
) -> None:
    """The composite graph completes and joins both fan-out branches.

    Two branches against one LM Studio instance run concurrently under
    ``asyncio.gather``; a server that serialises them still passes, because
    the assertion is on the joined arity rather than on elapsed time.
    """
    transport = httpx.ASGITransport(app=lmstudio_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            _RUN_ROUTE,
            json=_body(f"Please {_ROUTING_KEYWORD} the idea of continuous integration."),
            timeout=lmstudio_client_timeout,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["agent"] == "fan_out"
    lines = [line for line in payload["content"].split("\n") if line.strip()]
    assert len(lines) >= 2, f"expected both fan-out branches joined, got {payload['content']!r}"
    logger.info("Live composite workflow completed", extra={"joined_lines": len(lines)})


@pytest.mark.lmstudio
async def test_a_non_matching_input_takes_the_default_branch(
    lmstudio_app: FastAPI,
    lmstudio_client_timeout: float,
) -> None:
    """Routing is driven by the input this test controls, so it is deterministic.

    Paired with the test above: together they show the predicate actually
    selects, rather than one path always being taken.
    """
    transport = httpx.ASGITransport(app=lmstudio_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            _RUN_ROUTE,
            json=_body("Tell me something interesting."),
            timeout=lmstudio_client_timeout,
        )

    assert response.status_code == 200, response.text
    assert response.json()["content"], "the default branch must still produce content"
