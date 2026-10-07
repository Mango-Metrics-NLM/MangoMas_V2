"""Tests for docling-serve OOXML archive validation, zip-bomb mitigation and suffix checks."""

from __future__ import annotations

import io
import zipfile

import pytest
import respx

from mangomas.adapters.parsers._archive import check_ooxml_archive
from mangomas.errors import DocumentParseError
from tests.adapters.parsers.conftest import (
    _CONVERT_URL,
    _MAX_ENTRIES,
    _form_fields,
    _ok,
    _parser,
    _zip,
)
from tests.constants.docling import (
    DOCLING_FIXTURE_SUCCESS,
    SPEC_DOCLING_FORMATS,
    SPEC_DOCLING_FROM_FORMATS_FIELD,
    TEST_DOCLING_DOCX_NAME,
    TEST_DOCLING_HTML_NAME,
    TEST_DOCLING_MAX_ZIP_ENTRIES,
    TEST_DOCLING_MAX_ZIP_RATIO,
    TEST_DOCLING_NO_SUFFIX_NAME,
    TEST_DOCLING_ORIGINAL_STEM,
    TEST_DOCLING_PDF_BYTES,
    TEST_DOCLING_UNKNOWN_NAME,
    TEST_DOCLING_ZIP_BOMB_PAYLOAD_BYTES,
    TEST_DOCLING_ZIP_MEMBER,
    TEST_DOCLING_ZIP_SMALL_MEMBER,
    load_docling_fixture,
)

# ── Archive & Zip-Bomb Mitigation ─────────────────────────────────────────────


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


# ── Suffix Mapping & Filtering ────────────────────────────────────────────────


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
