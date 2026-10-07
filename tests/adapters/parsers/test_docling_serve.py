"""Tests for the docling-serve parser adapter — integration smoke tests (spec-0035).

Maintained for backwards compatibility for ``pytest tests/adapters/parsers/test_docling_serve.py``.
The detailed underlying test suites are decomposed into:
- ``test_docling_serve_convert.py``: Protocol satisfaction, status mapping, parameters, lifecycle
- ``test_docling_serve_archive.py``: OOXML archive inspection, zip-bomb mitigation, suffix checks
- ``test_docling_serve_auth.py``: API key and Google ID token auth strategies and token caching
- ``test_docling_serve_errors.py``: HTTP transport errors, status code mappings, telemetry
"""

from __future__ import annotations

import respx

from mangomas.adapters.parsers import DocumentParser
from tests.adapters.parsers.conftest import _CONVERT_URL, _ok, _parser
from tests.constants.docling import (
    DOCLING_FIXTURE_SUCCESS,
    TEST_DOCLING_PAGES,
    TEST_DOCLING_PAGES_FIELD,
    TEST_DOCLING_PDF_BYTES,
    TEST_DOCLING_PDF_NAME,
    load_docling_fixture,
)


def test_aggregate_facade_satisfies_protocol() -> None:
    """Smoke test ensuring DoclingServeParser satisfies DocumentParser."""
    assert isinstance(_parser(), DocumentParser)


@respx.mock
async def test_aggregate_facade_e2e_convert_smoke() -> None:
    """Smoke test ensuring the convert pipeline functions end-to-end."""
    body = load_docling_fixture(DOCLING_FIXTURE_SUCCESS)
    body["document"][TEST_DOCLING_PAGES_FIELD] = TEST_DOCLING_PAGES
    respx.post(_CONVERT_URL).mock(return_value=_ok(body))

    parsed = await _parser().parse(filename=TEST_DOCLING_PDF_NAME, content=TEST_DOCLING_PDF_BYTES)

    assert parsed.text == body["document"]["md_content"]
    assert parsed.pages == TEST_DOCLING_PAGES
    assert parsed.partial is False
