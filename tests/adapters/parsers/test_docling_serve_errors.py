"""Tests for docling-serve error handling, status translations, observability and canary checks."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import respx
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.adapters.parsers import DoclingServeParser
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import ConfigError, DocumentParseError
from tests.adapters.parsers.conftest import (
    _CONVERT_URL,
    _PARSER_LOGGER,
    _SOURCE_URL,
    _events,
    _ok,
    _parser,
    _rendered_record,
    _rendered_spans,
    _settings,
)
from tests.constants.docling import (
    DOCLING_FIXTURE_FAILURE,
    DOCLING_FIXTURE_SKIPPED,
    DOCLING_FIXTURE_SUCCESS,
    SPEC_DOCLING_CREDENTIALS_MESSAGE,
    SPEC_DOCLING_EVENT_COMPLETED,
    SPEC_DOCLING_EVENT_FAILED,
    SPEC_DOCLING_EVENT_PARTIAL,
    SPEC_DOCLING_SPAN_NAME,
    TEST_DOCLING_BODY_CANARY,
    TEST_DOCLING_DOC_CANARY,
    TEST_DOCLING_MAX_FILE_BYTES,
    TEST_DOCLING_MAX_RESPONSE_BYTES,
    TEST_DOCLING_ORIGINAL_STEM,
    TEST_DOCLING_PDF_BYTES,
    TEST_DOCLING_PDF_NAME,
    TEST_DOCLING_SENTINEL_KEY,
    load_docling_fixture,
)

# ── Status Mapping & Negative Protocol Cases ──────────────────────────────────


@respx.mock
async def test_success_does_not_warn_partial(caplog: pytest.LogCaptureFixture) -> None:
    """Two-sided: the partial warning is not emitted for a full success."""
    respx.post(_CONVERT_URL).mock(return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS)))

    with caplog.at_level(logging.DEBUG, logger=_PARSER_LOGGER):
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    events = [getattr(r, "event", None) for r in caplog.records]
    assert SPEC_DOCLING_EVENT_PARTIAL not in events
    assert SPEC_DOCLING_EVENT_COMPLETED in events


@pytest.mark.parametrize("fixture", [DOCLING_FIXTURE_FAILURE, DOCLING_FIXTURE_SKIPPED])
@respx.mock
async def test_failure_and_skipped_raise(fixture: str) -> None:
    body = load_docling_fixture(fixture)
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert body["status"] in excinfo.value.detail
    # The serve `errors` list is never echoed.
    for error in body["errors"]:
        assert error["error_message"] not in str(excinfo.value)
        assert error["error_message"] not in excinfo.value.detail


@pytest.mark.parametrize("status", ["pending", "started", "SUCCESS", 42, None])
@respx.mock
async def test_unknown_status_raises_typed(status: object) -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    body["status"] = status
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    with pytest.raises(DocumentParseError):
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)


@respx.mock
async def test_unknown_status_value_is_not_echoed_when_not_an_identifier() -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    body["status"] = f"weird {TEST_DOCLING_BODY_CANARY}"
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert TEST_DOCLING_BODY_CANARY not in excinfo.value.detail
    assert TEST_DOCLING_BODY_CANARY not in str(excinfo.value)


@respx.mock
async def test_empty_md_content_returns_empty_text() -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    body["document"]["md_content"] = ""
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    parsed = await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert parsed.text == ""


def _drop_document(body: dict[str, Any]) -> Any:
    del body["document"]
    return body


def _null_md(body: dict[str, Any]) -> Any:
    body["document"]["md_content"] = None
    return body


def _document_not_object(body: dict[str, Any]) -> Any:
    body["document"] = ["not", "an", "object"]
    return body


def _top_level_list(body: dict[str, Any]) -> Any:
    return [body]


@pytest.mark.parametrize(
    "mutate", [_drop_document, _null_md, _document_not_object, _top_level_list]
)
@respx.mock
async def test_missing_fields_raise_typed(mutate: Callable[[dict[str, Any]], Any]) -> None:
    body = mutate(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert len(excinfo.value.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE


# ── Transport Errors & HTTP Mappings ──────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(503, text="unavailable"),
        httpx.Response(404, text="missing"),
        httpx.Response(302, headers={"location": _SOURCE_URL}),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("peer closed"),
    ],
    ids=["500", "503", "404", "302", "read-timeout", "connect-timeout", "connect", "protocol"],
)
@respx.mock
async def test_5xx_timeout_connect_are_typed(outcome: httpx.Response | Exception) -> None:
    route = respx.post(_CONVERT_URL)
    if isinstance(outcome, Exception):
        route.mock(side_effect=outcome)
    else:
        route.mock(return_value=outcome)

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert not isinstance(excinfo.value, httpx.HTTPError)
    if isinstance(outcome, Exception):
        assert type(outcome).__name__ in excinfo.value.detail
    else:
        assert str(outcome.status_code) in excinfo.value.detail


@pytest.mark.parametrize("status_code", [401, 403])
@respx.mock
async def test_401_403_are_config_errors(status_code: int) -> None:
    respx.post(_CONVERT_URL).mock(
        return_value=httpx.Response(status_code, text=TEST_DOCLING_BODY_CANARY)
    )
    # The parser receives the resolved key explicitly (composition resolves it).
    parser = DoclingServeParser(
        _settings(auth_mode="api_key", api_key=TEST_DOCLING_SENTINEL_KEY),
        api_key=TEST_DOCLING_SENTINEL_KEY,
    )

    with pytest.raises(ConfigError) as excinfo:
        await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert str(excinfo.value) == SPEC_DOCLING_CREDENTIALS_MESSAGE
    assert not isinstance(excinfo.value, DocumentParseError)
    for rendered in (str(excinfo.value), repr(excinfo.value), excinfo.value.detail):
        assert TEST_DOCLING_SENTINEL_KEY not in rendered
        assert TEST_DOCLING_BODY_CANARY not in rendered


@respx.mock
async def test_malformed_json_truncated_detail() -> None:
    garbage = "<html>" + TEST_DOCLING_BODY_CANARY * 20
    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(200, text=garbage))

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert len(excinfo.value.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE
    assert TEST_DOCLING_BODY_CANARY not in excinfo.value.detail
    assert TEST_DOCLING_BODY_CANARY not in str(excinfo.value)


@respx.mock
async def test_malformed_shape_detail_names_keys_only_and_is_truncated() -> None:
    long_keys: dict[str, str] = {
        f"key_{i:03d}_{'x' * 20}": TEST_DOCLING_BODY_CANARY for i in range(40)
    }
    long_keys["status"] = "success"  # valid status, but no `document`
    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(200, json=long_keys))

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert len(excinfo.value.detail) <= DEFAULT_ERROR_DETAIL_TRUNCATE
    assert "key_000" in excinfo.value.detail
    assert TEST_DOCLING_BODY_CANARY not in excinfo.value.detail


# ── Limits before and during I/O ──────────────────────────────────────────────


@respx.mock
async def test_oversize_refused_without_network() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    parser = _parser()

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=b"x" * TEST_DOCLING_MAX_FILE_BYTES)
    assert route.call_count == 1

    with pytest.raises(DocumentParseError) as excinfo:
        await parser.parse(
            filename=TEST_DOCLING_PDF_NAME, content=b"x" * (TEST_DOCLING_MAX_FILE_BYTES + 1)
        )

    assert route.call_count == 1  # the n+1 attempt made no call
    assert str(TEST_DOCLING_MAX_FILE_BYTES) in excinfo.value.detail


@respx.mock
async def test_response_over_cap_refused_by_content_length() -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    body["document"]["md_content"] = "y" * (TEST_DOCLING_MAX_RESPONSE_BYTES + 1)
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert str(TEST_DOCLING_MAX_RESPONSE_BYTES) in excinfo.value.detail
    assert "y" * 10 not in excinfo.value.detail


@respx.mock
async def test_response_over_cap_refused_while_streaming() -> None:
    """No Content-Length (chunked): the streamed byte count enforces the cap."""
    chunk = b"z" * (TEST_DOCLING_MAX_RESPONSE_BYTES // 2)

    async def _chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield chunk

    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(200, content=_chunks()))

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert str(TEST_DOCLING_MAX_RESPONSE_BYTES) in excinfo.value.detail


# ── Observability & Secrets Hygiene ───────────────────────────────────────────


@respx.mock
async def test_completed_event_carries_allow_listed_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.post(_CONVERT_URL).mock(return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS)))

    with caplog.at_level(logging.DEBUG, logger=_PARSER_LOGGER):
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    [record] = _events(caplog, SPEC_DOCLING_EVENT_COMPLETED)
    assert record.levelno == logging.DEBUG
    assert record.suffix == ".pdf"  # type: ignore[attr-defined]
    assert record.bytes == len(TEST_DOCLING_PDF_BYTES)  # type: ignore[attr-defined]
    assert record.status == "success"  # type: ignore[attr-defined]
    assert isinstance(record.elapsed_ms, float)  # type: ignore[attr-defined]


@respx.mock
async def test_failed_event_is_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(500))

    with (
        caplog.at_level(logging.DEBUG, logger=_PARSER_LOGGER),
        pytest.raises(DocumentParseError),
    ):
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    [record] = _events(caplog, SPEC_DOCLING_EVENT_FAILED)
    assert record.levelno == logging.WARNING
    assert record.error_code == DocumentParseError.code  # type: ignore[attr-defined]
    assert record.error_class == DocumentParseError.__name__  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            json={
                "document": {"md_content": TEST_DOCLING_DOC_CANARY},
                "status": "success",
            },
        ),
        httpx.Response(
            200,
            json={
                "document": {"md_content": TEST_DOCLING_DOC_CANARY},
                "status": "partial_success",
            },
        ),
        httpx.Response(500, text=TEST_DOCLING_BODY_CANARY),
        httpx.Response(401, text=TEST_DOCLING_BODY_CANARY),
        httpx.Response(200, text=f"not json {TEST_DOCLING_BODY_CANARY}"),
        httpx.Response(
            200,
            json={"status": "failure", "errors": [{"error_message": TEST_DOCLING_BODY_CANARY}]},
        ),
    ],
    ids=["success", "partial", "500", "401", "malformed", "failure"],
)
@respx.mock
async def test_secret_never_in_logs_errors_or_spans(
    response: httpx.Response,
    caplog: pytest.LogCaptureFixture,
    span_exporter: InMemorySpanExporter,
) -> None:
    respx.post(_CONVERT_URL).mock(return_value=response)
    parser = DoclingServeParser(
        _settings(auth_mode="api_key", api_key=TEST_DOCLING_SENTINEL_KEY),
        api_key=TEST_DOCLING_SENTINEL_KEY,
    )
    document = f"%PDF {TEST_DOCLING_DOC_CANARY}".encode()

    rendered: list[str] = []
    with caplog.at_level(logging.DEBUG):
        try:
            await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=document)
        except (DocumentParseError, ConfigError) as exc:
            rendered += [str(exc), repr(exc), exc.detail]
    rendered += [_rendered_record(r) for r in caplog.records]
    rendered.append(_rendered_spans(span_exporter))
    rendered.append(repr(parser))

    blob = "\n".join(rendered)
    for secret in (
        TEST_DOCLING_SENTINEL_KEY,
        TEST_DOCLING_DOC_CANARY,
        TEST_DOCLING_BODY_CANARY,
        TEST_DOCLING_ORIGINAL_STEM,
    ):
        assert secret not in blob, f"{secret!r} leaked"
    assert SPEC_DOCLING_SPAN_NAME in blob  # the span really was captured


@respx.mock
async def test_span_carries_suffix_bytes_and_status_only(
    span_exporter: InMemorySpanExporter,
) -> None:
    respx.post(_CONVERT_URL).mock(return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS)))

    await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    [span] = [s for s in span_exporter.get_finished_spans() if s.name == SPEC_DOCLING_SPAN_NAME]
    assert dict(span.attributes or {}) == {
        "parser.suffix": ".pdf",
        "parser.bytes": len(TEST_DOCLING_PDF_BYTES),
        "parser.status": "success",
    }


@respx.mock
async def test_failed_span_records_error_code(span_exporter: InMemorySpanExporter) -> None:
    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(DocumentParseError):
        await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    [span] = [s for s in span_exporter.get_finished_spans() if s.name == SPEC_DOCLING_SPAN_NAME]
    attributes = dict(span.attributes or {})
    assert attributes["error.code"] == DocumentParseError.code
    assert span.status.status_code == trace.StatusCode.ERROR
