"""Regression + AQA guards for the 2026-10-29 origin-delta validation (spec-0039).

Branch ``sdlc/origin-delta-aqa-20261029``, cut from
``origin/feat/initial-release@a98b626``. Each guard is hermetic (fakes,
``httpx.MockTransport``, AST) and was run red against the pre-fix tree before
its fix landed. The register, with root causes and the mutation proof for each
guard, is ``docs/testing/rca-20261029.md``.

  D-1  Live-suite per-step budget (30 s) was smaller than the adapter budget
       it wraps (240 s), so must-not-fire step timeouts fired on any model
       slower than 30 s per step (spec-0029 R2.1 / R2.5).
  D-2  ``ToolAgent`` with no tool registry rejected any fenced JSON reply as a
       botched tool call, breaking the shipped plan-execute-review graph.
  D-3  ``tests/test_auth.py`` passed ``dict[str, bytes]`` headers, which is not
       an httpx ``HeaderTypes`` member; ``mypy --strict`` fails as soon as
       ``httpx2`` (via the ``embeddings-local`` extra) is installed.
  D-4  ``RecursionError`` from a pathologically nested 2xx body escaped both
       LM Studio adapters as an untyped 500.
  D-5  One malformed SSE chunk (non-object ``delta``, or a nested chunk)
       aborted the whole stream instead of being skipped.
  D-6  An invalid ``MANGOMAS_*`` value crashed every CLI command with a raw
       pydantic traceback (exit 1) that echoed the offending value.
"""

from __future__ import annotations

import ast
import json
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer
from pydantic import BaseModel, ValidationError, model_validator
from typer.main import get_command
from typer.testing import CliRunner

from mangomas.adapters.embeddings.lmstudio import (
    LMStudioEmbeddingClient,
    LMStudioEmbeddingError,
)
from mangomas.adapters.llm.lmstudio import LMStudioClient, LMStudioError
from mangomas.agents.tool_agent import ToolAgent
from mangomas.cli import _app as cli_app
from mangomas.cli._app import app
from mangomas.config import get_settings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.core.tools import ToolRegistry
from mangomas.errors import LLMBadResponse
from mangomas.registry import Registry
from tests._seam_guards import forbid_real_orchestrator
from tests.constants import (
    DEEP_NESTED_JSON,
    DEFAULT_AGENT_NAME,
    DEFAULT_LLM_BASE_URL,
    EXIT_CONFIG_ERROR,
    LLM_ALLOWED_MODELS_ENV,
    LLM_MODEL_ENV,
    LLM_TEMPERATURE_ENV,
    LMSTUDIO_E2E_TIMEOUT_ENV,
    LOOP_STEP_TIMEOUT_ENV,
    SLOW_BOX_LIVE_TIMEOUT_SECONDS,
    TEST_LMSTUDIO_MOCK_BASE_URL,
    TEST_LMSTUDIO_STUB_MODEL,
)
from tests.fakes import FakeLLM, FakeTool
from tests.lmstudio.conftest import make_lmstudio_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── D-1: live per-step budget never below the adapter budget ─────────────────


@pytest.fixture
def _no_live_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOOP_STEP_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(LMSTUDIO_E2E_TIMEOUT_ENV, raising=False)


@pytest.mark.usefixtures("_no_live_budget_env")
def test_d1_default_step_budget_is_not_below_the_adapter_budget() -> None:
    """The must-not-fire outer bound may not be tighter than the call it wraps."""
    settings = make_lmstudio_settings(DEFAULT_LLM_BASE_URL, TEST_LMSTUDIO_STUB_MODEL)
    assert settings.loop.step_timeout_seconds >= settings.llm.timeout_seconds, (
        f"per-step budget {settings.loop.step_timeout_seconds}s is below the adapter "
        f"budget {settings.llm.timeout_seconds}s — a slow model times out on the step, "
        "not on the call (spec-0029 R2.5)"
    )


@pytest.mark.usefixtures("_no_live_budget_env")
def test_d1_step_budget_follows_an_operator_raised_adapter_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising the adapter budget for slow hardware must raise the step budget too."""
    raised = SLOW_BOX_LIVE_TIMEOUT_SECONDS
    monkeypatch.setenv(LMSTUDIO_E2E_TIMEOUT_ENV, str(raised))
    settings = make_lmstudio_settings(DEFAULT_LLM_BASE_URL, TEST_LMSTUDIO_STUB_MODEL)
    assert settings.llm.timeout_seconds == raised
    assert settings.loop.step_timeout_seconds >= raised


# ── D-2: no registry ⇒ a fenced JSON reply is content, not a tool call ───────

# The planner→tool hop's real failure shape: the tool agent echoes the
# planner's JSON plan in a ```json fence, with no "tool" key.
_ECHOED_PLAN_REPLY = "```json\n" + json.dumps({"steps": [{"agent": "chat"}]}) + "\n```"


def _tool_request() -> AgentRequest:
    return AgentRequest(messages=[Message(role="user", content="execute the plan")])


async def test_d2_no_registry_returns_a_fenced_json_reply_as_content() -> None:
    agent = ToolAgent()
    ctx = AgentContext(llm=FakeLLM(reply=_ECHOED_PLAN_REPLY), repo=None, tools=None)
    response = await agent.handle(_tool_request(), ctx)
    assert response.content == _ECHOED_PLAN_REPLY
    assert response.metadata["tool_steps"] == 1


async def test_d2_a_configured_registry_still_rejects_a_botched_tool_call() -> None:
    """Boundary kept: once the model was given the tool contract, it is an error."""
    registry: ToolRegistry = Registry("tool")
    tool = FakeTool()
    registry.register(tool.name, tool)
    ctx = AgentContext(llm=FakeLLM(reply=_ECHOED_PLAN_REPLY), repo=None, tools=registry)
    with pytest.raises(LLMBadResponse):
        await ToolAgent().handle(_tool_request(), ctx)


# ── D-3: test_auth headers must be a valid httpx HeaderTypes member ──────────

_AUTH_TESTS = _REPO_ROOT / "tests" / "test_auth.py"


def _bytes_names(tree: ast.Module) -> set[str]:
    """Module-level names bound to ``<str>.encode(...)`` or ``bytes`` constants."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value: ast.expr | None = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        if value is None:
            continue
        is_encode = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "encode"
        )
        is_bytes_const = isinstance(value, ast.Constant) and isinstance(value.value, bytes)
        if is_encode or is_bytes_const:
            names.update(targets)
    return names


def _is_bytes_expr(node: ast.expr, bytes_names: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, bytes)
    if isinstance(node, ast.Name):
        return node.id in bytes_names
    if isinstance(node, ast.BinOp):
        return _is_bytes_expr(node.left, bytes_names) or _is_bytes_expr(node.right, bytes_names)
    return False


def _mixed_header_literals(source: str) -> list[int]:
    """Lines where a ``headers={...}`` literal mixes ``str`` keys with bytes values."""
    tree = ast.parse(source)
    bytes_names = _bytes_names(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "headers":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        str_keys = any(
            isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.value.keys
        )
        bytes_values = any(_is_bytes_expr(v, bytes_names) for v in node.value.values)
        if str_keys and bytes_values:
            lines.append(node.value.lineno)
    return lines


def test_d3_auth_tests_never_mix_str_keys_with_bytes_values() -> None:
    mixed = _mixed_header_literals(_AUTH_TESTS.read_text(encoding="utf-8"))
    assert not mixed, (
        f"tests/test_auth.py lines {mixed} pass a dict[str, bytes] header mapping, which "
        "is not an httpx HeaderTypes member; mypy --strict fails once httpx2 is installed. "
        "Use an all-bytes mapping ({b'Authorization': ...}) to keep the raw wire bytes."
    )


def test_d3_detector_flags_the_defect_shape() -> None:
    """The guard above is not vacuous: it does see the shape it forbids."""
    defect = 'W = "t".encode("utf-8")\nf(headers={"Authorization": b"Bearer " + W})\n'
    fixed = 'W = "t".encode("utf-8")\nf(headers={b"Authorization": b"Bearer " + W})\n'
    assert _mixed_header_literals(defect) == [2]
    assert _mixed_header_literals(fixed) == []


# ── D-4: nested 2xx body ⇒ typed malformed-response error ───────────────────


def _nested_body_client() -> httpx.AsyncClient:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=DEEP_NESTED_JSON.encode(), headers={"content-type": "application/json"}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


async def test_d4_chat_completion_maps_a_nested_body_to_the_typed_error() -> None:
    client = LMStudioClient(
        TEST_LMSTUDIO_MOCK_BASE_URL, TEST_LMSTUDIO_STUB_MODEL, client=_nested_body_client()
    )
    with pytest.raises(LMStudioError):
        await client.complete([Message(role="user", content="hi")])
    await client.aclose()


async def test_d4_embeddings_map_a_nested_body_to_the_typed_error() -> None:
    client = LMStudioEmbeddingClient(
        TEST_LMSTUDIO_MOCK_BASE_URL, TEST_LMSTUDIO_STUB_MODEL, client=_nested_body_client()
    )
    with pytest.raises(LMStudioEmbeddingError):
        await client.embed_batch(["hi"])
    await client.aclose()


# ── D-5: malformed SSE chunks are skipped, the stream continues ──────────────

_MALFORMED_SSE_LINES: tuple[str, ...] = (
    'data: {"choices": [{"delta": null}]}',
    'data: {"choices": [{"delta": "not-an-object"}]}',
    "data: " + DEEP_NESTED_JSON,
)


def _sse(content: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": content}}]})


@pytest.mark.parametrize("line", _MALFORMED_SSE_LINES, ids=["null", "string", "nested"])
def test_d5_a_malformed_chunk_is_skipped(line: str) -> None:
    assert LMStudioClient._parse_sse_line(line) is None


async def test_d5_the_stream_survives_a_malformed_chunk() -> None:
    body = "\n".join([_sse("a"), *_MALFORMED_SSE_LINES, _sse("b"), "data: [DONE]"]) + "\n"

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode())

    client = LMStudioClient(
        TEST_LMSTUDIO_MOCK_BASE_URL,
        TEST_LMSTUDIO_STUB_MODEL,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    tokens = [t async for t in await client.stream([Message(role="user", content="hi")])]
    assert tokens == ["a", "b"]
    await client.aclose()


# ── D-6: invalid MANGOMAS_* ⇒ exit 2, no traceback, no echoed value ─────────

# A float field: any non-numeric value fails validation.
_INVALID_ENV = LLM_TEMPERATURE_ENV
_PLACEHOLDER_ARG = "x"


def _leaf_command_paths() -> list[list[str]]:
    """Every leaf command in the assembled Click tree, with placeholder positionals.

    Discovered, not hand-listed, so a command added later is covered automatically.
    Duck-typed on ``commands`` / ``param_type_name`` because Typer vendors its
    own Click, whose classes are not ``click.Group`` / ``click.Argument``.
    """
    paths: list[list[str]] = []

    def walk(command: Any, prefix: list[str]) -> None:
        subcommands = getattr(command, "commands", None)
        if isinstance(subcommands, dict):
            for name, sub in subcommands.items():
                walk(sub, [*prefix, name])
            return
        positionals = [
            _PLACEHOLDER_ARG
            for param in command.params
            if param.param_type_name == "argument" and param.required
        ]
        paths.append([*prefix, *positionals])

    walk(get_command(app), [])
    return paths


@pytest.fixture
def _invalid_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    forbid_real_orchestrator(monkeypatch)
    marker = f"leak-{secrets.token_hex(8)}"
    monkeypatch.setenv(_INVALID_ENV, marker)
    get_settings.cache_clear()
    yield marker
    get_settings.cache_clear()


@pytest.mark.parametrize("argv", _leaf_command_paths(), ids=" ".join)
def test_d6_invalid_settings_exit_with_the_config_code(
    argv: list[str], _invalid_settings_env: str
) -> None:
    result = CliRunner().invoke(app, argv)
    output = result.output
    assert result.exit_code == EXIT_CONFIG_ERROR, output
    # Naming the variable is what distinguishes this from a Click usage error,
    # which also exits 2.
    assert _INVALID_ENV in output, output
    assert "Traceback" not in output
    assert _invalid_settings_env not in output, "the offending value was echoed"


def test_d6_command_tree_is_not_empty() -> None:
    """The parametrised guard above cannot pass vacuously over zero commands."""
    paths = {" ".join(argv) for argv in _leaf_command_paths()}
    assert {
        "agents",
        f"{DEFAULT_AGENT_NAME} x",
        "history",
        "rag query x",
        "workflow run x",
    } <= paths, paths


class _UnrelatedModel(BaseModel):
    count: int


def test_d6_a_non_settings_validation_error_still_propagates() -> None:
    """Only a *settings* error is a configuration error; anything else is a crash."""
    probe = typer.Typer(cls=cli_app.SettingsErrorGroup)

    @probe.command()
    def boom() -> None:
        _UnrelatedModel.model_validate({"count": "nope"})

    @probe.command()
    def other() -> None:  # a second command keeps Typer in group mode
        """Unused."""

    result = CliRunner().invoke(probe, ["boom"])
    assert isinstance(result.exception, ValidationError)
    assert result.exit_code != EXIT_CONFIG_ERROR


def test_d6_invalid_complex_json_setting_exits_with_config_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON in a complex env setting (SettingsError) must exit 2 without echoing it."""
    forbid_real_orchestrator(monkeypatch)
    marker = f"not-json-{secrets.token_hex(8)}"
    monkeypatch.setenv(LLM_ALLOWED_MODELS_ENV, marker)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["agents"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == EXIT_CONFIG_ERROR, result.output
    assert "Configuration error" in result.output
    assert "MANGOMAS_LLM:" in result.output
    assert "invalid JSON value" in result.output
    assert "Traceback" not in result.output
    assert marker not in result.output, "the offending JSON string was echoed"


class _EmptyLocation(BaseModel):
    """A model whose only failure is a model-level validator (``loc=()``)."""

    @model_validator(mode="after")
    def _reject(self) -> _EmptyLocation:
        raise ValueError("rejected")


def test_d6_an_empty_error_location_names_the_prefix() -> None:
    """A model-level error must not render as ``MANGOMAS_:``."""
    with pytest.raises(ValidationError) as caught:
        _EmptyLocation()
    assert caught.value.errors()[0]["loc"] == ()
    rendered = cli_app.format_settings_error(caught.value)
    assert "MANGOMAS_:" not in rendered
    assert "  MANGOMAS:" in rendered


def test_d6_allowlist_mismatch_redacts_quoted_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom validator text must not echo the model id or the roster."""
    forbid_real_orchestrator(monkeypatch)
    model = f"secret-model-{secrets.token_hex(4)}"
    roster = f"secret-roster-{secrets.token_hex(4)}"
    monkeypatch.setenv(LLM_MODEL_ENV, model)
    monkeypatch.setenv(LLM_ALLOWED_MODELS_ENV, json.dumps([roster]))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["agents"])
    finally:
        get_settings.cache_clear()
    output = result.output
    assert result.exit_code == EXIT_CONFIG_ERROR, output
    assert model not in output
    assert roster not in output
    assert "'<redacted>'" in output
    assert "Traceback" not in output


def test_d6_allowlist_redacts_both_quote_styles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``!r`` switches quotes and escapes the one it keeps; both must be scrubbed.

    An apostrophe-only id is double-quoted. An id that also contains a double
    quote stays single-quoted with a backslash before the apostrophe, which a
    ``'[^']*'`` scrub cuts in half and leaks.
    """
    forbid_real_orchestrator(monkeypatch)
    token = secrets.token_hex(4)
    model = f"it's-{token}\"end"
    roster = f"o'{token}"
    monkeypatch.setenv(LLM_MODEL_ENV, model)
    monkeypatch.setenv(LLM_ALLOWED_MODELS_ENV, json.dumps([roster]))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["agents"])
    finally:
        get_settings.cache_clear()
    output = result.output
    assert result.exit_code == EXIT_CONFIG_ERROR, output
    assert token not in output, output
    assert model not in output, output
    assert roster not in output, output
    assert "'<redacted>'" in output
    assert "Traceback" not in output


def _non_list_embedding_client() -> httpx.AsyncClient:
    def respond(_request: httpx.Request) -> httpx.Response:
        # ``"12"`` iterates to ``[1.0, 2.0]`` if the list check is gone.
        # ``"not-a-list"`` already dies in ``float()`` and would not notice.
        body = json.dumps({"data": [{"embedding": "12", "index": 0}]})
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "application/json"}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


async def test_d4_a_non_list_embedding_is_a_typed_error() -> None:
    """A numeric string is not a vector of floats.

    Without the list check, ``"12"`` is accepted as ``[1.0, 2.0]``. A
    non-numeric string fails inside ``float()`` either way, so it cannot
    show that the guard was removed.
    """
    client = LMStudioEmbeddingClient(
        TEST_LMSTUDIO_MOCK_BASE_URL,
        TEST_LMSTUDIO_STUB_MODEL,
        client=_non_list_embedding_client(),
    )
    with pytest.raises(LMStudioEmbeddingError):
        await client.embed_batch(["hi"])
    await client.aclose()
