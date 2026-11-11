"""Tests proving CI quality gate falsifiability (exit code 3) under regression,
threshold drops, errors, and Vertex AI evaluation targets.

Milestone B2 / Story 5.1.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

# Importing the CLI main module wires the scorer and sink registries.
import mangomas.cli.main as cli_main  # noqa: F401
from mangomas.adapters.embeddings.vertex import VertexEmbeddingClient
from mangomas.adapters.llm.vertex import VertexClient
from mangomas.agents import ChatAgent
from mangomas.cli import _runtime as cli_runtime
from mangomas.cli.exit_codes import EVAL_GATE_EXIT_CODE
from mangomas.cli.main import app
from mangomas.config import DEFAULT_VERTEX_LOCATION, get_settings
from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import Message
from mangomas.eval.scorers.llm_judge import judge_prompt_sha256
from mangomas.utils import clock as app_clock
from tests._seam_guards import forbid_real_orchestrator
from tests.constants import DEFAULT_EMBEDDINGS_MODEL, TEST_VERTEX_PROJECT
from tests.fakes import FakeLLM, FakeRepository, FakeVertexGenerativeModel, _FakeGenerationResponse


@dataclass
class _FakeEmbedding:
    values: list[float]


@dataclass
class _FakeModelClient:
    """Fake Vertex TextEmbeddingModel client returning fixed-vector embeddings."""

    values: list[float]

    async def get_embeddings_async(self, texts: list[str]) -> list[_FakeEmbedding]:
        return [_FakeEmbedding(values=list(self.values)) for _ in texts]


class _FakeAlternatingModelClient:
    """Fake Vertex TextEmbeddingModel client alternating orthogonal vectors."""

    def __init__(self) -> None:
        self._count = 0

    async def get_embeddings_async(self, texts: list[str]) -> list[_FakeEmbedding]:
        res: list[_FakeEmbedding] = []
        for _ in texts:
            if self._count % 2 == 0:
                res.append(_FakeEmbedding(values=[1.0, 0.0]))
            else:
                res.append(_FakeEmbedding(values=[0.0, 1.0]))
            self._count += 1
        return res


class _FakeJudgeGenerativeModel(FakeVertexGenerativeModel):
    """Fake Gemini GenerativeModel distinguishing agent responses from judge JSON evaluations."""

    def __init__(self, *, judge_reply: str) -> None:
        super().__init__()
        self.judge_reply = judge_reply

    async def generate_content_async(
        self,
        contents: Any,
        *,
        generation_config: dict[str, Any] | None = None,
        stream: bool = False,
        **_: Any,
    ) -> Any:
        self.calls.append(
            {
                "contents": contents,
                "generation_config": generation_config,
                "stream": stream,
            }
        )
        contents_str = str(contents)
        if "PREDICTION" in contents_str or "evaluator" in contents_str:
            return _FakeGenerationResponse(text=self.judge_reply)
        return _FakeGenerationResponse(text="agent prediction")


class _FailingLLM(FakeLLM):
    """LLM client that raises an exception during generation."""

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        del messages, temperature, max_tokens
        raise RuntimeError("LLM transient failure during eval row execution")


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Ensure each test runs with a fresh Settings cache."""
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _stub_cli_orchestrator(
    eval_orchestrator: Orchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub default CLI orchestrator with an offline fake orchestrator."""
    forbid_real_orchestrator(monkeypatch)
    monkeypatch.setattr(cli_runtime, "_build", lambda: eval_orchestrator)


def test_gate_falsifiability_mean_score_below_threshold(fixtures_dir: Path) -> None:
    """Gate trips with exit code 3 when mean_score is below configured threshold."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.8",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout
    assert "mean_score" in result.stdout


def test_gate_falsifiability_mean_score_boundary_pass(fixtures_dir: Path) -> None:
    """Gate passes with exit code 0 when mean_score satisfies configured threshold."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.66",
        ],
    )
    assert result.exit_code == 0
    assert "gate=PASS" in result.stdout


def test_gate_falsifiability_pass_rate_below_threshold(fixtures_dir: Path) -> None:
    """Gate trips with exit code 3 when pass_rate is below threshold."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--min-pass-rate",
            "0.8",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout
    assert "pass_rate" in result.stdout


def test_gate_falsifiability_baseline_mean_score_drop(fixtures_dir: Path, tmp_path: Path) -> None:
    """Gate trips with exit code 3 when mean_score drops beyond max allowed delta."""
    baseline_path = tmp_path / "baseline.json"
    runner = CliRunner()

    # Step 1: Save baseline report from all_pass (mean_score = 1.0)
    base_res = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--output-json",
            str(baseline_path),
        ],
    )
    assert base_res.exit_code == 0

    # Step 2: Run mixed.jsonl (mean_score = 0.667; drop = 0.333 > 0.100)
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--baseline",
            str(baseline_path),
            "--max-mean-score-drop",
            "0.1",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout
    assert "mean_score dropped" in result.stdout


def test_gate_falsifiability_baseline_pass_rate_drop(fixtures_dir: Path, tmp_path: Path) -> None:
    """Gate trips with exit code 3 when pass_rate drops beyond max allowed delta."""
    baseline_path = tmp_path / "baseline.json"
    runner = CliRunner()

    # Step 1: Save baseline report from all_pass (pass_rate = 1.0)
    base_res = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--output-json",
            str(baseline_path),
        ],
    )
    assert base_res.exit_code == 0

    # Step 2: Run mixed.jsonl (pass_rate = 0.667; drop = 0.333 > 0.100)
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--baseline",
            str(baseline_path),
            "--max-pass-rate-drop",
            "0.1",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout
    assert "pass_rate dropped" in result.stdout


def test_gate_falsifiability_no_allow_new_failures(fixtures_dir: Path, tmp_path: Path) -> None:
    """Gate trips with exit code 3 when a previously passing row regresses."""
    baseline_path = tmp_path / "baseline.json"
    runner = CliRunner()

    # Step 1: Baseline run on all_pass.jsonl (row p1 passes)
    base_res = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--output-json",
            str(baseline_path),
        ],
    )
    assert base_res.exit_code == 0

    # Step 2: Run a dataset where row p1 now fails
    regressed_dataset = tmp_path / "regressed.jsonl"
    regressed_dataset.write_text(
        json.dumps(
            {
                "id": "p1",
                "messages": [{"role": "user", "content": "say stub"}],
                "expected": "unmatched-expected-reply",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(regressed_dataset),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--baseline",
            str(baseline_path),
            "--no-allow-new-failures",
            "--max-mean-score-drop",
            "1.0",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout
    assert "1 row regression(s): p1" in result.stdout


def test_gate_falsifiability_allow_new_failures_permitted_by_default(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Allowing new failures permits passing with exit 0 when score drops are unconstrained."""
    baseline_path = tmp_path / "baseline.json"
    runner = CliRunner()

    base_res = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--output-json",
            str(baseline_path),
        ],
    )
    assert base_res.exit_code == 0

    regressed_dataset = tmp_path / "regressed.jsonl"
    regressed_dataset.write_text(
        json.dumps(
            {
                "id": "p1",
                "messages": [{"role": "user", "content": "say stub"}],
                "expected": "unmatched-expected-reply",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(regressed_dataset),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--baseline",
            str(baseline_path),
            "--allow-new-failures",
            "--max-mean-score-drop",
            "1.0",
        ],
    )
    assert result.exit_code == 0
    assert "gate=PASS" in result.stdout


def test_gate_falsifiability_fail_on_error(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate trips with exit code 3 when any row errors under --fail-on-error."""
    failing_orch = Orchestrator(AgentContext(llm=_FailingLLM(), repo=FakeRepository()))
    failing_orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: failing_orch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--fail-on-error",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout
    assert "errored (fail_on_error)" in result.stdout


def test_gate_falsifiability_sinks_emitted_before_exit_code_3(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """JSON file sink is fully serialized to disk even when the quality gate trips exit 3."""
    report_path = tmp_path / "tripped_gate_report.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.9",
            "--output-json",
            str(report_path),
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert report_path.is_file()

    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["gate"]["passed"] is False
    assert len(data["gate"]["reasons"]) > 0
    assert data["dataset_size"] == 3


_VERTEX_JUDGE_MODEL = "gemini-2.5-flash"
_CALIBRATION_FROZEN_NOW = datetime(2026, 11, 10, tzinfo=UTC)


def _seed_calibrated_vertex_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed a valid JudgeCalibrationRecord for ``_VERTEX_JUDGE_MODEL`` (spec-0047 / spec-0048)."""
    cal_path = tmp_path / "vertex_judge_calibration.json"
    cal_path.write_text(
        json.dumps(
            {
                "judge_model": _VERTEX_JUDGE_MODEL,
                "measured_at": "2026-11-01",
                "sample_size": 300,
                "cohens_kappa": 0.9,
                "self_preference": "measured_unbiased",
                "prompt_sha256": judge_prompt_sha256(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MANGOMAS_LLM__MODEL", _VERTEX_JUDGE_MODEL)
    monkeypatch.setenv("MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH", str(cal_path))
    monkeypatch.setattr(app_clock, "now", lambda: _CALIBRATION_FROZEN_NOW)


def test_gate_falsifiability_vertex_llm_judge_passes(
    fixtures_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibrated VertexClient with LLM judge passes gate when score meets threshold."""
    _seed_calibrated_vertex_judge(tmp_path, monkeypatch)
    fake_model = _FakeJudgeGenerativeModel(
        judge_reply='{"score": 0.95, "rationale": "semantically equivalent"}'
    )
    vertex_llm = VertexClient(
        project_id=TEST_VERTEX_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=_VERTEX_JUDGE_MODEL,
        client=fake_model,
    )
    orch = Orchestrator(AgentContext(llm=vertex_llm, repo=FakeRepository()))
    orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "llm_judge",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.7",
        ],
    )
    assert result.exit_code == 0
    assert "judge=llm_judge calibrated" in result.stdout
    assert "gate=PASS" in result.stdout
    assert "mean_score=0.950" in result.stdout


def test_gate_falsifiability_vertex_llm_judge_fails_exit_3(
    fixtures_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibrated VertexClient with LLM judge trips exit code 3 when score is sub-threshold."""
    _seed_calibrated_vertex_judge(tmp_path, monkeypatch)
    fake_model = _FakeJudgeGenerativeModel(
        judge_reply='{"score": 0.35, "rationale": "inaccurate and incomplete"}'
    )
    vertex_llm = VertexClient(
        project_id=TEST_VERTEX_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=_VERTEX_JUDGE_MODEL,
        client=fake_model,
    )
    orch = Orchestrator(AgentContext(llm=vertex_llm, repo=FakeRepository()))
    orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "llm_judge",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.7",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "judge=llm_judge calibrated" in result.stdout
    assert "gate=FAIL" in result.stdout
    assert "mean_score 0.350 < min_mean_score 0.700" in result.stdout


def test_gate_falsifiability_vertex_llm_judge_uncalibrated_reported_not_gated(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uncalibrated Vertex LLM judge reports sub-threshold score without gating (spec-0047)."""
    fake_model = _FakeJudgeGenerativeModel(
        judge_reply='{"score": 0.35, "rationale": "inaccurate and incomplete"}'
    )
    vertex_llm = VertexClient(
        project_id=TEST_VERTEX_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=_VERTEX_JUDGE_MODEL,
        client=fake_model,
    )
    orch = Orchestrator(AgentContext(llm=vertex_llm, repo=FakeRepository()))
    orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "llm_judge",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.7",
        ],
    )
    assert result.exit_code == 0
    assert "judge=llm_judge uncalibrated" in result.stdout
    assert "gate=PASS (note: llm_judge uncalibrated" in result.stdout
    assert "mean_score=0.350" in result.stdout


def test_gate_falsifiability_vertex_embedding_scorer_passes(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VertexEmbeddingClient with embedding scorer passes when cosine similarity is high."""
    embedding_model = _FakeModelClient(values=[1.0, 0.0])
    vertex_embed = VertexEmbeddingClient(
        project_id=TEST_VERTEX_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=DEFAULT_EMBEDDINGS_MODEL,
        client=embedding_model,
    )
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=FakeRepository(), embeddings=vertex_embed))
    orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "embedding",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.8",
        ],
    )
    assert result.exit_code == 0
    assert "gate=PASS" in result.stdout
    assert "mean_score=1.000" in result.stdout


def test_gate_falsifiability_vertex_embedding_scorer_fails_exit_3(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VertexEmbeddingClient with embedding scorer trips exit 3 when vectors diverge."""
    embedding_model = _FakeAlternatingModelClient()
    vertex_embed = VertexEmbeddingClient(
        project_id=TEST_VERTEX_PROJECT,
        location=DEFAULT_VERTEX_LOCATION,
        model=DEFAULT_EMBEDDINGS_MODEL,
        client=embedding_model,
    )
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=FakeRepository(), embeddings=vertex_embed))
    orch.register(ChatAgent())
    monkeypatch.setattr(cli_runtime, "_build", lambda: orch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "all_pass.jsonl"),
            "-s",
            "embedding",
            "-a",
            "chat",
            "--gate",
            "--min-mean-score",
            "0.75",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout
    assert "mean_score 0.500 < min_mean_score 0.750" in result.stdout


def test_gate_falsifiability_env_driven_settings(
    fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate configuration via environment variables triggers exit code 3 without CLI flags."""
    monkeypatch.setenv("MANGOMAS_EVAL__GATE_ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_EVAL__MIN_MEAN_SCORE", "0.99")

    runner = CliRunner()
    # Runs without --gate flag, driven entirely by env vars
    result = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
        ],
    )
    assert result.exit_code == EVAL_GATE_EXIT_CODE
    assert "gate=FAIL" in result.stdout

    # Explicit --no-gate disables gating
    no_gate_res = runner.invoke(
        app,
        [
            "eval",
            "-d",
            str(fixtures_dir / "mixed.jsonl"),
            "-s",
            "exact_match",
            "-a",
            "chat",
            "--no-gate",
        ],
    )
    assert no_gate_res.exit_code == 0
