"""Tests for the ``DocumentParser`` seam (spec-0035 R1) and its shared fake."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from mangomas.adapters.parsers import PARSER_EXTRAS_KEY, DocumentParser, ParsedDocument
from mangomas.adapters.parsers import base as parsers_base
from tests.constants import (
    SPEC_PARSER_EXTRAS_KEY,
    TEST_PARSER_CONTENT,
    TEST_PARSER_FILENAME,
    TEST_PARSER_OTHER_FILENAME,
    TEST_PARSER_PAGES,
    TEST_PARSER_TEXT,
)
from tests.fakes import FakeDocumentParser


class _ParseOnly:
    """Has ``parse`` but no ``aclose`` — must not satisfy the protocol."""

    async def parse(self, *, filename: str, content: bytes) -> ParsedDocument:
        return ParsedDocument(text=filename, pages=len(content))


def test_extras_key_is_pinned() -> None:
    assert PARSER_EXTRAS_KEY == SPEC_PARSER_EXTRAS_KEY


def test_package_reexports_are_the_base_objects() -> None:
    assert DocumentParser is parsers_base.DocumentParser
    assert ParsedDocument is parsers_base.ParsedDocument
    assert PARSER_EXTRAS_KEY is parsers_base.PARSER_EXTRAS_KEY


def test_parsed_document_defaults() -> None:
    doc = ParsedDocument(text=TEST_PARSER_TEXT)
    assert doc.pages is None
    assert doc.partial is False


def test_parsed_document_is_frozen() -> None:
    doc = ParsedDocument(text=TEST_PARSER_TEXT, pages=TEST_PARSER_PAGES)
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.text = TEST_PARSER_OTHER_FILENAME  # type: ignore[misc]


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeDocumentParser(), DocumentParser)


def test_class_missing_aclose_does_not_satisfy_the_protocol() -> None:
    assert not isinstance(_ParseOnly(), DocumentParser)


# ── FakeDocumentParser behaviour (consumed by later spec-0035 milestones) ─────


async def test_fake_returns_default_and_records_call() -> None:
    fake = FakeDocumentParser(default=TEST_PARSER_TEXT)
    doc = await fake.parse(filename=TEST_PARSER_FILENAME, content=TEST_PARSER_CONTENT)
    assert doc == ParsedDocument(text=TEST_PARSER_TEXT)
    assert fake.calls == [(TEST_PARSER_FILENAME, len(TEST_PARSER_CONTENT))]


async def test_fake_prefers_filename_over_suffix() -> None:
    by_name = ParsedDocument(text=TEST_PARSER_TEXT, pages=TEST_PARSER_PAGES, partial=True)
    fake = FakeDocumentParser(
        {TEST_PARSER_FILENAME: by_name, ".PDF": TEST_PARSER_OTHER_FILENAME},
    )
    assert await fake.parse(filename=TEST_PARSER_FILENAME, content=b"") is by_name


async def test_fake_falls_back_to_case_insensitive_suffix() -> None:
    fake = FakeDocumentParser({".PDF": TEST_PARSER_TEXT})
    doc = await fake.parse(filename=TEST_PARSER_FILENAME, content=b"")
    assert doc.text == TEST_PARSER_TEXT


async def test_fake_raises_scripted_exception() -> None:
    boom = RuntimeError(TEST_PARSER_FILENAME)
    fake = FakeDocumentParser({TEST_PARSER_FILENAME: boom})
    with pytest.raises(RuntimeError) as info:
        await fake.parse(filename=TEST_PARSER_FILENAME, content=TEST_PARSER_CONTENT)
    assert info.value is boom
    # The attempt is recorded before the raise: the parser really was called.
    assert fake.calls == [(TEST_PARSER_FILENAME, len(TEST_PARSER_CONTENT))]


async def test_fake_gate_holds_parse_until_set() -> None:
    gate = asyncio.Event()
    fake = FakeDocumentParser(default=TEST_PARSER_TEXT, gate=gate)
    task = asyncio.create_task(fake.parse(filename=TEST_PARSER_FILENAME, content=b""))
    await asyncio.sleep(0)
    assert not task.done()
    assert fake.calls == [(TEST_PARSER_FILENAME, 0)]
    gate.set()
    assert (await task).text == TEST_PARSER_TEXT


async def test_fake_aclose_counts_every_close() -> None:
    fake = FakeDocumentParser()
    assert fake.closed is False
    await fake.aclose()
    await fake.aclose()
    assert fake.closed is True
    assert fake.close_count == 2
