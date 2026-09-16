"""Unit tests for the Vertex AI adapter.

All tests use :class:`FakeVertexGenerativeModel` so the real ``vertexai`` SDK
is never imported. Error-mapping tests synthesise exception classes with the
``__module__``/``__qualname__`` strings that ``_translate_vertex_error``
matches against — no Google packages required.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest

from mangomas.adapters.llm.vertex import (
    VertexClient,
    VertexError,
    _translate_vertex_error,
)
from mangomas.config import DEFAULT_VERTEX_LOCATION
from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable
from tests.constants import TEST_MAX_TOKENS_OVERRIDE
from tests.fakes import FakeVertexGenerativeModel

_TEST_PROJECT = "unit-test-project"
_TEST_MODEL = "gemini-fake"


def _make_vertex_exception(qualname: str, message: str = "boom") -> Exception:
    """Synthesize an exception whose qualname matches the SDK exception path."""
    module, name = qualname.rsplit(".", 1)
    cls = type(name, (Exception,), {"__module__": module})
    instance = cls(message)
    assert isinstance(instance, Exception)
    return instance


def _make_client(
    *,
    model: FakeVertexGenerativeModel | None = None,
    project_id: str | None = _TEST_PROJECT,
) -> tuple[VertexClient, FakeVertexGenerativeModel]:
    fake = model or FakeVertexGenerativeModel()
    client = VertexClient(
        project_id=project_id,
        location=DEFAULT_VERTEX_LOCATION,
        model=_TEST_MODEL,
        client=fake,
    )
    return client, fake


# ── Error translation matrix ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("qualname", "expected_cls"),
    [
        ("google.api_core.exceptions.DeadlineExceeded", LLMTimeout),
        ("google.api_core.exceptions.RetryError", LLMTimeout),
        ("google.api_core.exceptions.ServiceUnavailable", LLMUnavailable),
        ("google.api_core.exceptions.InternalServerError", LLMUnavailable),
        ("google.api_core.exceptions.GatewayTimeout", LLMUnavailable),
        ("google.api_core.exceptions.Aborted", LLMUnavailable),
        ("google.auth.exceptions.RefreshError", LLMUnavailable),
        ("google.auth.exceptions.DefaultCredentialsError", LLMUnavailable),
        ("google.api_core.exceptions.InvalidArgument", LLMBadResponse),
        ("google.api_core.exceptions.PermissionDenied", LLMBadResponse),
        ("google.api_core.exceptions.Unauthenticated", LLMBadResponse),
        ("google.api_core.exceptions.NotFound", LLMBadResponse),
        ("google.api_core.exceptions.FailedPrecondition", LLMBadResponse),
        # Anything we don't know about defaults to Unavailable.
        ("some.unknown.module.MysteryError", LLMUnavailable),
    ],
)
def test_translate_vertex_error_matrix(qualname: str, expected_cls: type) -> None:
    exc = _make_vertex_exception(qualname, message="x")
    translated = _translate_vertex_error(exc, project=_TEST_PROJECT)
    assert isinstance(translated, expected_cls), (qualname, type(translated))


def test_translate_vertex_bad_request_uses_vertex_error_subclass() -> None:
    exc = _make_vertex_exception("google.api_core.exceptions.InvalidArgument")
    translated = _translate_vertex_error(exc, project=_TEST_PROJECT)
    assert isinstance(translated, VertexError)


# ── complete() ──────────────────────────────────────────────────────────────


async def test_complete_returns_text_from_fake() -> None:
    fake = FakeVertexGenerativeModel(reply="hello from vertex")
    client, _ = _make_client(model=fake)
    out = await client.complete([Message(role="user", content="hi")])
    assert out == "hello from vertex"
    assert len(fake.calls) == 1
    assert fake.calls[0]["stream"] is False
    assert fake.calls[0]["generation_config"] == {"temperature": 0.2}


async def test_complete_respects_explicit_temperature() -> None:
    fake = FakeVertexGenerativeModel()
    client, _ = _make_client(model=fake)
    await client.complete([Message(role="user", content="hi")], temperature=0.9)
    assert fake.calls[0]["generation_config"] == {"temperature": 0.9}


async def test_complete_omits_max_output_tokens_when_none() -> None:
    """spec-0014 M5: max_tokens=None must not add max_output_tokens to the
    generation_config, so every existing (pre-M5) caller's request is unchanged."""
    fake = FakeVertexGenerativeModel()
    client, _ = _make_client(model=fake)
    await client.complete([Message(role="user", content="hi")])
    assert fake.calls[0]["generation_config"] == {"temperature": 0.2}


async def test_complete_includes_max_output_tokens_when_given() -> None:
    fake = FakeVertexGenerativeModel()
    client, _ = _make_client(model=fake)
    await client.complete(
        [Message(role="user", content="hi")],
        temperature=0.9,
        max_tokens=TEST_MAX_TOKENS_OVERRIDE,
    )
    assert fake.calls[0]["generation_config"] == {
        "temperature": 0.9,
        "max_output_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_complete_raises_vertex_error_on_empty_text() -> None:
    fake = FakeVertexGenerativeModel(reply="")
    client, _ = _make_client(model=fake)
    with pytest.raises(VertexError):
        await client.complete([Message(role="user", content="hi")])


async def test_complete_translates_sdk_exception() -> None:
    fake = FakeVertexGenerativeModel()
    fake.raise_on_call = _make_vertex_exception("google.api_core.exceptions.DeadlineExceeded")
    client, _ = _make_client(model=fake)
    with pytest.raises(LLMTimeout):
        await client.complete([Message(role="user", content="hi")])


async def test_complete_logs_error_event_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeVertexGenerativeModel()
    fake.raise_on_call = _make_vertex_exception("google.api_core.exceptions.ServiceUnavailable")
    client, _ = _make_client(model=fake)
    with (
        caplog.at_level(logging.ERROR, logger="mangomas.adapters.llm.vertex"),
        pytest.raises(LLMUnavailable),
    ):
        await client.complete([Message(role="user", content="hi")])
    matching = [rec for rec in caplog.records if getattr(rec, "event", None) == "vertex_error"]
    assert matching, "expected a structured vertex_error log record"
    assert getattr(matching[0], "error_type", None) == "ServiceUnavailable"


# ── ping() ──────────────────────────────────────────────────────────────────


async def test_ping_invokes_minimal_completion() -> None:
    fake = FakeVertexGenerativeModel()
    client, _ = _make_client(model=fake)
    await client.ping()
    assert len(fake.calls) == 1
    assert fake.calls[0]["generation_config"] == {
        "temperature": 0.0,
        "max_output_tokens": 1,
    }


async def test_ping_translates_sdk_exception() -> None:
    fake = FakeVertexGenerativeModel()
    fake.raise_on_call = _make_vertex_exception("google.api_core.exceptions.PermissionDenied")
    client, _ = _make_client(model=fake)
    with pytest.raises(VertexError):
        await client.ping()


# ── stream() ────────────────────────────────────────────────────────────────


async def test_stream_yields_chunks_in_order() -> None:
    fake = FakeVertexGenerativeModel(chunks=["foo", "bar", "baz"])
    client, _ = _make_client(model=fake)
    stream = await client.stream([Message(role="user", content="hi")])
    collected = [tok async for tok in stream]
    assert collected == ["foo", "bar", "baz"]


async def test_stream_skips_empty_chunks() -> None:
    fake = FakeVertexGenerativeModel(chunks=["foo", "", "bar"])
    client, _ = _make_client(model=fake)
    stream = await client.stream([Message(role="user", content="hi")])
    collected = [tok async for tok in stream]
    assert collected == ["foo", "bar"]


async def test_stream_includes_max_output_tokens_when_given() -> None:
    fake = FakeVertexGenerativeModel(chunks=["foo"])
    client, _ = _make_client(model=fake)
    stream = await client.stream(
        [Message(role="user", content="hi")], max_tokens=TEST_MAX_TOKENS_OVERRIDE
    )
    _ = [tok async for tok in stream]
    assert fake.calls[0]["generation_config"] == {
        "temperature": 0.2,
        "max_output_tokens": TEST_MAX_TOKENS_OVERRIDE,
    }


async def test_stream_translates_sdk_exception_on_start() -> None:
    fake = FakeVertexGenerativeModel()
    fake.raise_on_call = _make_vertex_exception("google.api_core.exceptions.Aborted")
    client, _ = _make_client(model=fake)
    with pytest.raises(LLMUnavailable):
        stream = await client.stream([Message(role="user", content="hi")])
        async for _tok in stream:  # pragma: no cover - generator should not yield
            pass


# ── Timeout budget (MANGOMAS_LLM__TIMEOUT_SECONDS) ──────────────────────────
# A budget far below the stub's hang, so the timeout fires fast and
# deterministically; the stub's sleep is cancelled by the expiring
# `asyncio.timeout`, so its nominal length is never actually waited out.
# Module-private for the same reason `_TEST_PROJECT` is: these are test-scoped
# values with no config counterpart to re-export.
_TINY_TIMEOUT_SECONDS: float = 0.02
_HANGING_CALL_SECONDS: float = 5.0


class _HangingVertexModel:
    """Stub ``GenerativeModel`` whose calls outlive any sane request budget.

    Local to this module rather than a flag on :class:`FakeVertexGenerativeModel`
    because it models the *upstream SDK going unresponsive* — the one condition
    ``timeout_seconds`` exists to bound — and never returns a usable response.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_content_async(
        self,
        contents: Any,
        *,
        generation_config: dict[str, Any] | None = None,
        stream: bool = False,
        **_: Any,
    ) -> Any:
        self.calls.append(
            {"contents": contents, "generation_config": generation_config, "stream": stream}
        )
        await asyncio.sleep(_HANGING_CALL_SECONDS)
        # Unreachable once the adapter applies its budget: the sleep above is
        # cancelled first. Raising keeps an un-bounded adapter honest instead of
        # letting it pass on a late-but-valid reply.
        raise AssertionError("hanging stub was not cancelled by the adapter's timeout")


def _timed_client(stub: Any, *, timeout_seconds: float = _TINY_TIMEOUT_SECONDS) -> VertexClient:
    """Build a client around *stub* with an explicit request budget."""
    return VertexClient(
        project_id=_TEST_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=_TEST_MODEL,
        timeout_seconds=timeout_seconds,
        client=stub,
    )


async def test_complete_applies_configured_timeout() -> None:
    """``timeout_seconds`` bounds ``complete``'s outbound call.

    Before the fix the constructor stored ``timeout_seconds`` and no call site
    ever read it, so this waited out the stub's full hang and surfaced the
    stub's own failure as ``LLMUnavailable`` — ``MANGOMAS_LLM__TIMEOUT_SECONDS``
    was silently ignored by the Vertex provider while LM Studio honoured it.
    """
    stub = _HangingVertexModel()
    client = _timed_client(stub)
    started = time.monotonic()
    with pytest.raises(LLMTimeout) as excinfo:
        await client.complete([Message(role="user", content="hi")])
    elapsed = time.monotonic() - started
    assert elapsed < _HANGING_CALL_SECONDS, "adapter waited out the stub instead of cancelling it"
    assert len(stub.calls) == 1
    # The budget named in the error is the constructor's value, not a literal.
    assert str(_TINY_TIMEOUT_SECONDS) in excinfo.value.detail


async def test_ping_applies_configured_timeout() -> None:
    """The readiness probe is bounded too — an unbounded ping stalls /readyz."""
    stub = _HangingVertexModel()
    client = _timed_client(stub)
    started = time.monotonic()
    with pytest.raises(LLMTimeout):
        await client.ping()
    assert time.monotonic() - started < _HANGING_CALL_SECONDS
    assert len(stub.calls) == 1


async def test_stream_applies_configured_timeout_to_opening_request() -> None:
    """The budget bounds the opening stream request (not the drain)."""
    stub = _HangingVertexModel()
    client = _timed_client(stub)
    started = time.monotonic()
    with pytest.raises(LLMTimeout):
        stream = await client.stream([Message(role="user", content="hi")])
        async for _tok in stream:  # pragma: no cover - generator must not yield
            pass
    assert time.monotonic() - started < _HANGING_CALL_SECONDS
    assert len(stub.calls) == 1
    assert stub.calls[0]["stream"] is True


async def test_prompt_reply_is_unaffected_by_the_budget() -> None:
    """The other half of the guard: a call inside budget must not be cancelled."""
    fake = FakeVertexGenerativeModel(reply="quick")
    client = _timed_client(fake)
    assert await client.complete([Message(role="user", content="hi")]) == "quick"


# ── aclose() ────────────────────────────────────────────────────────────────


async def test_aclose_invokes_owned_client_close() -> None:
    fake = FakeVertexGenerativeModel()
    client = VertexClient(
        project_id=_TEST_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=_TEST_MODEL,
        client=fake,
    )
    # Owned flag is False when client is injected — fake should NOT be closed.
    await client.aclose()
    assert fake.closed is False


async def test_aclose_with_owned_client_calls_close_if_present() -> None:
    """When the adapter owns the client, ``aclose`` invokes its close hook."""
    fake = FakeVertexGenerativeModel()
    client = VertexClient(
        project_id=_TEST_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=_TEST_MODEL,
        client=fake,
    )
    # Manually flip the owned flag for this test — production code only sets
    # this when constructing through the lazy-import path.
    client._owns_client = True
    await client.aclose()
    assert fake.closed is True


# ── Lazy import behaviour ───────────────────────────────────────────────────


def test_constructor_without_sdk_raises_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If no client is injected and the SDK is missing, raise ImportError."""
    # In-test import keeps the patched module local to this test so it doesn't
    # affect other tests that exercise the real lazy-import path.
    import mangomas.adapters.llm.vertex as vertex_module  # noqa: PLC0415

    def _fail_import() -> tuple[Any, Any, Any]:
        raise ImportError("vertexai not installed")

    monkeypatch.setattr(vertex_module, "_lazy_import_vertex", _fail_import)
    with pytest.raises(ImportError):
        VertexClient(
            project_id=_TEST_PROJECT,
            location=DEFAULT_VERTEX_LOCATION,
            model=_TEST_MODEL,
        )


# ── _build_contents fallback shape (no SDK installed) ───────────────────────


def test_build_contents_dict_fallback_for_injected_client() -> None:
    fake = FakeVertexGenerativeModel()
    client, _ = _make_client(model=fake)
    messages = [
        Message(role="system", content="be terse"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
        Message(role="tool", content="tool-result"),
    ]
    contents = client._build_contents(messages)
    assert contents == [
        {"role": "user", "content": "[system] be terse"},
        {"role": "user", "content": "hi"},
        {"role": "model", "content": "hello"},
        {"role": "user", "content": "[tool] tool-result"},
    ]


# ── Credentials resolution ──────────────────────────────────────────────────


def test_credentials_json_parse_error_raises_vertex_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # In-test import keeps the monkeypatched module local to this test.
    import mangomas.adapters.llm.vertex as vertex_module  # noqa: PLC0415

    monkeypatch.setattr(
        vertex_module,
        "_lazy_import_credentials",
        lambda: object,  # not used; JSON parse fails first
    )
    with pytest.raises(VertexError):
        VertexClient._resolve_credentials(
            credentials_path=None,
            credentials_json="this is not json",
        )


def test_resolve_credentials_returns_none_when_no_inputs() -> None:
    out = VertexClient._resolve_credentials(
        credentials_path=None,
        credentials_json=None,
    )
    assert out is None


class _StubCredentialsCls:
    """Stand-in for ``google.oauth2.service_account.Credentials``."""

    @classmethod
    def from_service_account_info(cls, info: dict[str, Any]) -> _StubCredentialsCls:
        instance = cls()
        instance.info = info  # type: ignore[attr-defined]
        return instance

    @classmethod
    def from_service_account_file(cls, path: str) -> _StubCredentialsCls:
        instance = cls()
        instance.path = path  # type: ignore[attr-defined]
        return instance


def test_resolve_credentials_uses_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import mangomas.adapters.llm.vertex as vertex_module  # noqa: PLC0415

    monkeypatch.setattr(vertex_module, "_lazy_import_credentials", lambda: _StubCredentialsCls)
    out = VertexClient._resolve_credentials(
        credentials_path=None,
        credentials_json='{"client_email": "x@y.z"}',
    )
    assert isinstance(out, _StubCredentialsCls)
    assert out.info == {"client_email": "x@y.z"}  # type: ignore[attr-defined]


def test_resolve_credentials_uses_file_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import mangomas.adapters.llm.vertex as vertex_module  # noqa: PLC0415

    monkeypatch.setattr(vertex_module, "_lazy_import_credentials", lambda: _StubCredentialsCls)
    out = VertexClient._resolve_credentials(
        credentials_path="/some/key.json",
        credentials_json=None,
    )
    assert isinstance(out, _StubCredentialsCls)
    assert out.path == "/some/key.json"  # type: ignore[attr-defined]


# ── _build_contents real-SDK branch ─────────────────────────────────────────


class _StubContent:
    def __init__(self, *, role: str, parts: list[Any]) -> None:
        self.role = role
        self.parts = parts


class _StubPart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, text: str) -> _StubPart:
        return cls(text)


def test_build_contents_real_sdk_branch() -> None:
    """When ``_Content`` and ``_Part`` are populated, build real Content objects."""
    fake = FakeVertexGenerativeModel()
    client, _ = _make_client(model=fake)
    client._Content = _StubContent
    client._Part = _StubPart
    out = client._build_contents(
        [Message(role="user", content="hi"), Message(role="system", content="be terse")]
    )
    assert len(out) == 2
    assert all(isinstance(c, _StubContent) for c in out)
    assert out[0].role == "user"
    assert out[0].parts[0].text == "hi"
    assert out[1].role == "user"
    assert out[1].parts[0].text == "[system] be terse"
