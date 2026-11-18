"""Regression + AQA guards for the 2026-11-15 origin-delta validation (spec-0051).

Branch ``sdlc/origin-delta-aqa-20261115``, cut from ``origin/dev@45e04fa``
(post-cutover parity with ``origin/qa`` and ``origin/main``). Every guard is
hermetic (no sockets, no GPUs, no live services) and was verified RED against
unmodified ``45e04fa`` before landing fixes. See ``docs/testing/rca-20261115.md``
for baseline/post-fix lane results, root causes, and mutation proofs.
"""

from __future__ import annotations

import importlib
import json
import logging
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
import typer
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from mangomas.agents import ChatAgent
from mangomas.api.app import create_app
from mangomas.api.health import READINESS_PATHS, check_ready
from mangomas.cli import _runtime as cli_runtime
from mangomas.cli.commands._eval_config import _build_sinks, _resolve_gating, _resolve_regression
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR
from mangomas.cli.main import app as cli_app
from mangomas.config import APISettings, EvalSettings
from mangomas.core import AgentContext, Orchestrator
from mangomas.errors import ConfigError
from mangomas.eval import DEFAULT_SINK_ARTIFACTS
from tests._seam_guards import forbid_real_orchestrator
from tests.constants.corpus import CLAUDE_AGENTS_DIR_RELPATH, CLAUDE_SKILLS_DIR_RELPATH
from tests.constants.deploy import PROVIDER_REQUIREMENTS
from tests.deploy import _makefile
from tests.fakes import FakeLLM, FakeRepository
from tests.tooling._corpus import is_ignored_repo_walk_path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "tests" / "eval" / "fixtures"
_RUNNER = CliRunner()
_RUNNER_TIMEOUT_BUDGET_SECONDS = 0.05
_SLOW_PROBE_DELAY_SECONDS = 2.0
_MAX_BOUNDED_WALL_SECONDS = 1.0


def _vertex_smoke_module() -> str:
    """Return the configured ``smoke_module`` for the ``vertex`` provider row."""
    for row in PROVIDER_REQUIREMENTS:
        if row.name == "vertex":
            return row.smoke_module
    raise AssertionError("vertex provider row missing from PROVIDER_REQUIREMENTS")


def _make_orch(*, llm: FakeLLM | None = None, repo: FakeRepository | None = None) -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=llm or FakeLLM(), repo=repo))
    orch.register(ChatAgent())
    return orch


# ── D-1: Vertex smoke import matches google.genai runtime ────────────────────


def test_d1_vertex_smoke_import_matches_google_genai_runtime() -> None:
    """``PROVIDER_REQUIREMENTS['vertex']`` & ``IMAGE_SMOKE_IMPORTS`` smoke ``google.genai``."""
    smoke_modules = {part.strip() for part in _makefile.variable("IMAGE_SMOKE_IMPORTS").split(",")}
    assert _vertex_smoke_module() == "google.genai"
    assert "google.genai" in smoke_modules
    assert "vertexai" not in smoke_modules


# ── D-2: /readyz and check_ready honor ready_timeout_seconds ─────────────────


async def test_d2_check_ready_bounds_hung_llm_ping(caplog: pytest.LogCaptureFixture) -> None:
    """``check_ready(..., timeout_seconds=...)`` fails closed on a stalled LLM ping."""
    orch = _make_orch(llm=FakeLLM(ping_delay_seconds=_SLOW_PROBE_DELAY_SECONDS))
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="mangomas.api.health"):
        report = await check_ready(orch, timeout_seconds=_RUNNER_TIMEOUT_BUDGET_SECONDS)
    elapsed = time.monotonic() - t0

    assert elapsed < _MAX_BOUNDED_WALL_SECONDS
    assert report.ready is False
    statuses = {c.name: c for c in report.checks}
    assert statuses["llm"].status == "error"
    assert "timed out after" in statuses["llm"].detail
    assert "LLM ping failed" in caplog.text


async def test_d2_check_ready_bounds_hung_db_probe(caplog: pytest.LogCaptureFixture) -> None:
    """``check_ready(..., timeout_seconds=...)`` bounds the DB ``list_turns`` probe too."""
    orch = _make_orch(
        llm=FakeLLM(),
        repo=FakeRepository(list_turns_delay_seconds=_SLOW_PROBE_DELAY_SECONDS),
    )
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="mangomas.api.health"):
        report = await check_ready(orch, timeout_seconds=_RUNNER_TIMEOUT_BUDGET_SECONDS)
    elapsed = time.monotonic() - t0

    assert elapsed < _MAX_BOUNDED_WALL_SECONDS
    assert report.ready is False
    statuses = {c.name: c for c in report.checks}
    assert statuses["llm"].status == "ok"
    assert statuses["db"].status == "error"
    assert "timed out after" in statuses["db"].detail
    assert "DB probe failed" in caplog.text


@pytest.mark.parametrize("path", READINESS_PATHS)
@pytest.mark.parametrize("hang_target", ["llm", "db"])
def test_d2_http_readiness_routes_enforce_ready_timeout_env(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    hang_target: str,
) -> None:
    """``GET /readyz`` and ``/ready`` enforce ``MANGOMAS_API__READY_TIMEOUT_SECONDS``."""
    monkeypatch.setenv("MANGOMAS_API__READY_TIMEOUT_SECONDS", str(_RUNNER_TIMEOUT_BUDGET_SECONDS))
    llm = FakeLLM(
        ping_delay_seconds=_SLOW_PROBE_DELAY_SECONDS if hang_target == "llm" else 0.0,
    )
    repo = FakeRepository(
        list_turns_delay_seconds=_SLOW_PROBE_DELAY_SECONDS if hang_target == "db" else 0.0,
    )
    web_app = create_app(orchestrator=_make_orch(llm=llm, repo=repo))

    t0 = time.monotonic()
    with TestClient(web_app) as client:
        response = client.get(path)
    elapsed = time.monotonic() - t0

    assert elapsed < _MAX_BOUNDED_WALL_SECONDS
    assert response.status_code == 503
    body: dict[str, Any] = response.json()
    assert body["ready"] is False
    failed = [c for c in body["checks"] if c["name"] == hang_target]
    assert len(failed) == 1
    assert failed[0]["status"] == "error"
    assert "timed out after" in failed[0]["detail"]


async def test_d2_check_ready_unbounded_default_preserves_backcompat() -> None:
    """Direct callers omitting ``timeout_seconds`` keep unbounded completion."""
    orch = _make_orch(llm=FakeLLM(ping_delay_seconds=0.01), repo=FakeRepository())
    report = await check_ready(orch)
    assert report.ready is True


# ── D-3: MANGOMAS_EVAL__OUTPUT_DIR drives artifact sink default paths ─────────


def test_d3_cli_eval_uses_output_dir_for_json_and_sqlite_sinks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cli_logging_calls: list[dict[str, object]],
) -> None:
    """``MANGOMAS_EVAL__OUTPUT_DIR`` supplies defaults for ``json_file`` and ``sqlite_results``."""
    forbid_real_orchestrator(monkeypatch)
    monkeypatch.setattr(cli_runtime, "_build", _make_orch)
    out_dir = tmp_path / "artifacts-out"
    monkeypatch.setenv("MANGOMAS_EVAL__OUTPUT_DIR", str(out_dir))
    monkeypatch.setenv(
        "MANGOMAS_EVAL__SINKS", json.dumps(["console", "json_file", "sqlite_results"])
    )
    monkeypatch.setenv("MANGOMAS_EVAL__SINK_OPTIONS", "{}")

    result = _RUNNER.invoke(
        cli_app,
        [
            "eval",
            "--dataset",
            str(_FIXTURES_DIR / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--target",
            "echo",
        ],
    )

    assert result.exit_code == 0, result.output
    assert cli_logging_calls == [{"verbose": False}]
    report_path = out_dir / "report.json"
    sqlite_path = out_dir / "results.db"
    assert report_path.is_file(), f"missing {report_path}"
    assert sqlite_path.is_file(), f"missing {sqlite_path}"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["dataset_size"] >= 1


def test_d3_build_sinks_precedence_and_strict_validation(tmp_path: Path) -> None:
    """Precedence is ``--output-json`` > ``sink_options`` > ``output_dir``; invalid values fail."""
    default_dir = tmp_path / "default-dir"
    explicit_json = tmp_path / "explicit.json"
    flag_json = tmp_path / "flag.json"

    # output_dir seeds omitted paths, while explicit sink_options wins over output_dir.
    sinks_with_explicit = _build_sinks(
        ["json_file", "sqlite_results"],
        {"json_file": {"path": str(explicit_json)}},
        None,
        output_dir=str(default_dir),
    )
    assert Path(sinks_with_explicit[0]._path) == explicit_json  # type: ignore[attr-defined]
    assert Path(sinks_with_explicit[1]._path) == default_dir / "results.db"  # type: ignore[attr-defined]

    # --output-json overrides explicit sink_options and output_dir.
    sinks_with_flag = _build_sinks(
        ["json_file"],
        {"json_file": {"path": str(explicit_json)}},
        str(flag_json),
        output_dir=str(default_dir),
    )
    assert Path(sinks_with_flag[0]._path) == flag_json  # type: ignore[attr-defined]

    # Explicit empty path must still fail closed even when output_dir is supplied.
    with pytest.raises(ConfigError, match="json_file sink requires a string 'path' option"):
        _build_sinks(
            ["json_file"],
            {"json_file": {"path": ""}},
            None,
            output_dir=str(default_dir),
        )

    # Without output_dir, missing path still raises ConfigError (3-arg compatibility).
    with pytest.raises(ConfigError):
        _build_sinks(["json_file"], {}, None)


def test_d3_cli_eval_empty_output_dir_fails_closed_when_path_missing(
    monkeypatch: pytest.MonkeyPatch,
    cli_logging_calls: list[dict[str, object]],
) -> None:
    """Blank ``MANGOMAS_EVAL__OUTPUT_DIR`` must not synthesize a relative '.' path."""
    forbid_real_orchestrator(monkeypatch)
    monkeypatch.setenv("MANGOMAS_EVAL__OUTPUT_DIR", "   ")
    monkeypatch.setenv("MANGOMAS_EVAL__SINKS", json.dumps(["json_file"]))
    monkeypatch.setenv("MANGOMAS_EVAL__SINK_OPTIONS", "{}")

    result = _RUNNER.invoke(
        cli_app,
        [
            "eval",
            "--dataset",
            str(_FIXTURES_DIR / "all_pass.jsonl"),
            "--scorer",
            "exact_match",
            "--target",
            "echo",
        ],
    )
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert cli_logging_calls == [{"verbose": False}]


# ── D-4: Agent, skill, and architecture/adapter docs reflect google.genai ────


def test_d4_rag_and_vertex_docs_reflect_google_genai_and_aclose() -> None:
    """Skills, agents, and docs must reflect the PR #135 ``google.genai`` migration."""
    rag_skill = (_REPO_ROOT / CLAUDE_SKILLS_DIR_RELPATH / "mango-rag" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    rag_agent = (_REPO_ROOT / CLAUDE_AGENTS_DIR_RELPATH / "mango-rag-dev.md").read_text(
        encoding="utf-8"
    )
    vertex_doc = (_REPO_ROOT / "docs" / "adapters" / "vertex.md").read_text(encoding="utf-8")
    c1_doc = (_REPO_ROOT / "docs" / "architecture" / "c1-context.md").read_text(encoding="utf-8")
    c2_doc = (_REPO_ROOT / "docs" / "architecture" / "c2-container.md").read_text(encoding="utf-8")
    cloud_doc = (_REPO_ROOT / "docs" / "architecture" / "cloud-providers.md").read_text(
        encoding="utf-8"
    )

    assert "vertexai" not in rag_agent
    assert "google.genai" in rag_agent
    assert "vertexai" not in rag_skill
    assert "google.genai" in rag_skill
    stale_aclose_claim = (
        "`NoTransportAcloseMixin` supplies the no-op `aclose()` for backends owning\n"
        "  no sockets (`sentence_transformers`, `vertex`)"
    )
    assert stale_aclose_claim not in rag_skill
    assert "close_genai_client" in rag_skill

    for doc_name, text in (
        ("docs/adapters/vertex.md", vertex_doc),
        ("docs/architecture/c1-context.md", c1_doc),
        ("docs/architecture/c2-container.md", c2_doc),
        ("docs/architecture/cloud-providers.md", cloud_doc),
    ):
        assert "vertexai.generative_models" not in text, f"{doc_name} still cites vertexai"
        assert "google.genai" in text, f"{doc_name} missing google.genai"


# ── D-5: Readiness timeout & eval gating finite / NumPy scalar validation ───


@pytest.mark.parametrize("bad_timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_d5_api_settings_ready_timeout_rejects_invalid_values(bad_timeout: float) -> None:
    """``APISettings.ready_timeout_seconds`` fails closed on non-positive / non-finite bounds."""
    with pytest.raises(ValidationError):
        APISettings(ready_timeout_seconds=bad_timeout)


@pytest.mark.parametrize(
    "bad_timeout",
    [True, False, "1.0", 0.0, -0.5, float("nan"), float("inf")],
)
async def test_d5_check_ready_validates_timeout_scalars(bad_timeout: Any) -> None:
    """``check_ready`` rejects non-positive, boolean, string, and non-finite budgets."""
    orch = _make_orch(llm=FakeLLM(), repo=FakeRepository())
    with pytest.raises(ConfigError, match="ready_timeout_seconds must be a positive finite number"):
        await check_ready(orch, timeout_seconds=bad_timeout)


async def test_d5_check_ready_accepts_real_and_numpy_scalars_with_structured_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``check_ready`` accepts ``numbers.Real`` / NumPy scalars and emits structured events."""
    orch = _make_orch(llm=FakeLLM(ping_delay_seconds=0.0), repo=FakeRepository())
    budgets: list[Any] = [Fraction(1, 20)]
    try:
        np_mod = importlib.import_module("numpy")
    except ModuleNotFoundError:
        pass
    else:
        budgets.append(np_mod.float64(0.05))

    for budget in budgets:
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="mangomas.api.health"):
            report = await check_ready(orch, timeout_seconds=budget)
        assert report.ready is True
        events = [getattr(r, "event", None) for r in caplog.records]
        assert "readiness_probe_ok" in events
        assert "readiness_report" in events

    hung_orch = _make_orch(llm=FakeLLM(ping_delay_seconds=_SLOW_PROBE_DELAY_SECONDS))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="mangomas.api.health"):
        hung_report = await check_ready(hung_orch, timeout_seconds=_RUNNER_TIMEOUT_BUDGET_SECONDS)
    assert hung_report.ready is False
    failed_records = [
        r for r in caplog.records if getattr(r, "event", None) == "readiness_probe_failed"
    ]
    assert len(failed_records) == 1
    assert getattr(failed_records[0], "timed_out", None) is True


def test_d5_eval_sink_artifacts_and_gating_finite_guards() -> None:
    """``DEFAULT_SINK_ARTIFACTS`` is immutable and ``_resolve_gating`` rejects ``bool``/``NaN``."""
    with pytest.raises(TypeError):
        DEFAULT_SINK_ARTIFACTS["custom"] = ("path", "x.json")  # type: ignore[index]

    cfg = EvalSettings()
    gating_none = _resolve_gating(
        gate=None,
        min_mean_score=None,
        min_pass_rate=None,
        fail_on_error=True,
        max_mean_cost_usd=None,
        min_pass_pow_k=None,
        cfg=cfg,
    )
    assert gating_none == (True, None, None, True, None, None)

    valid_min: Any = Fraction(4, 5)
    gating_valid = _resolve_gating(
        gate=True,
        min_mean_score=valid_min,
        min_pass_rate=0.5,
        fail_on_error=None,
        max_mean_cost_usd=1,
        min_pass_pow_k=0.5,
        cfg=cfg,
    )
    assert gating_valid[0] is True
    assert gating_valid[1] == pytest.approx(0.8)
    assert type(gating_valid[1]) is float
    assert type(gating_valid[4]) is float

    for invalid in (True, False, float("nan"), float("inf"), -0.1, 1.1):
        with pytest.raises(typer.Exit) as exc_info:
            _resolve_gating(
                gate=True,
                min_mean_score=invalid,
                min_pass_rate=None,
                fail_on_error=None,
                max_mean_cost_usd=None,
                min_pass_pow_k=None,
                cfg=cfg,
            )
        assert exc_info.value.exit_code == EXIT_CONFIG_ERROR

    valid_drop: Any = Fraction(1, 10)
    pass_drop: Any = None
    try:
        np_mod = importlib.import_module("numpy")
    except ModuleNotFoundError:
        pass
    else:
        pass_drop = np_mod.float64(0.2)

    reg_ok = _resolve_regression(
        baseline="baseline.json",
        max_mean_score_drop=valid_drop,
        max_pass_rate_drop=pass_drop,
        allow_new_failures=False,
        cfg=cfg,
    )
    assert reg_ok[1] == pytest.approx(0.1)
    assert type(reg_ok[1]) is float
    if pass_drop is not None:
        assert type(reg_ok[2]) is float
        assert reg_ok[2] == pytest.approx(0.2)
    else:
        assert reg_ok[2] is None
    assert reg_ok[3] is False

    for bad_drop in (True, float("nan"), -0.05):
        with pytest.raises(typer.Exit) as exc_info:
            _resolve_regression(
                baseline="baseline.json",
                max_mean_score_drop=bad_drop,
                max_pass_rate_drop=None,
                allow_new_failures=None,
                cfg=cfg,
            )
        assert exc_info.value.exit_code == EXIT_CONFIG_ERROR


# ── D-6: Shared repo-walk exclusion covers secondary virtualenvs & scratch ───


@pytest.mark.parametrize(
    ("relpath", "expected_ignored"),
    [
        (".git/config", True),
        (".venv/Lib/CLAUDE.md", True),
        (".venv-gpu/Lib/CLAUDE.md", True),
        (".venv-aqa311/Lib/AGENTS.md", True),
        ("venv/Lib/agent.md", True),
        ("scratch/notes.md", True),
        ("artifacts/report.md", True),
        ("AGENTS.md", False),
        ("CLAUDE.md", False),
        ("src/mangomas/api/CLAUDE.md", False),
        (".claude/agents/mango-rag-dev.md", False),
    ],
)
def test_d6_ignored_repo_walk_path_filters_secondary_venvs_only(
    relpath: str, expected_ignored: bool
) -> None:
    """``is_ignored_repo_walk_path`` ignores secondary virtualenvs without dropping live docs."""
    assert is_ignored_repo_walk_path(relpath) is expected_ignored


# ── D-7: Main-parity C4, observability, eval, and agent corpus docs sync ────


def test_d7_architecture_eval_and_agent_docs_match_main_contracts() -> None:
    """Updated architecture, observability, eval, and agent docs reflect current contracts."""
    c4_doc = (_REPO_ROOT / "docs" / "architecture" / "c4-code.md").read_text(encoding="utf-8")
    c3_doc = (_REPO_ROOT / "docs" / "architecture" / "c3-component.md").read_text(encoding="utf-8")
    obs_doc = (_REPO_ROOT / "docs" / "architecture" / "observability.md").read_text(
        encoding="utf-8"
    )
    eval_doc = (_REPO_ROOT / "docs" / "eval" / "harness.md").read_text(encoding="utf-8")

    for required in (
        "PlannerAgent",
        "ReviewerAgent",
        "VertexEmbeddingClient",
        "close_genai_client",
    ):
        assert required in c4_doc, f"c4-code.md missing {required}"
    assert "app.state.ready_timeout_seconds" in c3_doc
    assert "DEFAULT_SINK_ARTIFACTS" in c3_doc
    assert "mangomas.correlation.correlation_id" in obs_doc
    assert "sanitize_inbound_correlation_id" in obs_doc
    assert "eval_sink_default_path" in eval_doc
    assert "DEFAULT_SINK_ARTIFACTS" in eval_doc
    assert "mangomas.correlation.correlation_id_var" not in eval_doc
