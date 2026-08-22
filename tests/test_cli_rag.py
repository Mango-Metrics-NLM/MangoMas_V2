"""Tests for the ``mangomas rag`` CLI sub-commands (fakes via monkeypatch)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mangomas.agents import ChatAgent
from mangomas.cli import _runtime as cli_runtime
from mangomas.cli import main as cli_main
from mangomas.core import AgentContext, Orchestrator
from tests._seam_guards import forbid_real_orchestrator
from tests.fakes import FakeEmbeddingClient, FakeLLM, FakeVectorStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _noop_close(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_real_orchestrator(monkeypatch)

    async def _close(_orch: Orchestrator) -> None:
        return None

    monkeypatch.setattr(cli_runtime, "_close_orchestrator", _close)


def _rag_orch(*, enabled: bool) -> Orchestrator:
    ctx = AgentContext(
        llm=FakeLLM(),
        repo=None,
        embeddings=FakeEmbeddingClient() if enabled else None,
        vector_store=FakeVectorStore() if enabled else None,
    )
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


def test_rag_ingest_reports_counts(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    orch = _rag_orch(enabled=True)
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)
    doc = tmp_path / "a.txt"
    doc.write_text("one two three four", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["rag", "ingest", str(doc)])
    assert result.exit_code == 0
    assert "ingested docs=1" in result.stdout
    assert "chunks=" in result.stdout


def test_rag_ingest_exits_when_rag_disabled(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    orch = _rag_orch(enabled=False)
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)
    doc = tmp_path / "a.txt"
    doc.write_text("hello", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["rag", "ingest", str(doc)])
    assert result.exit_code == 2
    assert "RAG is not enabled" in (result.stdout + (result.stderr or ""))


def test_rag_ingest_verbose_requests_debug_logging(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
    cli_logging_calls: list[dict[str, object]],
) -> None:
    """`rag ingest --verbose` asks for DEBUG logging."""
    orch = _rag_orch(enabled=True)
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)
    doc = tmp_path / "a.txt"
    doc.write_text("alpha beta", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["rag", "ingest", str(doc), "--verbose"])
    assert result.exit_code == 0
    assert cli_logging_calls == [{"verbose": True}]


def test_rag_query_verbose_requests_debug_logging(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    cli_logging_calls: list[dict[str, object]],
) -> None:
    """`rag query --verbose` — a branch nothing exercised until now.

    `commands/rag.py:83` was never executed by any test. It did not show up as a
    coverage gap because the unanchored `"\\.\\.\\."` exclusion pattern matched
    this module's `typer.Argument(...)` signatures and dropped both command
    bodies from measurement entirely, so the file reported 100% over a
    denominator of 16 statements instead of 49.
    """
    orch = _rag_orch(enabled=True)
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    result = runner.invoke(cli_main.app, ["rag", "query", "anything", "--verbose"])
    assert result.exit_code == 0
    assert cli_logging_calls == [{"verbose": True}]


async def _ingest(orch: Orchestrator, text: str, source: str) -> None:
    """Seed the orchestrator's fake vector store with one embedded document."""
    emb = orch.context.embeddings
    store = orch.context.vector_store
    assert emb is not None and store is not None
    vec = await emb.embed(text)
    await store.upsert(
        ids=[f"{source}#0"],
        embeddings=[vec],
        documents=[text],
        metadatas=[{"source": source, "index": 0}],
    )


def test_rag_query_prints_ranked_context(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    orch = _rag_orch(enabled=True)
    asyncio.run(_ingest(orch, "the grounded fact", "kb.txt"))
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    result = runner.invoke(cli_main.app, ["rag", "query", "the grounded fact"])
    assert result.exit_code == 0
    assert "grounded fact" in result.stdout
    assert "source=kb.txt" in result.stdout


def test_rag_query_no_results(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    orch = _rag_orch(enabled=True)  # empty store
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    result = runner.invoke(cli_main.app, ["rag", "query", "anything"])
    assert result.exit_code == 0
    assert "No relevant context found." in result.stdout


def test_rag_query_exits_when_rag_disabled(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    orch = _rag_orch(enabled=False)
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    result = runner.invoke(cli_main.app, ["rag", "query", "anything"])
    assert result.exit_code == 2
    assert "RAG is not enabled" in (result.stdout + (result.stderr or ""))


def test_rag_query_top_k_option(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    orch = _rag_orch(enabled=True)
    asyncio.run(_ingest(orch, "fact one", "a.txt"))
    asyncio.run(_ingest(orch, "fact two", "b.txt"))
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    result = runner.invoke(cli_main.app, ["rag", "query", "fact", "--top-k", "1"])
    assert result.exit_code == 0
    # Only one ranked passage printed.
    assert result.stdout.count("score=") == 1
