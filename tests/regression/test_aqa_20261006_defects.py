"""Regression + acceptance guards for the 2026-10-06 origin-sync / PR #83 audit.

Branch ``sdlc/origin-sync-pr83-aqa-20261006``. Every guard here was verified to
fail against the pre-fix code (mutation proof) and to pass after the fix. No
live LLM is required; upstreams are ``respx``/``MockTransport`` fakes.

Defect classes covered (``[trunk]`` = present on ``origin/feat/initial-release``):

  D2 [trunk] — LM Studio LLM + embedding adapters leaked a raw ``JSONDecodeError``
               (and ``RecursionError``) on a 2xx non-JSON / pathologically nested
               body: ``resp.json()`` ran outside every ``try``, so the API answered a
               bare 500 with no error envelope instead of the typed 502.
  D3 [trunk] — ``tests/test_auth.py`` passed ``dict[str, bytes]`` headers, which is
               not a member of httpx's ``HeaderTypes``. CI never saw it: Starlette's
               TestClient does ``try: import httpx2 as httpx`` and, without httpx2,
               mypy degrades the parameter to ``Any``; with httpx2 installed
               ``mypy --strict`` fails the gate.
"""

from __future__ import annotations

import ast
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mangomas.adapters._http_errors import JSON_DECODE_ERRORS
from mangomas.adapters.embeddings.lmstudio import (
    LMStudioEmbeddingClient,
    LMStudioEmbeddingError,
)
from mangomas.adapters.llm.lmstudio import LMStudioClient, LMStudioError
from mangomas.adapters.storage.sqlite import SQLiteRepository
from mangomas.agents.chat import ChatAgent
from mangomas.api.app import create_app
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, get_settings
from mangomas.core import AgentContext, Message, Orchestrator
from mangomas.errors import LLMBadResponse
from tests.constants import (
    TEST_EMBEDDINGS_MOCK_MODEL,
    TEST_LMSTUDIO_MOCK_BASE_URL,
    TEST_LMSTUDIO_MOCK_MODEL,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"
_CHAT_COMPLETIONS_URL = f"{TEST_LMSTUDIO_MOCK_BASE_URL}/chat/completions"
_EMBEDDINGS_URL = f"{TEST_LMSTUDIO_MOCK_BASE_URL}/embeddings"
_INVOKE_PATH = "/agents/chat/invoke"
_HTTP_BAD_GATEWAY = 502

# A body a proxy / captive portal / wrong-server ``base_url`` returns with a 200.
# The marker string is asserted *absent* from every error surface.
_HTML_MARKER = "proxy-login-page-marker"
_HTML_BODY = f"<html><body>{_HTML_MARKER}</body></html>".encode()
_HTML_CONTENT_TYPE = "text/html"
_UNDECODABLE_BODY = b"\xff\xfe\xfa not utf-8"

# Derived, not hard-coded: comfortably past the interpreter recursion limit on
# any platform, so the C JSON scanner raises RecursionError deterministically.
_PATHOLOGICAL_DEPTH = sys.getrecursionlimit() * 10


def _nested_json(depth: int = _PATHOLOGICAL_DEPTH) -> bytes:
    return b"[" * depth + b"]" * depth


def _llm(client: httpx.AsyncClient | None = None) -> LMStudioClient:
    return LMStudioClient(
        base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_LMSTUDIO_MOCK_MODEL, client=client
    )


def _embedder() -> LMStudioEmbeddingClient:
    return LMStudioEmbeddingClient(
        base_url=TEST_LMSTUDIO_MOCK_BASE_URL, model=TEST_EMBEDDINGS_MOCK_MODEL
    )


_MSGS = [Message(role="user", content="hi")]


# ── D2: shared decode-error vocabulary ─────────────────────────────────────────


def test_d2_json_decode_errors_cover_every_decoder_failure() -> None:
    """The shared tuple must catch all three ways ``json.loads`` fails on bytes."""
    import json  # noqa: PLC0415 - local: only this guard needs the stdlib decoder

    failures: list[BaseException] = []
    for body in (_HTML_BODY, _UNDECODABLE_BODY, _nested_json()):
        try:
            json.loads(body)
        except Exception as exc:  # classifying; each is re-asserted below
            failures.append(exc)
    assert {type(f).__name__ for f in failures} >= {
        "JSONDecodeError",
        "UnicodeDecodeError",
        "RecursionError",
    }
    assert all(isinstance(f, JSON_DECODE_ERRORS) for f in failures)


# ── D2: LLM adapter ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        (_HTML_BODY, "JSONDecodeError"),
        (_UNDECODABLE_BODY, "UnicodeDecodeError"),
        (_nested_json(), "RecursionError"),
    ],
    ids=["html", "undecodable", "nested"],
)
@respx.mock
async def test_d2_llm_complete_non_json_body_raises_typed_error(
    body: bytes, expected_error: str, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(_CHAT_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": _HTML_CONTENT_TYPE})
    )
    caplog.set_level(logging.ERROR, logger="mangomas.adapters._openai_client")
    with pytest.raises(LMStudioError) as info:
        await _llm().complete(_MSGS)
    err = info.value
    assert isinstance(err, LLMBadResponse)
    assert "non-JSON" in str(err)
    assert expected_error in err.detail
    assert _HTML_CONTENT_TYPE in err.detail
    assert f"bytes={len(body)}" in err.detail
    assert len(err.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE
    assert _HTML_MARKER not in err.detail
    records = [r for r in caplog.records if getattr(r, "error", None) == expected_error]
    assert records, "the decode failure must be logged with its error class"
    assert all(_HTML_MARKER not in r.getMessage() for r in caplog.records)


@respx.mock
async def test_d2_llm_valid_json_path_is_unchanged() -> None:
    """Backwards compatibility: the success path still returns the content."""
    respx.post(_CHAT_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    assert await _llm().complete(_MSGS) == "ok"


def test_d2_sse_chunk_with_pathological_nesting_is_skipped_not_raised() -> None:
    """A garbled SSE chunk keeps the existing skip-unparseable policy."""
    line = "data: " + _nested_json().decode()
    assert LMStudioClient._parse_sse_line(line) is None


# ── D2: embedding adapter ──────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [_HTML_BODY, _nested_json()], ids=["html", "nested"])
@respx.mock
async def test_d2_embeddings_non_json_body_raises_typed_error(body: bytes) -> None:
    respx.post(_EMBEDDINGS_URL).mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": _HTML_CONTENT_TYPE})
    )
    with pytest.raises(LMStudioEmbeddingError) as info:
        await _embedder().embed_batch(["hi"])
    assert "non-JSON" in str(info.value)
    assert _HTML_MARKER not in info.value.detail


# ── D2 AQA: black-box through the HTTP API ─────────────────────────────────────


def _html_upstream(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=_HTML_BODY, headers={"content-type": _HTML_CONTENT_TYPE})


@pytest.fixture
def html_upstream_app() -> Iterator[FastAPI]:
    """A real app whose LM Studio upstream answers every call with a 200 HTML page."""
    get_settings.cache_clear()
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(_html_upstream))
    repo = SQLiteRepository(":memory:")
    orch = Orchestrator(AgentContext(llm=_llm(upstream), repo=repo))
    orch.register(ChatAgent())
    yield create_app(orchestrator=orch)
    repo.close()
    get_settings.cache_clear()


def test_d2_api_answers_502_envelope_not_bare_500(html_upstream_app: FastAPI) -> None:
    """Before the fix this was an unmapped exception: bare 500, no envelope."""
    with TestClient(html_upstream_app, raise_server_exceptions=False) as client:
        r = client.post(_INVOKE_PATH, json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == _HTTP_BAD_GATEWAY, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"] == LMStudioError.code
    assert _HTML_MARKER not in r.text


# ── D3: header typing hazard ───────────────────────────────────────────────────


def _mixed_str_bytes_header_dicts(tree: ast.AST) -> Iterator[int]:
    """Yield line numbers of ``headers={"str": b"bytes"...}`` keyword arguments."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "headers":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            str_key = isinstance(key, ast.Constant) and isinstance(key.value, str)
            if str_key and _is_bytes_expr(value):
                yield node.value.lineno


def _is_bytes_expr(value: ast.expr) -> bool:
    """Best-effort static bytes detection: literals, ``+`` of them, ``*_WIRE`` names.

    ``*_WIRE`` is this suite's naming convention for pre-encoded wire bytes
    (``tests/test_auth.py``); a name-based rule is the only static signal short
    of type inference, and the mutation test below pins that it still fires.
    """
    if isinstance(value, ast.Constant):
        return isinstance(value.value, bytes)
    if isinstance(value, ast.BinOp):
        return _is_bytes_expr(value.left) or _is_bytes_expr(value.right)
    if isinstance(value, ast.Name):
        return value.id.endswith("_WIRE")
    return False


def _test_sources() -> Iterator[Path]:
    yield from sorted(_TESTS_ROOT.rglob("*.py"))


def test_d3_no_test_passes_mixed_str_bytes_header_dicts() -> None:
    """``Mapping[bytes, bytes]`` or ``Mapping[str, str]`` only — never mixed.

    Structural, so it holds in CI where TestClient's header type is masked to
    ``Any`` (no httpx2), which is exactly where ``mypy --strict`` cannot see it.
    """
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{line}"
        for path in _test_sources()
        for line in _mixed_str_bytes_header_dicts(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], f"mixed str/bytes header dicts: {offenders}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('f(headers={"Authorization": b"Bearer " + X_WIRE})', 1),
        ('f(headers={"X-API-Key": X_WIRE})', 1),
        ('f(headers={b"Authorization": b"Bearer " + X_WIRE})', 0),
        ('f(headers={"Authorization": "Bearer t"})', 0),
    ],
    ids=["str-key-bytes-binop", "str-key-wire-name", "bytes-key", "all-str"],
)
def test_d3_detector_is_mutation_sensitive(source: str, expected: int) -> None:
    """The guard above must flag the pre-fix shape and pass the fixed one."""
    assert len(list(_mixed_str_bytes_header_dicts(ast.parse(source)))) == expected
