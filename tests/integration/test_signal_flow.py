"""Tier-1: CognitiveSignal emission through the real composition root.

Flag-off must not write JSONL or attach a sink. Flag-on must leave the HTTP
body identical and append one ingestible JSONL line. Gated by
``RUN_INTEGRATION=1`` (CI runs ``make gated-suites``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mango_contracts import CognitiveSignal, SignalKind
from mangomas.cognitive.constants import COGNITIVE_SINK_EXTRAS_KEY
from tests.constants import (
    JSONL_FILENAME,
    PLANNER_SIGNAL_GOAL,
    PLANNER_SIGNAL_REPLY,
    REVIEWER_SIGNAL_REPLY,
    SIGNAL_DIR_ENV,
    SIGNAL_ENABLED_ENV,
)
from tests.fakes import FakeLLM
from tests.integration.conftest import ComposeFn

pytestmark = pytest.mark.integration


async def test_flag_off_invoke_writes_no_jsonl(compose_app: ComposeFn, tmp_path: Path) -> None:
    composed = compose_app(
        env={SIGNAL_DIR_ENV: str(tmp_path)},
        llm=FakeLLM(reply=PLANNER_SIGNAL_REPLY),
    )
    assert COGNITIVE_SINK_EXTRAS_KEY not in composed.orchestrator.context.extras

    async with composed.client() as client:
        response = await client.post(
            "/agents/planner/invoke",
            json={"messages": [{"role": "user", "content": "go"}]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == PLANNER_SIGNAL_REPLY
    assert list(tmp_path.iterdir()) == []


async def test_flag_on_planner_invoke_writes_one_jsonl_line(
    compose_app: ComposeFn, tmp_path: Path
) -> None:
    composed = compose_app(
        env={
            SIGNAL_ENABLED_ENV: "true",
            SIGNAL_DIR_ENV: str(tmp_path),
        },
        llm=FakeLLM(reply=PLANNER_SIGNAL_REPLY),
    )
    assert COGNITIVE_SINK_EXTRAS_KEY in composed.orchestrator.context.extras

    async with composed.client() as client:
        response = await client.post(
            "/agents/planner/invoke",
            json={"messages": [{"role": "user", "content": "go"}]},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == PLANNER_SIGNAL_REPLY
    path = tmp_path / JSONL_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    signal = CognitiveSignal.model_validate_json(lines[0])
    assert signal.signal_kind is SignalKind.PLANNING_PROPOSAL
    assert signal.payload["goal"] == PLANNER_SIGNAL_GOAL


async def test_flag_on_reviewer_invoke_writes_one_jsonl_line(
    compose_app: ComposeFn, tmp_path: Path
) -> None:
    composed = compose_app(
        env={
            SIGNAL_ENABLED_ENV: "true",
            SIGNAL_DIR_ENV: str(tmp_path),
        },
        llm=FakeLLM(reply=REVIEWER_SIGNAL_REPLY),
    )

    async with composed.client() as client:
        response = await client.post(
            "/agents/reviewer/invoke",
            json={"messages": [{"role": "user", "content": "go"}]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == REVIEWER_SIGNAL_REPLY
    path = tmp_path / JSONL_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    signal = CognitiveSignal.model_validate_json(lines[0])
    assert signal.signal_kind is SignalKind.REVIEW_FINDING


async def test_flag_on_planner_stream_writes_no_jsonl(
    compose_app: ComposeFn, tmp_path: Path
) -> None:
    composed = compose_app(
        env={
            SIGNAL_ENABLED_ENV: "true",
            SIGNAL_DIR_ENV: str(tmp_path),
        },
        llm=FakeLLM(reply=PLANNER_SIGNAL_REPLY),
    )

    async with (
        composed.client() as client,
        client.stream(
            "POST",
            "/agents/planner/stream",
            json={"messages": [{"role": "user", "content": "go"}]},
        ) as response,
    ):
        assert response.status_code == 200, response.text
        async for _line in response.aiter_lines():
            pass

    assert list(tmp_path.iterdir()) == []
