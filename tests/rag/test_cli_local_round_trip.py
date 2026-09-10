"""Tier-3 E4: ``mangomas rag ingest`` → ``query`` with the real local backend.

The only tier-3 scenario that goes through ``build_orchestrator``, which makes
it the one that proves the ``MANGOMAS_EMBEDDINGS__DEVICE`` composition wiring
end to end: env → ``EmbeddingSettings.device`` → factory → adapter → loader.
The unit test in ``tests/adapters/embeddings/`` proves the forwarding with a
recorder; this proves a real model actually loads under it and answers.

Distinct from ``tests/test_cli_rag.py``, which drives the same two commands
with fakes injected via ``monkeypatch`` and never builds a real orchestrator
(its ``forbid_real_orchestrator`` guard makes that explicit). Here the real
build is the point.

Skipped unless ``RUN_EMBEDDINGS_LOCAL=1`` **and** ``RUN_RAG=1``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mangomas.cli.main import app
from mangomas.config import get_settings
from tests.constants import (
    DB_URL_ENV,
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    DEVICE_CPU,
    EMBEDDINGS_DEVICE_ENV,
    EMBEDDINGS_ENABLED_ENV,
    EMBEDDINGS_MODEL_ENV,
    EMBEDDINGS_PROVIDER_ENV,
    IN_MEMORY_SQLITE_URL,
    RAG_DEVICE_CORPUS,
    RAG_DEVICE_EXPECTED_TOP_SOURCE,
    RAG_DEVICE_QUERY,
    VECTOR_ENABLED_ENV,
    VECTOR_PERSIST_DIR_ENV,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.embeddings_local, pytest.mark.rag]

_SENTENCE_TRANSFORMERS = "sentence_transformers"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _enable_local_rag(
    monkeypatch: pytest.MonkeyPatch, persist_dir: Path, *, device: str | None
) -> None:
    """Turn on the real local embedding + vector stack for one CLI run."""
    monkeypatch.setenv(EMBEDDINGS_ENABLED_ENV, "true")
    monkeypatch.setenv(EMBEDDINGS_PROVIDER_ENV, _SENTENCE_TRANSFORMERS)
    monkeypatch.setenv(EMBEDDINGS_MODEL_ENV, DEFAULT_LOCAL_EMBEDDING_MODEL)
    monkeypatch.setenv(VECTOR_ENABLED_ENV, "true")
    monkeypatch.setenv(VECTOR_PERSIST_DIR_ENV, str(persist_dir))
    # Never touch a developer's real database from a suite that builds a real
    # orchestrator.
    monkeypatch.setenv(DB_URL_ENV, IN_MEMORY_SQLITE_URL)
    if device is None:
        # Deleting, not merely "not setting". The auto-detect case must mean
        # *no* device configured — if a developer has
        # MANGOMAS_EMBEDDINGS__DEVICE exported, leaving it in place turns the
        # parity check into forced-vs-forced and quietly proves nothing. That
        # is the same ambient-environment trap `tests/deploy/
        # test_env_example_contract.py` records: an env-reading test whose
        # verdict depends on the shell it runs in is not a contract test.
        monkeypatch.delenv(EMBEDDINGS_DEVICE_ENV, raising=False)
    else:
        monkeypatch.setenv(EMBEDDINGS_DEVICE_ENV, device)
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "device",
    [
        # Auto-detect: what an operator gets by default, and the only case
        # that exercises a GPU when one is present.
        pytest.param(None, id="auto-detect"),
        # Forced CPU: the operator knob, and the way a GPU box can reproduce
        # what the nightly CPU runner sees.
        pytest.param(DEVICE_CPU, id="forced-cpu"),
    ],
)
def test_ingest_then_query_round_trip_through_the_cli(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    device: str | None,
) -> None:
    """Both device modes ingest a corpus and retrieve the right document.

    The oracle is the *source* named in the query output, not the score the
    CLI prints alongside it — scores drift across devices, the ranking does
    not (spec-0029 R2.2).
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, text in RAG_DEVICE_CORPUS.items():
        (corpus / name).write_text(text, encoding="utf-8")

    _enable_local_rag(monkeypatch, tmp_path / "chroma", device=device)

    ingest = runner.invoke(app, ["rag", "ingest", str(corpus)])
    assert ingest.exit_code == 0, ingest.output
    assert f"docs={len(RAG_DEVICE_CORPUS)}" in ingest.output

    query = runner.invoke(app, ["rag", "query", RAG_DEVICE_QUERY])
    assert query.exit_code == 0, query.output
    logger.info("CLI RAG round trip complete", extra={"device": device})

    # Typer's CliRunner always mixes stderr into ``Result.output`` (StreamMixer;
    # there is no mix_stderr=False). ``build_orchestrator`` logs INFO to stderr,
    # so output[0] is a log line, not the ranking. User results are stdout.
    first_result = _first_ranked_source_line(query.stdout)
    assert RAG_DEVICE_EXPECTED_TOP_SOURCE in first_result, (
        f"expected {RAG_DEVICE_EXPECTED_TOP_SOURCE} ranked first, got: {query.stdout!r}"
    )


def _first_ranked_source_line(stdout: str) -> str:
    """Return the first CLI ranking line (``[1] score=... source=...``)."""
    for line in stdout.splitlines():
        if "source=" in line:
            return line
    raise AssertionError(f"no source= ranking line in stdout: {stdout!r}")
