"""Tests for the docling-serve parser conversion, protocol and request hygiene (spec-0035)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from mangomas.adapters.parsers import DoclingServeParser, DocumentParser
from mangomas.adapters.parsers._auth import GoogleIdTokenProvider, IdTokenProvider
from tests.adapters.parsers.conftest import (
    _CONVERT_URL,
    _SOURCE_URL,
    _file_parts,
    _form_fields,
    _ok,
    _parser,
    _settings,
    _zip,
)
from tests.constants.docling import (
    DOCLING_FIXTURE_PARTIAL,
    DOCLING_FIXTURE_SUCCESS,
    SPEC_DOCLING_CONVERT_PATH,
    SPEC_DOCLING_DO_OCR_FIELD,
    SPEC_DOCLING_DOCUMENT_TIMEOUT_FIELD,
    SPEC_DOCLING_EVENT_PARTIAL,
    SPEC_DOCLING_FILES_FIELD,
    SPEC_DOCLING_FORBIDDEN_FIELD,
    SPEC_DOCLING_FORMATS,
    SPEC_DOCLING_FROM_FORMATS_FIELD,
    SPEC_DOCLING_MAX_NUM_PAGES_FIELD,
    SPEC_DOCLING_TO_FORMAT_MD,
    SPEC_DOCLING_TO_FORMATS_FIELD,
    TEST_DOCLING_BASE_URL,
    TEST_DOCLING_GENERATED_PDF_NAME,
    TEST_DOCLING_MAX_RESPONSE_BYTES,
    TEST_DOCLING_ORIGINAL_STEM,
    TEST_DOCLING_PAGES,
    TEST_DOCLING_PAGES_FIELD,
    TEST_DOCLING_PDF_BYTES,
    TEST_DOCLING_PDF_NAME,
    TEST_DOCLING_UPPER_PDF_NAME,
    TEST_DOCLING_ZIP_MEMBER,
    TEST_DOCLING_ZIP_SMALL_MEMBER,
    load_docling_fixture,
)

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
    body["document"][TEST_DOCLING_PAGES_FIELD] = TEST_DOCLING_PAGES
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    with caplog.at_level(logging.WARNING):
        parsed = await _parser().parse(
            filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES
        )

    assert parsed.text == body["document"]["md_content"]
    assert parsed.pages == TEST_DOCLING_PAGES
    assert parsed.partial is True
    records = [r for r in caplog.records if getattr(r, "event", None) == SPEC_DOCLING_EVENT_PARTIAL]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING


@respx.mock
async def test_partial_success_without_pages_keeps_partial_flag() -> None:
    body = load_docling_fixture(DOCLING_FIXTURE_PARTIAL)
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    parsed = await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert parsed.pages is None
    assert parsed.partial is True


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
