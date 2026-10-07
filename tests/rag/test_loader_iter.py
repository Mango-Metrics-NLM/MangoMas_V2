"""Streaming loader ``iter_documents`` (spec-0035 R6 / M5).

The parity tests are the backwards-compatibility proof: with no parser,
``iter_documents`` must yield exactly what ``load_documents`` returns, in the
same order, so the pipeline can switch to the iterator without changing the
text-only ingest path.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path, PurePosixPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.adapters.parsers.base import ParsedDocument
from mangomas.config import ParserSettings
from mangomas.errors import ConfigError, DocumentParseError
from mangomas.rag import loader as loader_module
from mangomas.rag.loader import ParseFailure, RawDoc, iter_documents, load_documents
from tests.constants.docling import (
    SPEC_DOCLING_PROVIDER,
    SPEC_LOADER_EVENT_PARSE_FAILED,
    SPEC_LOADER_EVENT_SYMLINK_ESCAPE,
    SPEC_LOADER_META_PARSE_STATUS,
    SPEC_LOADER_META_PARSER,
    SPEC_LOADER_SPAN_NAME,
    SPEC_LOADER_STATUS_FAILED,
    SPEC_LOADER_STATUS_PARTIAL,
    SPEC_LOADER_STATUS_SUCCESS,
    TEST_DOCLING_MAX_FILE_BYTES,
    TEST_DOCLING_PAGES,
    TEST_DOCLING_PDF_BYTES,
    TEST_DOCLING_PDF_NAME,
    TEST_DOCLING_UPPER_PDF_NAME,
    TEST_LOADER_CANARY_TEXT,
    TEST_LOADER_TABLE_MARKDOWN,
    TEST_LOADER_TEXT_BODY,
)
from tests.fakes import FakeDocumentParser

if sys.platform == "win32":
    import _winapi

_DOCX = "slides.docx"


def _settings(**overrides: object) -> ParserSettings:
    values: dict[str, object] = {"enabled": True, "max_file_bytes": TEST_DOCLING_MAX_FILE_BYTES}
    values.update(overrides)
    return ParserSettings.model_validate(values)


async def _collect(path: Path, **kwargs: object) -> list[RawDoc | ParseFailure]:
    return [doc async for doc in iter_documents(str(path), **kwargs)]  # type: ignore[arg-type]


def _parsed_kwargs(parser: FakeDocumentParser, **overrides: object) -> dict[str, object]:
    return {
        "parser": parser,
        "settings": _settings(**overrides),
        "parser_name": SPEC_DOCLING_PROVIDER,
    }


# ── Parity with load_documents (back-compat) ──────────────────────────────────

_NAME = st.text(alphabet="abcxyzé_-", min_size=1, max_size=6)
_BODY = st.text(max_size=40)


@st.composite
def _trees(draw: st.DrawFn) -> list[tuple[str, bytes]]:
    files: dict[str, bytes] = {}
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        depth = draw(st.integers(min_value=0, max_value=2))
        parts = [draw(_NAME) for _ in range(depth)]
        suffix = draw(st.sampled_from([".txt", ".md", ".pdf", ".bin"]))
        rel = "/".join([*parts, draw(_NAME) + suffix])
        if draw(st.booleans()):
            files[rel] = draw(_BODY).encode("utf-8")
        else:
            files[rel] = b"\xff\xfe\x00 not utf-8"
    return sorted(files.items())


def _materialise(root: Path, tree: list[tuple[str, bytes]]) -> None:
    for rel, data in tree:
        target = root / rel
        if any(parent.is_file() for parent in target.parents):
            continue  # a generated name collided with an existing file path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            continue
        target.write_bytes(data)


@settings(max_examples=40, deadline=None)
@given(_trees())
def test_iter_documents_matches_load_documents_without_a_parser(
    tree: list[tuple[str, bytes]],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _materialise(root, tree)
        expected = asyncio.run(load_documents(str(root)))
        actual = asyncio.run(_collect(root))
    assert actual == expected


def test_parity_holds_for_a_single_text_file(tmp_path: Path) -> None:
    f = tmp_path / "one.md"
    f.write_text(TEST_LOADER_TEXT_BODY, encoding="utf-8")
    assert asyncio.run(_collect(f)) == asyncio.run(load_documents(str(f)))


@settings(max_examples=25, deadline=None)
@given(st.permutations(["b.md", "a/c.txt", "a.txt", "z/y/x.md"]))
def test_order_is_independent_of_creation_order(order: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in order:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(rel, encoding="utf-8")
        sources = [d.source for d in asyncio.run(_collect(root)) if isinstance(d, RawDoc)]
    # Path order (part by part), the contract load_documents has always had.
    assert sources == sorted(order, key=PurePosixPath)


async def test_missing_path_raises_before_the_first_yield(tmp_path: Path) -> None:
    agen = iter_documents(str(tmp_path / "absent"))
    with pytest.raises(ConfigError):
        await agen.__anext__()


# ── Which files reach the parser ──────────────────────────────────────────────


async def test_without_a_parser_pdfs_are_ignored_as_today(tmp_path: Path) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    assert await _collect(tmp_path) == []


async def test_a_suffix_outside_the_allow_list_is_ignored(tmp_path: Path) -> None:
    (tmp_path / _DOCX).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser()
    docs = await _collect(tmp_path, **_parsed_kwargs(parser, allowed_suffixes=(".pdf",)))
    assert docs == []
    assert parser.calls == []


async def test_an_upper_case_suffix_is_parsed(tmp_path: Path) -> None:
    (tmp_path / TEST_DOCLING_UPPER_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    docs = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert [d.source for d in docs] == [TEST_DOCLING_UPPER_PDF_NAME]
    assert parser.calls == [(TEST_DOCLING_UPPER_PDF_NAME, len(TEST_DOCLING_PDF_BYTES))]


async def test_text_and_parsed_files_are_interleaved_in_source_order(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(TEST_LOADER_TEXT_BODY, encoding="utf-8")
    (tmp_path / "b.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    (tmp_path / "c.txt").write_text(TEST_LOADER_TEXT_BODY, encoding="utf-8")
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    docs = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert [d.source for d in docs] == ["a.md", "b.pdf", "c.txt"]


# ── Parsed documents ──────────────────────────────────────────────────────────


async def test_a_parsed_document_keeps_its_markdown_and_metadata(tmp_path: Path) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    (doc,) = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert isinstance(doc, RawDoc)
    assert doc.text == TEST_LOADER_TABLE_MARKDOWN
    assert dict(doc.metadata) == {
        SPEC_LOADER_META_PARSER: SPEC_DOCLING_PROVIDER,
        SPEC_LOADER_META_PARSE_STATUS: SPEC_LOADER_STATUS_SUCCESS,
    }


async def test_a_partial_parse_is_marked_partial(tmp_path: Path) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(
        default=ParsedDocument(text=TEST_LOADER_TABLE_MARKDOWN, partial=True)
    )
    (doc,) = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert isinstance(doc, RawDoc)
    assert doc.metadata[SPEC_LOADER_META_PARSE_STATUS] == SPEC_LOADER_STATUS_PARTIAL


async def test_a_single_parsed_file_source_is_the_path_as_given(tmp_path: Path) -> None:
    """Same rule as text files: a bare name would let two same-named files collide."""
    f = tmp_path / TEST_DOCLING_PDF_NAME
    f.write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    (doc,) = await _collect(f, **_parsed_kwargs(parser))
    assert doc.source == f.as_posix()


async def test_same_named_single_files_get_distinct_sources(tmp_path: Path) -> None:
    """Regression (PR #83 review): bare-name sources made b/report.pdf purge a/report.pdf."""
    sources = []
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        f = tmp_path / folder / TEST_DOCLING_PDF_NAME
        f.write_bytes(TEST_DOCLING_PDF_BYTES)
        parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
        (doc,) = await _collect(f, **_parsed_kwargs(parser))
        sources.append(doc.source)
    assert sources[0] != sources[1]


@pytest.mark.parametrize("name", ["notes.rst", "README.MD", "data.csv"])
async def test_a_single_unlisted_file_is_read_as_text_with_a_parser(
    tmp_path: Path, name: str
) -> None:
    """Regression (PR #83 review): enabling the parser stopped `rag ingest notes.rst`."""
    f = tmp_path / name
    f.write_text(TEST_LOADER_TEXT_BODY, encoding="utf-8")
    parser = FakeDocumentParser()
    with_parser = await _collect(f, **_parsed_kwargs(parser))
    assert with_parser == await load_documents(str(f))
    assert parser.calls == []


def test_rawdoc_stays_hashable() -> None:
    """Regression (PR #83 review): the mapping field made hash(RawDoc) raise."""
    assert hash(RawDoc(source="a", text="x")) == hash(RawDoc(source="a", text="x"))


async def test_nested_parsed_sources_are_relative_posix_paths(tmp_path: Path) -> None:
    nested = tmp_path / "reports" / "2026"
    nested.mkdir(parents=True)
    (nested / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    (doc,) = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert doc.source == f"reports/2026/{TEST_DOCLING_PDF_NAME}"
    assert "\\" not in doc.source


# ── Failures never raise (except configuration) ───────────────────────────────


@pytest.mark.parametrize(
    ("size", "parsed"),
    [(TEST_DOCLING_MAX_FILE_BYTES, True), (TEST_DOCLING_MAX_FILE_BYTES + 1, False)],
)
async def test_the_size_limit_is_checked_before_reading(
    tmp_path: Path, size: int, parsed: bool
) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(b"x" * size)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    (doc,) = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert isinstance(doc, RawDoc) is parsed
    assert len(parser.calls) == (1 if parsed else 0)
    if not parsed:
        assert isinstance(doc, ParseFailure)
        assert isinstance(doc.error, DocumentParseError)


async def test_a_parse_error_is_yielded_and_iteration_continues(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    (tmp_path / "b.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(
        outcomes={"a.pdf": DocumentParseError("parser rejected it")},
        default=TEST_LOADER_TABLE_MARKDOWN,
    )
    failure, doc = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert isinstance(failure, ParseFailure)
    assert failure.source == "a.pdf"
    assert isinstance(failure.error, DocumentParseError)
    assert failure.error.source == "a.pdf"
    assert isinstance(doc, RawDoc)
    assert doc.source == "b.pdf"


async def test_an_error_that_already_names_its_source_is_kept(tmp_path: Path) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    original = DocumentParseError("boom", source=TEST_DOCLING_PDF_NAME)
    parser = FakeDocumentParser(default=original)
    (failure,) = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert isinstance(failure, ParseFailure)
    assert failure.error is original


async def test_a_config_error_fails_the_run(tmp_path: Path) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=ConfigError("docling-serve rejected credentials"))
    with pytest.raises(ConfigError):
        await _collect(tmp_path, **_parsed_kwargs(parser))


@pytest.mark.parametrize("text", ["", "  \n\t "])
async def test_empty_text_from_a_non_empty_file_is_a_failure(tmp_path: Path, text: str) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=text)
    (failure,) = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert isinstance(failure, ParseFailure)


async def test_an_empty_file_parsing_to_empty_text_is_a_document(tmp_path: Path) -> None:
    (tmp_path / TEST_DOCLING_PDF_NAME).write_bytes(b"")
    parser = FakeDocumentParser(default="")
    (doc,) = await _collect(tmp_path, **_parsed_kwargs(parser))
    assert isinstance(doc, RawDoc)
    assert doc.text == ""


# ── Containment ───────────────────────────────────────────────────────────────


def _link(link: Path, target: Path) -> Path:
    """Make ``target`` (a file) reachable at ``link``; return the path the loader walks.

    A file symlink where the platform allows one. Windows without the
    symlink privilege (WinError 1314) gets a directory junction to the
    target's parent instead: it needs no privilege, ``rglob`` descends into it
    and ``resolve()`` follows it, so the loader's containment check runs on
    every platform rather than being skipped (spec-0022 R8 zero-skip guard).
    The target's parent must therefore hold nothing but ``target``.
    """
    try:
        link.symlink_to(target)
    except OSError:
        if sys.platform == "win32":
            junction = link.with_suffix("")
            _winapi.CreateJunction(str(target.parent), str(junction))
            return junction / target.name
        raise
    return link


def test_link_reaches_the_target_through_the_link(tmp_path: Path) -> None:
    """Guards the helper itself: whichever mechanism, the walked path is a link."""
    (tmp_path / "real").mkdir()
    target = tmp_path / "real" / "real.md"
    target.write_text(TEST_LOADER_TEXT_BODY, encoding="utf-8")
    walked = _link(tmp_path / "alias.md", target)
    assert walked.parent == tmp_path or walked.parent.name == "alias"
    assert walked != target
    assert walked.resolve() == target.resolve()
    assert walked.read_text(encoding="utf-8") == TEST_LOADER_TEXT_BODY


async def test_a_symlink_escaping_the_root_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text(TEST_LOADER_CANARY_TEXT, encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    walked = _link(root / "link.md", secret)
    caplog.set_level(logging.WARNING, logger=loader_module.__name__)
    assert await _collect(root) == []
    escapes = [
        getattr(r, "source", None)
        for r in caplog.records
        if getattr(r, "event", None) == SPEC_LOADER_EVENT_SYMLINK_ESCAPE
    ]
    assert escapes == [walked.relative_to(root).as_posix()]


async def test_a_symlink_inside_the_root_is_followed(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    real = tmp_path / "real" / "real.md"
    real.write_text(TEST_LOADER_TEXT_BODY, encoding="utf-8")
    walked = _link(tmp_path / "alias.md", real)
    docs = await _collect(tmp_path)
    assert [d.source for d in docs] == [walked.relative_to(tmp_path).as_posix(), "real/real.md"]
    assert all(isinstance(d, RawDoc) and d.text == TEST_LOADER_TEXT_BODY for d in docs)


# ── Observability ─────────────────────────────────────────────────────────────


def _exporter() -> InMemorySpanExporter:
    """Attach to whichever provider is live (see tests/rag/test_retrieval.py)."""
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


async def test_each_parse_emits_one_span_with_its_status(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    (tmp_path / "b.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(
        outcomes={"b.pdf": DocumentParseError("nope")}, default=TEST_LOADER_TABLE_MARKDOWN
    )
    exporter = _exporter()
    await _collect(tmp_path, **_parsed_kwargs(parser))
    spans = [s for s in exporter.get_finished_spans() if s.name == SPEC_LOADER_SPAN_NAME]
    statuses = {
        (s.attributes or {}).get("rag.source"): (s.attributes or {}).get("rag.parse.status")
        for s in spans
    }
    assert statuses == {"a.pdf": SPEC_LOADER_STATUS_SUCCESS, "b.pdf": SPEC_LOADER_STATUS_FAILED}


async def test_the_page_count_is_recorded_on_the_span(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(
        default=ParsedDocument(text=TEST_LOADER_TABLE_MARKDOWN, pages=TEST_DOCLING_PAGES)
    )
    exporter = _exporter()
    await _collect(tmp_path, **_parsed_kwargs(parser))
    (span,) = [s for s in exporter.get_finished_spans() if s.name == SPEC_LOADER_SPAN_NAME]
    assert (span.attributes or {})["rag.parse.pages"] == TEST_DOCLING_PAGES


async def test_document_text_never_reaches_logs_or_spans(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "a.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    (tmp_path / "b.pdf").write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(
        outcomes={"b.pdf": DocumentParseError("nope")}, default=TEST_LOADER_CANARY_TEXT
    )
    exporter = _exporter()
    caplog.set_level(logging.DEBUG, logger=loader_module.__name__)
    await _collect(tmp_path, **_parsed_kwargs(parser))
    rendered = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert TEST_LOADER_CANARY_TEXT not in rendered
    assert SPEC_LOADER_EVENT_PARSE_FAILED in rendered
    for span in exporter.get_finished_spans():
        assert TEST_LOADER_CANARY_TEXT not in str(dict(span.attributes or {}))


# ── RawDoc metadata ───────────────────────────────────────────────────────────


def test_rawdoc_default_metadata_is_empty_immutable_and_unshared() -> None:
    a = RawDoc(source="a", text="x")
    b = RawDoc(source="b", text="y")
    assert dict(a.metadata) == {}
    with pytest.raises(TypeError):
        a.metadata["k"] = "v"  # type: ignore[index]
    assert a == RawDoc(source="a", text="x")
    assert dict(b.metadata) == {}


async def test_a_symlinked_pdf_escaping_the_root_is_never_uploaded(tmp_path: Path) -> None:
    """The escape risk that matters: an outside file sent to the parser service."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / TEST_DOCLING_PDF_NAME).write_bytes(TEST_DOCLING_PDF_BYTES)
    root = tmp_path / "root"
    root.mkdir()
    _link(root / TEST_DOCLING_PDF_NAME, outside / TEST_DOCLING_PDF_NAME)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    assert await _collect(root, **_parsed_kwargs(parser)) == []
    assert parser.calls == []


async def test_only_one_parse_is_in_flight(tmp_path: Path) -> None:
    """Backs the one-document-in-memory claim: the next file is not read until
    the consumer asks for it."""
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / name).write_bytes(TEST_DOCLING_PDF_BYTES)
    parser = FakeDocumentParser(default=TEST_LOADER_TABLE_MARKDOWN)
    agen = iter_documents(str(tmp_path), **_parsed_kwargs(parser))  # type: ignore[arg-type]
    first = await agen.__anext__()
    assert first.source == "a.pdf"
    assert [name for name, _ in parser.calls] == ["a.pdf"]
    await agen.aclose()
