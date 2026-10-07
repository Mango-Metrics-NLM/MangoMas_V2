"""Tests for the docling-serve parser adapter (spec-0035 R3/R4/R5/R11, respx-mocked)."""

from __future__ import annotations

import base64
import io
import json
import logging
import sys
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any

import httpx
import pytest
import respx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.adapters.parsers import DoclingServeParser, DocumentParser
from mangomas.adapters.parsers._auth import (
    GOOGLE_AUTH_INSTALL_HINT,
    GoogleIdTokenProvider,
    IdTokenProvider,
    id_token_expiry,
)
from mangomas.adapters.parsers.docling_serve import check_ooxml_archive
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, ParserSettings
from mangomas.errors import ConfigError, DocumentParseError
from tests.constants.docling import (
    DOCLING_FIXTURE_FAILURE,
    DOCLING_FIXTURE_PARTIAL,
    DOCLING_FIXTURE_SKIPPED,
    DOCLING_FIXTURE_SUCCESS,
    SPEC_DOCLING_API_KEY_HEADER,
    SPEC_DOCLING_CONVERT_PATH,
    SPEC_DOCLING_CREDENTIALS_MESSAGE,
    SPEC_DOCLING_DO_OCR_FIELD,
    SPEC_DOCLING_DOCUMENT_TIMEOUT_FIELD,
    SPEC_DOCLING_EVENT_COMPLETED,
    SPEC_DOCLING_EVENT_FAILED,
    SPEC_DOCLING_EVENT_PARTIAL,
    SPEC_DOCLING_FILES_FIELD,
    SPEC_DOCLING_FORBIDDEN_FIELD,
    SPEC_DOCLING_FORMATS,
    SPEC_DOCLING_FROM_FORMATS_FIELD,
    SPEC_DOCLING_MAX_NUM_PAGES_FIELD,
    SPEC_DOCLING_SOURCE_PATH,
    SPEC_DOCLING_SPAN_NAME,
    SPEC_DOCLING_TO_FORMAT_MD,
    SPEC_DOCLING_TO_FORMATS_FIELD,
    TEST_DOCLING_AUDIENCE,
    TEST_DOCLING_BASE_URL,
    TEST_DOCLING_BODY_CANARY,
    TEST_DOCLING_DOC_CANARY,
    TEST_DOCLING_DOCX_NAME,
    TEST_DOCLING_GENERATED_PDF_NAME,
    TEST_DOCLING_HTML_NAME,
    TEST_DOCLING_MAX_FILE_BYTES,
    TEST_DOCLING_MAX_RESPONSE_BYTES,
    TEST_DOCLING_MAX_ZIP_ENTRIES,
    TEST_DOCLING_MAX_ZIP_RATIO,
    TEST_DOCLING_NO_SUFFIX_NAME,
    TEST_DOCLING_ORIGINAL_STEM,
    TEST_DOCLING_PAGES,
    TEST_DOCLING_PAGES_FIELD,
    TEST_DOCLING_PDF_BYTES,
    TEST_DOCLING_PDF_NAME,
    TEST_DOCLING_SENTINEL_KEY,
    TEST_DOCLING_UNKNOWN_NAME,
    TEST_DOCLING_UNRESOLVED_REF,
    TEST_DOCLING_UPPER_PDF_NAME,
    TEST_DOCLING_ZIP_BOMB_PAYLOAD_BYTES,
    TEST_DOCLING_ZIP_MEMBER,
    TEST_DOCLING_ZIP_SMALL_MEMBER,
    TEST_ID_TOKEN_FIRST,
    TEST_ID_TOKEN_LIFETIME,
    TEST_ID_TOKEN_NOW,
    TEST_ID_TOKEN_REFRESH_MARGIN,
    TEST_ID_TOKEN_SECOND,
    load_docling_fixture,
)
from tests.fakes import FakeIdTokenProvider

_CONVERT_URL = f"{TEST_DOCLING_BASE_URL}{SPEC_DOCLING_CONVERT_PATH}"
_SOURCE_URL = f"{TEST_DOCLING_BASE_URL}{SPEC_DOCLING_SOURCE_PATH}"
_PARSER_LOGGER = "mangomas.adapters.parsers.docling_serve"
_MAX_ENTRIES = TEST_DOCLING_MAX_ZIP_ENTRIES


# ── Helpers ───────────────────────────────────────────────────────────────────


def _settings(**overrides: Any) -> ParserSettings:
    values: dict[str, Any] = {
        "enabled": True,
        "base_url": TEST_DOCLING_BASE_URL,
        "max_file_bytes": TEST_DOCLING_MAX_FILE_BYTES,
        "max_response_bytes": TEST_DOCLING_MAX_RESPONSE_BYTES,
        "max_zip_entries": TEST_DOCLING_MAX_ZIP_ENTRIES,
        "max_zip_ratio": TEST_DOCLING_MAX_ZIP_RATIO,
    }
    values.update(overrides)
    return ParserSettings(**values)


def _parser(**overrides: Any) -> DoclingServeParser:
    return DoclingServeParser(_settings(**overrides))


def _ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event]


class _Clock:
    """A settable clock for the token cache."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _multipart(request: httpx.Request) -> EmailMessage:
    """Parse a captured multipart request body with the stdlib MIME parser."""
    body = request.read()
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + body)
    assert isinstance(message, EmailMessage)  # the HTTP policy builds EmailMessage
    return message


def _form_fields(request: httpx.Request) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for part in _multipart(request).iter_parts():
        if part.get_filename() is not None:
            continue
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        fields.setdefault(name, []).append(payload.decode())
    return fields


def _file_parts(request: httpx.Request) -> list[tuple[str, str, bytes]]:
    files: list[tuple[str, str, bytes]] = []
    for part in _multipart(request).iter_parts():
        filename = part.get_filename()
        if filename is None:
            continue
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        files.append((name, filename, payload))
    return files


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _jwt(claims: dict[str, Any]) -> str:
    def seg(obj: dict[str, Any]) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.signature"


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Attach an in-memory exporter to the (set-once) global SDK provider."""
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    processor.shutdown()


# ── Protocol ──────────────────────────────────────────────────────────────────


def test_satisfies_document_parser_protocol() -> None:
    assert isinstance(_parser(), DocumentParser)


def test_google_provider_satisfies_id_token_protocol() -> None:
    assert isinstance(GoogleIdTokenProvider(), IdTokenProvider)


# ── Status mapping ────────────────────────────────────────────────────────────


@respx.mock
async def test_success_returns_text_and_pages() -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    body["document"][TEST_DOCLING_PAGES_FIELD] = TEST_DOCLING_PAGES
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    parsed = await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert parsed.text == body["document"]["md_content"]
    assert parsed.pages == TEST_DOCLING_PAGES
    assert parsed.partial is False


@respx.mock
async def test_success_without_page_count_reports_none() -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    parsed = await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert parsed.pages is None


@pytest.mark.parametrize("bad_pages", [True, "7", 7.0, None])
@respx.mock
async def test_non_integer_page_count_is_ignored(bad_pages: object) -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    body["document"][TEST_DOCLING_PAGES_FIELD] = bad_pages
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    parsed = await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert parsed.pages is None


@respx.mock
async def test_partial_success_sets_partial_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_PARTIAL)
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    with caplog.at_level(logging.WARNING, logger=_PARSER_LOGGER):
        parsed = await _parser().parse(
            filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES
        )

    assert parsed.partial is True
    assert parsed.text == body["document"]["md_content"]
    warnings = _events(caplog, SPEC_DOCLING_EVENT_PARTIAL)
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING


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


# ── Transport ─────────────────────────────────────────────────────────────────


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


# ── Limits before I/O ─────────────────────────────────────────────────────────


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


@respx.mock
async def test_response_at_cap_streamed_is_accepted() -> None:
    """Boundary: a chunked body of exactly the cap is read and parsed."""
    payload = json.dumps(load_docling_fixture(DOCLING_FIXTURE_SUCCESS)).encode()
    padded = payload + b" " * (TEST_DOCLING_MAX_RESPONSE_BYTES - len(payload))

    async def _chunks() -> AsyncIterator[bytes]:
        yield padded

    respx.post(_CONVERT_URL).mock(return_value=httpx.Response(200, content=_chunks()))

    parsed = await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert parsed.text


@respx.mock
async def test_zip_bomb_refused_without_network() -> None:
    route = respx.post(_CONVERT_URL)
    bomb = _zip({TEST_DOCLING_ZIP_MEMBER: b"\0" * TEST_DOCLING_ZIP_BOMB_PAYLOAD_BYTES})
    parser = _parser(max_file_bytes=len(bomb))

    with pytest.raises(DocumentParseError) as excinfo:
        await parser.parse(filename=TEST_DOCLING_DOCX_NAME, content=bomb)

    assert route.call_count == 0
    assert "ratio" in excinfo.value.detail


@respx.mock
async def test_too_many_entries_refused_without_network() -> None:
    route = respx.post(_CONVERT_URL)
    archive = _zip(
        {
            f"p{i}.xml": TEST_DOCLING_ZIP_SMALL_MEMBER
            for i in range(TEST_DOCLING_MAX_ZIP_ENTRIES + 1)
        }
    )
    parser = _parser(max_file_bytes=len(archive), max_zip_ratio=1_000.0)

    with pytest.raises(DocumentParseError) as excinfo:
        await parser.parse(filename=TEST_DOCLING_DOCX_NAME, content=archive)

    assert route.call_count == 0
    assert str(TEST_DOCLING_MAX_ZIP_ENTRIES) in excinfo.value.detail


@respx.mock
async def test_non_zip_docx_refused_without_network() -> None:
    route = respx.post(_CONVERT_URL)

    with pytest.raises(DocumentParseError):
        await _parser().parse(filename=TEST_DOCLING_DOCX_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert route.call_count == 0


@respx.mock
async def test_well_formed_docx_is_sent_with_its_format() -> None:
    """Two-sided for the archive guard: a benign OOXML archive goes through."""
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    archive = _zip({TEST_DOCLING_ZIP_MEMBER: TEST_DOCLING_ZIP_SMALL_MEMBER})
    parser = _parser(max_file_bytes=len(archive), max_zip_ratio=1_000.0)

    await parser.parse(filename=TEST_DOCLING_DOCX_NAME, content=archive)

    assert route.call_count == 1
    fields = _form_fields(route.calls.last.request)
    assert fields[SPEC_DOCLING_FROM_FORMATS_FIELD] == [SPEC_DOCLING_FORMATS[".docx"]]


def test_check_ooxml_archive_accepts_empty_archive() -> None:
    check_ooxml_archive(
        _zip({}),
        max_entries=TEST_DOCLING_MAX_ZIP_ENTRIES,
        max_ratio=TEST_DOCLING_MAX_ZIP_RATIO,
    )


def test_check_ooxml_archive_accepts_stored_empty_member() -> None:
    """Zero compressed and zero uncompressed bytes is ratio 0, not a division error."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(TEST_DOCLING_ZIP_MEMBER, b"")
    check_ooxml_archive(buffer.getvalue(), max_entries=TEST_DOCLING_MAX_ZIP_ENTRIES, max_ratio=1.0)


def test_check_ooxml_archive_at_entry_limit_is_accepted() -> None:
    """Boundary: exactly ``max_entries`` members pass; one more is refused."""
    members = {f"p{i}.xml": TEST_DOCLING_ZIP_SMALL_MEMBER for i in range(_MAX_ENTRIES)}
    check_ooxml_archive(_zip(members), max_entries=_MAX_ENTRIES, max_ratio=1_000.0)


@pytest.mark.parametrize("filename", [TEST_DOCLING_UNKNOWN_NAME, TEST_DOCLING_NO_SUFFIX_NAME])
@respx.mock
async def test_unknown_suffix_refused_without_network(filename: str) -> None:
    route = respx.post(_CONVERT_URL)

    with pytest.raises(DocumentParseError) as excinfo:
        await _parser().parse(filename=filename, content=TEST_DOCLING_PDF_BYTES)

    assert route.call_count == 0
    assert TEST_DOCLING_ORIGINAL_STEM not in str(excinfo.value)
    assert TEST_DOCLING_ORIGINAL_STEM not in excinfo.value.detail


@respx.mock
async def test_mapped_suffix_outside_allow_list_refused() -> None:
    """`.html` has a docling format but is excluded by default (ADR-0036 §7)."""
    route = respx.post(_CONVERT_URL)

    with pytest.raises(DocumentParseError):
        await _parser().parse(filename=TEST_DOCLING_HTML_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert route.call_count == 0


@respx.mock
async def test_mapped_suffix_inside_allow_list_is_sent() -> None:
    """Two-sided: an operator who allows `.html` gets it sent, format pinned."""
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    parser = _parser(allowed_suffixes=(".pdf", ".html"))

    await parser.parse(filename=TEST_DOCLING_HTML_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert route.call_count == 1
    assert _form_fields(route.calls.last.request)[SPEC_DOCLING_FROM_FORMATS_FIELD] == ["html"]


# ── Request hygiene ───────────────────────────────────────────────────────────


@respx.mock
async def test_request_hygiene() -> None:
    convert = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    source = respx.post(_SOURCE_URL)
    settings = _settings(do_ocr=True)

    await DoclingServeParser(settings).parse(
        filename=TEST_DOCLING_UPPER_PDF_NAME, content=TEST_DOCLING_PDF_BYTES
    )

    assert convert.call_count == 1
    assert source.call_count == 0
    assert respx.calls.call_count == 1
    request = convert.calls.last.request
    assert request.url.path == SPEC_DOCLING_CONVERT_PATH

    files = _file_parts(request)
    assert files == [
        (SPEC_DOCLING_FILES_FIELD, TEST_DOCLING_GENERATED_PDF_NAME, TEST_DOCLING_PDF_BYTES)
    ]
    raw = request.read()
    assert TEST_DOCLING_ORIGINAL_STEM.encode() not in raw
    assert TEST_DOCLING_ORIGINAL_STEM not in str(request.url)

    fields = _form_fields(request)
    assert fields[SPEC_DOCLING_FROM_FORMATS_FIELD] == [SPEC_DOCLING_FORMATS[".pdf"]]
    assert fields[SPEC_DOCLING_TO_FORMATS_FIELD] == [SPEC_DOCLING_TO_FORMAT_MD]
    assert fields[SPEC_DOCLING_DO_OCR_FIELD] == ["true"]
    timeout = float(fields[SPEC_DOCLING_DOCUMENT_TIMEOUT_FIELD][0])
    assert timeout == settings.document_timeout_seconds
    assert int(fields[SPEC_DOCLING_MAX_NUM_PAGES_FIELD][0]) == settings.max_pages
    assert SPEC_DOCLING_FORBIDDEN_FIELD not in fields


@pytest.mark.parametrize(("suffix", "docling_format"), sorted(SPEC_DOCLING_FORMATS.items()))
@respx.mock
async def test_from_formats_pinned_to_suffix(suffix: str, docling_format: str) -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    content = (
        _zip({TEST_DOCLING_ZIP_MEMBER: TEST_DOCLING_ZIP_SMALL_MEMBER})
        if suffix != ".pdf"
        else TEST_DOCLING_PDF_BYTES
    )
    parser = _parser(max_file_bytes=max(len(content), 1), max_zip_ratio=1_000.0)

    await parser.parse(filename=f"{TEST_DOCLING_ORIGINAL_STEM}{suffix}", content=content)

    request = route.calls.last.request
    assert _form_fields(request)[SPEC_DOCLING_FROM_FORMATS_FIELD] == [docling_format]
    assert _file_parts(request)[0][1] == f"document{suffix}"


@respx.mock
async def test_do_ocr_false_is_sent_as_false() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )

    parser = _parser(do_ocr=False)
    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert _form_fields(route.calls.last.request)[SPEC_DOCLING_DO_OCR_FIELD] == ["false"]


@respx.mock
async def test_trailing_slash_base_url_is_normalised() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )

    await _parser(base_url=f"{TEST_DOCLING_BASE_URL}/").parse(
        filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES
    )

    assert route.call_count == 1


# ── Authentication ────────────────────────────────────────────────────────────


@respx.mock
async def test_api_key_header_sent_in_api_key_mode() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    parser = DoclingServeParser(
        _settings(auth_mode="api_key", api_key=TEST_DOCLING_SENTINEL_KEY),
        api_key=TEST_DOCLING_SENTINEL_KEY,
    )

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    headers = route.calls.last.request.headers
    assert headers[SPEC_DOCLING_API_KEY_HEADER] == TEST_DOCLING_SENTINEL_KEY
    assert "authorization" not in headers


@respx.mock
async def test_no_auth_header_in_none_mode_even_with_a_key() -> None:
    """Two-sided: `none` mode never sends a key, even if one is supplied."""
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    parser = DoclingServeParser(_settings(), api_key=TEST_DOCLING_SENTINEL_KEY)

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    headers = route.calls.last.request.headers
    assert SPEC_DOCLING_API_KEY_HEADER not in headers
    assert "authorization" not in headers
    assert TEST_DOCLING_SENTINEL_KEY.encode() not in route.calls.last.request.read()


def test_api_key_mode_without_key_is_a_config_error() -> None:
    # Settings accept a secret_ref; composition resolved nothing → no key.
    settings = _settings(auth_mode="api_key", secret_ref=TEST_DOCLING_UNRESOLVED_REF)

    with pytest.raises(ConfigError):
        DoclingServeParser(settings, api_key=None)


@respx.mock
async def test_id_token_bearer_header_cached_then_refreshed() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(
        tokens=[TEST_ID_TOKEN_FIRST, TEST_ID_TOKEN_SECOND],
        clock=clock,
        lifetime=TEST_ID_TOKEN_LIFETIME,
    )
    parser = DoclingServeParser(
        _settings(
            auth_mode="google_id_token",
            id_token_refresh_margin_seconds=TEST_ID_TOKEN_REFRESH_MARGIN,
        ),
        api_key=TEST_DOCLING_SENTINEL_KEY,  # ignored in this mode
        token_provider=provider,
        clock=clock,
    )
    refresh_at = TEST_ID_TOKEN_NOW + TEST_ID_TOKEN_LIFETIME - TEST_ID_TOKEN_REFRESH_MARGIN

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)
    clock.now = refresh_at - 1
    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)
    assert len(provider.audiences) == 1  # cached inside the margin

    clock.now = refresh_at
    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)
    assert len(provider.audiences) == 2  # re-minted at the margin

    sent = [call.request.headers["authorization"] for call in route.calls]
    assert sent == [
        f"Bearer {TEST_ID_TOKEN_FIRST}",
        f"Bearer {TEST_ID_TOKEN_FIRST}",
        f"Bearer {TEST_ID_TOKEN_SECOND}",
    ]
    for call in route.calls:
        assert SPEC_DOCLING_API_KEY_HEADER not in call.request.headers
    # Audience defaults to the (normalised) base URL.
    assert provider.audiences == [TEST_DOCLING_BASE_URL, TEST_DOCLING_BASE_URL]


@respx.mock
async def test_id_token_uses_explicit_audience() -> None:
    respx.post(_CONVERT_URL).mock(return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS)))
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(
        tokens=[TEST_ID_TOKEN_FIRST], clock=clock, lifetime=TEST_ID_TOKEN_LIFETIME
    )
    parser = DoclingServeParser(
        _settings(auth_mode="google_id_token", id_token_audience=TEST_DOCLING_AUDIENCE),
        token_provider=provider,
        clock=clock,
    )

    await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert provider.audiences == [TEST_DOCLING_AUDIENCE]


@respx.mock
async def test_id_token_provider_failure_is_config_error_without_network() -> None:
    route = respx.post(_CONVERT_URL)
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(
        tokens=[],
        clock=clock,
        lifetime=TEST_ID_TOKEN_LIFETIME,
        error=RuntimeError(TEST_DOCLING_SENTINEL_KEY),
    )
    parser = DoclingServeParser(
        _settings(auth_mode="google_id_token"), token_provider=provider, clock=clock
    )

    with pytest.raises(ConfigError) as excinfo:
        await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert route.call_count == 0
    assert TEST_DOCLING_SENTINEL_KEY not in str(excinfo.value)
    assert TEST_DOCLING_SENTINEL_KEY not in excinfo.value.detail
    assert "RuntimeError" in excinfo.value.detail


@respx.mock
async def test_id_token_provider_returning_empty_token_is_config_error() -> None:
    clock = _Clock(TEST_ID_TOKEN_NOW)
    provider = FakeIdTokenProvider(tokens=[""], clock=clock, lifetime=TEST_ID_TOKEN_LIFETIME)
    parser = DoclingServeParser(
        _settings(auth_mode="google_id_token"), token_provider=provider, clock=clock
    )

    with pytest.raises(ConfigError):
        await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)


def test_google_id_token_mode_defaults_to_the_google_provider() -> None:
    parser = _parser(auth_mode="google_id_token")
    assert isinstance(parser.token_provider, GoogleIdTokenProvider)


def test_non_id_token_modes_hold_no_token_provider() -> None:
    assert _parser().token_provider is None


# ── GoogleIdTokenProvider (fetcher injected; the SDK path is extra-gated) ────


async def test_google_provider_reads_expiry_from_the_jwt() -> None:
    expiry = TEST_ID_TOKEN_NOW + TEST_ID_TOKEN_LIFETIME
    token = _jwt({"exp": expiry, "aud": TEST_DOCLING_AUDIENCE})
    seen: list[str] = []

    def _fetcher(audience: str) -> str:
        seen.append(audience)
        return token

    minted, minted_expiry = await GoogleIdTokenProvider(fetcher=_fetcher).fetch(
        TEST_DOCLING_AUDIENCE
    )

    assert (minted, minted_expiry) == (token, expiry)
    assert seen == [TEST_DOCLING_AUDIENCE]


async def test_google_provider_wraps_fetch_failure_by_class_name_only() -> None:
    def _fetcher(audience: str) -> str:  # noqa: ARG001
        raise ValueError(TEST_DOCLING_SENTINEL_KEY)

    with pytest.raises(ConfigError) as excinfo:
        await GoogleIdTokenProvider(fetcher=_fetcher).fetch(TEST_DOCLING_AUDIENCE)

    assert excinfo.value.detail == "ValueError"
    assert TEST_DOCLING_SENTINEL_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    "missing", ["google.oauth2", "google.auth.transport"], ids=["oauth2", "transport"]
)
async def test_google_provider_without_sdk_names_the_gcp_extra(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """No google-auth installed → ConfigError with the install hint, at first use."""
    monkeypatch.setitem(sys.modules, missing, None)

    with pytest.raises(ConfigError) as excinfo:
        await GoogleIdTokenProvider().fetch(TEST_DOCLING_AUDIENCE)

    assert str(excinfo.value) == GOOGLE_AUTH_INSTALL_HINT
    assert "pip install 'mangomas[gcp]'" in str(excinfo.value)


@respx.mock
async def test_id_token_mode_without_sdk_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the default provider: no SDK → ConfigError, zero calls."""
    monkeypatch.setitem(sys.modules, "google.oauth2", None)
    route = respx.post(_CONVERT_URL)

    with pytest.raises(ConfigError) as excinfo:
        await _parser(auth_mode="google_id_token").parse(
            filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES
        )

    assert "mangomas[gcp]" in str(excinfo.value)
    assert route.call_count == 0


async def test_google_provider_passes_config_errors_through() -> None:
    hint = ConfigError("install the extra")

    def _fetcher(audience: str) -> str:  # noqa: ARG001
        raise hint

    with pytest.raises(ConfigError) as excinfo:
        await GoogleIdTokenProvider(fetcher=_fetcher).fetch(TEST_DOCLING_AUDIENCE)

    assert excinfo.value is hint


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        "a.!!!.c",
        f"a.{base64.urlsafe_b64encode(b'[1, 2]').decode()}.c",
        _jwt({"aud": TEST_DOCLING_AUDIENCE}),
        _jwt({"exp": "soon"}),
        _jwt({"exp": True}),
    ],
    ids=["segments", "base64", "not-object", "no-exp", "str-exp", "bool-exp"],
)
def test_id_token_expiry_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        id_token_expiry(token)
    assert token not in str(excinfo.value)
    assert token not in excinfo.value.detail


# ── Observability + secrets hygiene ───────────────────────────────────────────


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


def _rendered_record(record: logging.LogRecord) -> str:
    return " ".join(f"{k}={v!r}" for k, v in vars(record).items()) + record.getMessage()


def _rendered_spans(exporter: InMemorySpanExporter) -> str:
    parts: list[str] = []
    for span in exporter.get_finished_spans():
        parts.append(span.name)
        parts.append(repr(dict(span.attributes or {})))
        parts.append(repr(span.status.description))
        for event in span.events:
            parts.append(event.name)
            parts.append(repr(dict(event.attributes or {})))
    return " ".join(parts)


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


# ── Lifecycle ─────────────────────────────────────────────────────────────────


async def test_aclose_closes_owned_client_once(monkeypatch: pytest.MonkeyPatch) -> None:
    closes: list[httpx.AsyncClient] = []
    original = httpx.AsyncClient.aclose

    async def _counting_aclose(self: httpx.AsyncClient) -> None:
        closes.append(self)
        await original(self)

    monkeypatch.setattr(httpx.AsyncClient, "aclose", _counting_aclose)
    parser = _parser()

    await parser.aclose()
    await parser.aclose()

    assert len(closes) == 1


async def test_aclose_never_closes_injected_client() -> None:
    client = httpx.AsyncClient()
    try:
        parser = DoclingServeParser(_settings(), client=client)
        await parser.aclose()
        await parser.aclose()
        assert client.is_closed is False
    finally:
        await client.aclose()


@respx.mock
async def test_injected_client_is_used_for_requests() -> None:
    route = respx.post(_CONVERT_URL).mock(
        return_value=_ok(load_docling_fixture(DOCLING_FIXTURE_SUCCESS))
    )
    async with httpx.AsyncClient() as client:
        parser = DoclingServeParser(_settings(), client=client)
        await parser.parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert route.call_count == 1
