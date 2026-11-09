"""Run provenance and per-slice aggregates on the report (spec-0045 B6).

Provenance is a label block: stamped by the runner, enriched by the CLI,
serialised by every sink, round-tripped by the baseline loader and ignored by
``diff_reports`` — so two reports can be compared knowing *what* each ran and
a regenerated baseline never drifts on a timestamp.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mangomas import __version__
from mangomas.agents import ChatAgent
from mangomas.agents._prompt import LLM_PROFILES_EXTRAS_KEY
from mangomas.config import DEFAULT_EVAL_SCHEMA_VERSION
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.errors import ConfigError
from mangomas.eval import EvalRunner, diff_reports, load_baseline, target_registry
from mangomas.eval.dataset import DatasetRow
from mangomas.eval.provenance import GIT_SHA_ENV, dataset_digest, digest_of, resolve_git_sha
from mangomas.eval.runner import SliceStats, slice_stats
from mangomas.eval.scorers.exact_match import ExactMatchScorer
from mangomas.eval.sinks.console import ConsoleSink
from mangomas.eval.sinks.json_file import JsonFileSink
from mangomas.eval.sinks.sqlite_results import SqliteResultsSink
from mangomas.eval.targets import AgentTarget
from tests.constants import DEFAULT_AGENT_NAME, STUB_REPLY
from tests.fakes import FakeLLM, FakeRepository


def _rows() -> list[DatasetRow]:
    def row(rid: str, category: str | None, expected: str = STUB_REPLY) -> DatasetRow:
        metadata = {"category": category} if category is not None else {}
        return DatasetRow(
            id=rid,
            messages=[Message(role="user", content="x")],
            expected=expected,
            metadata=metadata,
        )

    return [
        row("a1", "arith"),
        row("a2", "arith", expected="wrong"),
        row("s1", "smoke"),
        row("u1", None),
        row("n1", "numeric-key" if False else "smoke"),
    ]


def _orchestrator() -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=FakeRepository()))
    orch.register(ChatAgent())
    return orch


async def _run(**kwargs: object) -> tuple[Orchestrator, object]:
    orch = _orchestrator()
    runner = EvalRunner(orch, ExactMatchScorer(), **kwargs)  # type: ignore[arg-type]
    return orch, await runner.run(_rows(), DEFAULT_AGENT_NAME)


async def test_report_carries_provenance_with_the_runner_side_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GIT_SHA_ENV, "abc123")
    _, report = await _run(provenance_labels={"llm_model": "m", "llm_profile": None})
    run = report.run  # type: ignore[attr-defined]
    assert run is not None
    assert len(run.run_id) == 32
    assert run.started_at.endswith("+00:00")
    assert run.dataset_sha256 == dataset_digest(_rows())
    assert (run.scorer, run.target) == ("exact_match", DEFAULT_AGENT_NAME)
    assert (run.mangomas_version, run.schema_version) == (__version__, DEFAULT_EVAL_SCHEMA_VERSION)
    assert run.git_sha == "abc123"
    assert run.labels == {"llm_model": "m", "llm_profile": None}


async def test_git_sha_prefers_the_ci_env_then_reads_dot_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(GIT_SHA_ENV, "  fromci  ")
    assert await resolve_git_sha() == "fromci"
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    assert await resolve_git_sha(tmp_path) is None, "no repository above tmp_path"

    repo = tmp_path / "repo" / "nested"
    git = tmp_path / "repo" / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    repo.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    assert await resolve_git_sha(repo) == "a" * 40, "a loose ref, found from a nested directory"

    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text(f"# pack-refs\n{'b' * 40} refs/heads/main\n", encoding="utf-8")
    assert await resolve_git_sha(repo) == "b" * 40, "a packed ref"

    (git / "HEAD").write_text("c" * 40 + "\n", encoding="utf-8")
    assert await resolve_git_sha(repo) == "c" * 40, "a detached HEAD"

    (git / "HEAD").write_text("ref: refs/heads/gone\n", encoding="utf-8")
    assert await resolve_git_sha(repo) is None, "a ref in neither loose nor packed form"
    (git / "packed-refs").unlink()
    assert await resolve_git_sha(repo) is None, "no packed-refs file at all"

    (git / "HEAD").unlink()
    (git / "HEAD").mkdir()  # reading a directory raises IsADirectoryError (an OSError)
    assert await resolve_git_sha(repo) is None, "an unreadable repository is simply unknown"


async def test_git_sha_follows_a_worktree_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(GIT_SHA_ENV, raising=False)
    main_git = tmp_path / "main" / ".git"
    (main_git / "refs" / "heads").mkdir(parents=True)
    (main_git / "refs" / "heads" / "feature").write_text("d" * 40, encoding="utf-8")
    wt_git = main_git / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")
    assert await resolve_git_sha(worktree) == "d" * 40
    (worktree / ".git").write_text("not a pointer", encoding="utf-8")
    assert await resolve_git_sha(worktree) is None


async def test_console_sink_prints_no_interval_for_an_empty_report() -> None:
    runner = EvalRunner(_orchestrator(), ExactMatchScorer())
    report = await runner.run([], DEFAULT_AGENT_NAME)
    lines: list[str] = []
    await ConsoleSink(write=lines.append).emit(report)
    assert not any("Wilson" in line for line in lines)
    assert any(line.startswith("run=") for line in lines), (
        "provenance is stamped even on an empty run"
    )


def test_dataset_digest_is_content_based_not_file_based() -> None:
    rows = _rows()
    same = [
        DatasetRow(
            id=r.id, messages=list(r.messages), expected=r.expected, metadata=dict(r.metadata)
        )
        for r in rows
    ]
    assert dataset_digest(rows) == dataset_digest(same)
    changed = list(rows)
    changed[0] = DatasetRow(id="a1", messages=rows[0].messages, expected="other")
    assert dataset_digest(changed) != dataset_digest(rows)
    assert digest_of({"b": 1, "a": 2}) == digest_of({"a": 2, "b": 1}), "key order is canonicalised"


async def test_slices_group_rows_by_the_metadata_key_and_skip_unsliced_rows() -> None:
    _, report = await _run()
    slices = report.slices  # type: ignore[attr-defined]
    assert set(slices) == {"arith", "smoke"}, "the row without a category is unsliced"
    assert slices["arith"] == SliceStats(size=2, passed=1, failed=1, errored=0, mean_score=0.5)
    assert slices["smoke"].size == 2 and slices["smoke"].passed == 2
    assert slice_stats(_rows(), [], slice_key="category") == {}
    assert slice_stats(_rows(), report.rows, slice_key="absent") == {}  # type: ignore[attr-defined]


async def test_slice_key_is_configurable() -> None:
    _, report = await _run(slice_key="nope")
    assert report.slices == {}  # type: ignore[attr-defined]


async def test_json_file_round_trips_provenance_and_slices_and_diff_ignores_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(GIT_SHA_ENV, "deadbeef")
    _, report = await _run(provenance_labels={"llm_provider": "lmstudio"})
    path = tmp_path / "report.json"
    await JsonFileSink(path=str(path)).emit(report)  # type: ignore[arg-type]
    loaded = await load_baseline(path)
    assert loaded.run == report.run  # type: ignore[attr-defined]
    assert loaded.slices == report.slices  # type: ignore[attr-defined]
    # A later run has a new run id and timestamp; the diff must not call that a change.
    _, later = await _run()
    diff = diff_reports(loaded, later)  # type: ignore[arg-type]
    assert diff.regressed_rows == [] and diff.mean_score_delta == 0.0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run"]["git_sha"] == "deadbeef" and payload["slices"]["arith"]["size"] == 2


async def test_pre_spec_baseline_without_run_or_slices_still_loads(tmp_path: Path) -> None:
    legacy = {
        "scorer": "exact_match",
        "agent_name": "chat",
        "dataset_size": 0,
        "passed": 0,
        "failed": 0,
        "errored": 0,
        "mean_score": 0.0,
        "duration_ms": 0.0,
        "rows": [],
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = await load_baseline(path)
    assert loaded.run is None and loaded.slices == {}


async def test_console_sink_prints_interval_slices_and_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GIT_SHA_ENV, "cafef00dcafef00d")
    _, report = await _run()
    lines: list[str] = []
    await ConsoleSink(write=lines.append).emit(report)  # type: ignore[arg-type]
    text = "\n".join(lines)
    assert "95% Wilson" in text and "informational" in text
    assert "slice=arith size=2 passed=1" in text
    assert "git=cafef00dcafe" in text and "run=" in text


async def test_sqlite_sink_stores_run_json(tmp_path: Path) -> None:
    _, report = await _run()
    db = tmp_path / "r.db"
    await SqliteResultsSink(db_path=str(db)).emit(report)  # type: ignore[arg-type]
    with sqlite3.connect(db) as conn:
        (run_json,) = conn.execute("SELECT run_json FROM eval_reports").fetchone()
    assert json.loads(run_json)["scorer"] == "exact_match"


async def test_agent_target_routes_every_row_through_the_named_profile() -> None:
    """The profile is selected per request (spec-0040), so the reply comes from that client."""
    base, cloud = FakeLLM(reply="local-reply"), FakeLLM(reply="cloud-reply")
    orch = Orchestrator(
        AgentContext(
            llm=base, repo=FakeRepository(), extras={LLM_PROFILES_EXTRAS_KEY: {"cloud": cloud}}
        )
    )
    orch.register(ChatAgent())
    request = AgentRequest(messages=[Message(role="user", content="x")], metadata={"k": 1})
    outcome = await AgentTarget(agent=DEFAULT_AGENT_NAME, llm_profile="cloud").run(
        request, orch=orch
    )
    assert outcome.content == "cloud-reply" and not base.calls
    # The response-side stamp is added by the composition-built orchestrator (spec-0040),
    # not by the bare one used here; routing is what this target owns.
    assert request.metadata == {"k": 1}, "the caller's request is not mutated"
    plain = await AgentTarget(agent=DEFAULT_AGENT_NAME).run(request, orch=orch)
    assert plain.content == "local-reply"


def test_agent_target_factory_validates_llm_profile() -> None:
    target = target_registry.get("agent")({"agent": "chat", "llm_profile": "cloud"})
    assert isinstance(target, AgentTarget)
    with pytest.raises(ConfigError, match="llm_profile"):
        target_registry.get("agent")({"agent": "chat", "llm_profile": ""})
