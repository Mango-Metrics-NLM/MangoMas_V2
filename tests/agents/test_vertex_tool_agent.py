"""Tests for ToolAgent multi-step reasoning and declarative workflows under Vertex AI.

Validates Story 2.2 and Story 5.2 (Milestone C1):
1. ToolAgent multi-step reasoning with Vertex AI:
   - Tool execution and result re-injection as ``Message(role="tool")``.
   - VertexClient Gemini role folding: ``role="tool"`` maps to ``role="user"`` with
     ``[tool]`` prefix, while assistant messages map to ``role="model"``.
   - ToolAgent tracking and metadata: ``metadata["tool_steps"]`` accurately reflects
     the number of LLM completion steps executed.
   - Multi-step reasoning chains (2-step and 3-step tool invocations).
   - Step budget halting: when ``steps == max_tool_steps``, ToolAgent halts
     without executing the final tool call.
   - Error handling: ``ToolNotFound`` on unknown tool, ``ToolExecutionError`` on tool failure.
2. Declarative workflow graphs under Vertex AI:
   - CLI syntax & schema validation via ``mangomas workflow validate``.
   - Multi-node workflow execution (sequence, fan-out, loop) with an Orchestrator
     wired to VertexClient and FakeVertexGenerativeModel.
   - CLI execution via ``mangomas workflow run`` dispatching to VertexClient.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from mangomas.adapters.llm.vertex import VertexClient
from mangomas.adapters.llm.vertex._messages import _annotate, _vertex_role_for
from mangomas.agents import ChatAgent, SummarizeAgent
from mangomas.agents.tool_agent import ToolAgent
from mangomas.cli import _runtime as cli_runtime
from mangomas.cli import main as cli_main
from mangomas.config import DEFAULT_VERTEX_LOCATION, get_settings
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.core.tools import ToolRegistry
from mangomas.errors import ToolExecutionError, ToolNotFound
from mangomas.registry import Registry
from mangomas.workflow import execute_workflow
from mangomas.workflow.graph import WorkflowGraph
from tests.constants import STUB_REPLY, WORKFLOW_CONFIG_EXIT_CODE
from tests.fakes import FakeTool, FakeVertexGenerativeModel

_TEST_PROJECT = "demo-gcp-project"
_TEST_MODEL = "gemini-2.0-flash"


def _make_vertex_client(
    replies: list[str] | None = None,
    reply: str = STUB_REPLY,
) -> tuple[VertexClient, FakeVertexGenerativeModel]:
    """Build a VertexClient backed by an offline FakeVertexGenerativeModel."""
    fake = FakeVertexGenerativeModel(reply=reply, replies=replies or [])
    client = VertexClient(
        project_id=_TEST_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=_TEST_MODEL,
        client=fake,
    )
    return client, fake


def _make_tool_registry(*tools: FakeTool) -> ToolRegistry:
    """Create a ToolRegistry containing the given FakeTool instances."""
    reg: ToolRegistry = Registry("tool")
    for tool in tools:
        reg.register(tool.name, tool)
    return reg


def _req(content: str = "Execute task") -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content=content)])


# ── 1. Vertex Role Folding Contract ──────────────────────────────────────────


def test_vertex_role_folding_and_annotation() -> None:
    """Gemini only accepts user and model roles; other roles fold with annotations."""
    # Role mapping parity
    assert _vertex_role_for("assistant") == "model"
    assert _vertex_role_for("user") == "user"
    assert _vertex_role_for("system") == "user"
    assert _vertex_role_for("tool") == "user"

    # Annotation preservation
    assert _annotate(Message(role="user", content="hello")) == "hello"
    assert _annotate(Message(role="assistant", content="hi")) == "hi"
    assert _annotate(Message(role="system", content="prompt")) == "[system] prompt"
    tool_payload = json.dumps({"tool": "calculator", "output": 42})
    assert _annotate(Message(role="tool", content=tool_payload)) == f"[tool] {tool_payload}"


# ── 2. ToolAgent Multi-Step Reasoning with Vertex AI ─────────────────────────


async def test_vertex_tool_agent_two_step_reasoning_chain() -> None:
    """ToolAgent executes tool call in step 1 and returns final response in step 2."""
    weather_tool = FakeTool(name="get_weather", result="sunny and 24C")
    registry = _make_tool_registry(weather_tool)

    tool_call_json = json.dumps({"tool": "get_weather", "arguments": {"city": "San Francisco"}})
    reply_step_1 = f"```json\n{tool_call_json}\n```"
    reply_step_2 = "The weather in San Francisco is sunny and 24C."

    client, fake_model = _make_vertex_client(replies=[reply_step_1, reply_step_2])
    ctx = AgentContext(llm=client, repo=None, tools=registry)

    agent = ToolAgent(max_tool_steps=5)
    request = _req("What is the weather in SF?")
    response = await agent.handle(request, ctx)

    # Output & step count contract
    assert response.content == reply_step_2
    assert response.agent == "tool"
    assert response.metadata["tool_steps"] == 2

    # Tool invocation verification
    assert len(weather_tool.calls) == 1
    assert weather_tool.calls[0] == {"city": "San Francisco"}

    # Outbound Vertex payload verification (role folding)
    assert len(fake_model.calls) == 2
    step_2_contents = fake_model.calls[1]["contents"]

    # In step 2, verify assistant message has role 'model'
    # and tool result has role 'user' with '[tool]' prefix.
    roles = [entry["role"] for entry in step_2_contents]
    assert "model" in roles
    assert "user" in roles

    tool_entries = [
        entry
        for entry in step_2_contents
        if entry["role"] == "user" and entry["content"].startswith("[tool]")
    ]
    assert len(tool_entries) == 1
    tool_data = json.loads(tool_entries[0]["content"].removeprefix("[tool] "))
    assert tool_data["tool"] == "get_weather"
    assert tool_data["output"] == "sunny and 24C"


async def test_vertex_tool_agent_three_step_reasoning_chain() -> None:
    """ToolAgent executes two successive tool calls before producing a final response."""
    geo_tool = FakeTool(name="get_coordinates", result=json.dumps({"lat": 37.77, "lon": -122.41}))
    forecast_tool = FakeTool(name="get_forecast", result="moderate breeze")
    registry = _make_tool_registry(geo_tool, forecast_tool)

    step_1_call = json.dumps({"tool": "get_coordinates", "arguments": {"location": "SF"}})
    step_2_call = json.dumps({"tool": "get_forecast", "arguments": {"lat": 37.77, "lon": -122.41}})
    final_answer = "Forecast for SF: moderate breeze at (37.77, -122.41)."

    client, fake_model = _make_vertex_client(
        replies=[
            f"```json\n{step_1_call}\n```",
            f"```json\n{step_2_call}\n```",
            final_answer,
        ]
    )
    ctx = AgentContext(llm=client, repo=None, tools=registry)

    agent = ToolAgent(max_tool_steps=5)
    response = await agent.handle(_req("Get forecast"), ctx)

    assert response.content == final_answer
    assert response.metadata["tool_steps"] == 3
    assert len(geo_tool.calls) == 1
    assert geo_tool.calls[0] == {"location": "SF"}
    assert len(forecast_tool.calls) == 1
    assert forecast_tool.calls[0] == {"lat": 37.77, "lon": -122.41}
    assert len(fake_model.calls) == 3


async def test_vertex_tool_agent_step_budget_halting() -> None:
    """ToolAgent halts when max_tool_steps is reached without executing the final tool call."""
    search_tool = FakeTool(name="search", result="search-result-1")
    registry = _make_tool_registry(search_tool)

    tool_call_1 = json.dumps({"tool": "search", "arguments": {"q": "mango 1"}})
    tool_call_2 = json.dumps({"tool": "search", "arguments": {"q": "mango 2"}})
    reply_1 = f"```json\n{tool_call_1}\n```"
    reply_2 = f"```json\n{tool_call_2}\n```"

    client, fake_model = _make_vertex_client(replies=[reply_1, reply_2])
    ctx = AgentContext(llm=client, repo=None, tools=registry)

    # Budget capped at 2 LLM calls
    agent = ToolAgent(max_tool_steps=2)
    response = await agent.handle(_req("Run search"), ctx)

    # Halts after 2 steps; the second tool call is returned as content rather than executed
    assert response.metadata["tool_steps"] == 2
    assert response.content == reply_2
    assert len(search_tool.calls) == 1
    assert search_tool.calls[0] == {"q": "mango 1"}
    assert len(fake_model.calls) == 2


async def test_vertex_tool_agent_unknown_tool_raises_tool_not_found() -> None:
    """Requesting an unregistered tool raises ToolNotFound."""
    registry = _make_tool_registry(FakeTool(name="known_tool"))
    unknown_call = json.dumps({"tool": "missing_service", "arguments": {}})

    client, _ = _make_vertex_client(replies=[f"```json\n{unknown_call}\n```"])
    ctx = AgentContext(llm=client, repo=None, tools=registry)

    agent = ToolAgent()
    with pytest.raises(ToolNotFound) as exc_info:
        await agent.handle(_req("Go"), ctx)

    assert exc_info.value.name == "missing_service"


async def test_vertex_tool_agent_tool_execution_error_wrapped() -> None:
    """Exceptions raised during tool execution are wrapped as ToolExecutionError."""

    class FailingTool(FakeTool):
        async def execute(self, _arguments: dict[str, Any]) -> str:
            raise ConnectionResetError("Remote API disconnected")

    failing_tool = FailingTool(name="flaky_service")
    registry = _make_tool_registry(failing_tool)
    tool_call = json.dumps({"tool": "flaky_service", "arguments": {}})

    client, _ = _make_vertex_client(replies=[f"```json\n{tool_call}\n```"])
    ctx = AgentContext(llm=client, repo=None, tools=registry)

    agent = ToolAgent()
    with pytest.raises(ToolExecutionError) as exc_info:
        await agent.handle(_req("Call service"), ctx)

    assert exc_info.value.tool_name == "flaky_service"
    assert "Remote API disconnected" in str(exc_info.value)


# ── 3. Declarative Workflow Graphs under Vertex AI ────────────────────────────


def _build_test_graph() -> tuple[str, WorkflowGraph]:
    """Construct a multi-node workflow graph combining sequence, fan-out, and loop."""
    graph_dict: dict[str, Any] = {
        "schema_version": 1,
        "name": "vertex-multi-agent-pipeline",
        "root": {
            "kind": "sequence",
            "steps": [
                {"kind": "agent", "agent": "summarize"},
                {
                    "kind": "fan_out",
                    "join": "concat",
                    "branches": [
                        {"kind": "agent", "agent": "chat"},
                        {"kind": "agent", "agent": "helper"},
                    ],
                },
                {
                    "kind": "loop",
                    "agent": "chat",
                    "max_steps": 3,
                    "accept": {"kind": "contains", "value": "STATUS:DONE"},
                },
            ],
        },
    }
    graph_json = json.dumps(graph_dict)
    return graph_json, WorkflowGraph.model_validate(graph_dict)


def test_workflow_validate_cli_valid_graph() -> None:
    """CLI mangomas workflow validate passes on valid multi-node workflow graph."""
    runner = CliRunner()
    graph_json, _ = _build_test_graph()

    result = runner.invoke(cli_main.app, ["workflow", "validate", "-f", graph_json])
    assert result.exit_code == 0
    assert "ok" in result.stdout
    assert "name=vertex-multi-agent-pipeline" in result.stdout
    assert "root=sequence" in result.stdout


def test_workflow_validate_cli_invalid_syntax_exits_config_error() -> None:
    """CLI mangomas workflow validate exits 2 on malformed JSON or invalid schema."""
    runner = CliRunner()

    # Malformed JSON
    res1 = runner.invoke(cli_main.app, ["workflow", "validate", "-f", "{not-valid-json"])
    assert res1.exit_code == WORKFLOW_CONFIG_EXIT_CODE

    # Invalid node kind
    bad_schema = json.dumps(
        {"schema_version": 1, "name": "bad", "root": {"kind": "nonexistent_node"}}
    )
    res2 = runner.invoke(cli_main.app, ["workflow", "validate", "-f", bad_schema])
    assert res2.exit_code == WORKFLOW_CONFIG_EXIT_CODE


async def test_workflow_execution_under_vertex_orchestrator() -> None:
    """Execute composite sequence -> fan_out -> loop workflow against Vertex-backed orchestrator."""
    _, graph = _build_test_graph()

    # Sequence of replies consumed across workflow steps:
    # 1. summarize: generates preliminary summary
    # 2. fan_out branch 1 (chat): perspective A
    # 3. fan_out branch 2 (helper): perspective B
    # 4. loop turn 1 (chat): draft attempt (not matching accept predicate)
    # 5. loop turn 2 (chat): final accepted output (contains 'STATUS:DONE')
    replies = [
        "Executive Summary: Initial draft.",
        "Perspective A: Architecture looks good.",
        "Perspective B: Security verified.",
        "Refinement Draft 1: Working on it.",
        "Refinement Final: All checks passed. STATUS:DONE",
    ]

    client, fake_model = _make_vertex_client(replies=replies)
    ctx = AgentContext(llm=client, repo=None)
    orch = Orchestrator(ctx)

    # Register agents needed by the graph
    orch.register(SummarizeAgent())
    orch.register(ChatAgent())

    class HelperAgent(ChatAgent):
        name = "helper"

    orch.register(HelperAgent())

    request = _req("Initiate multi-agent workflow review")
    response: AgentResponse = await execute_workflow(graph, request, orch=orch)

    # Assert final result produced by the terminating loop node
    assert "STATUS:DONE" in response.content
    assert response.content == "Refinement Final: All checks passed. STATUS:DONE"

    # Verify that all 5 calls were dispatched to the Vertex client
    assert len(fake_model.calls) == 5


def test_workflow_run_cli_under_vertex_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI mangomas workflow run dispatches through Vertex-wired orchestrator and prints result."""
    runner = CliRunner()
    graph_json, _ = _build_test_graph()

    replies = [
        "Summary step.",
        "Branch A.",
        "Branch B.",
        "Final step with STATUS:DONE",
    ]
    client, _ = _make_vertex_client(replies=replies)
    ctx = AgentContext(llm=client, repo=None)
    orch = Orchestrator(ctx)
    orch.register(SummarizeAgent())
    orch.register(ChatAgent())

    class HelperAgent(ChatAgent):
        name = "helper"

    orch.register(HelperAgent())

    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    async def _noop_close(_orch: Orchestrator) -> None:
        return None

    monkeypatch.setattr(cli_runtime, "_close_orchestrator", _noop_close)
    get_settings.cache_clear()

    result = runner.invoke(
        cli_main.app,
        ["workflow", "run", "Start review", "--definition", graph_json],
    )
    assert result.exit_code == 0
    assert "STATUS:DONE" in result.stdout
