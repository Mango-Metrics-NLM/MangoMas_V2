"""Regression + AQA guards for the 2026-11-12 origin-delta validation (spec-0050).

Branch ``sdlc/origin-delta-aqa-20261112``, cut from
``origin/feat/initial-release@9f14fe4`` (`spec-0043..0049` beyond `6fbf689`).
Every guard is hermetic (fakes, ``CliRunner``, in-process calls — no sockets)
and was verified RED on unmodified ``9f14fe4`` prior to landing fixes. See
``docs/testing/rca-20261112.md`` for root causes and mutation proofs.
"""

from __future__ import annotations

import ast
import json
import math
import os
import subprocess
import sys
import types
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from mangomas.adapters.vector import VectorMatch
from mangomas.cli.commands._eval_config import _build_scorer
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR
from mangomas.cli.main import app
from mangomas.config import EvalSettings, RepairSettings
from mangomas.core.tools import ToolEffects, ToolSpec
from mangomas.errors import ConfigError, ToolExecutionError
from mangomas.eval.baseline import ReportDiff, _report_from_dict
from mangomas.eval.dataset import DatasetError, _parse_row
from mangomas.eval.gate import GateResult, evaluate_regression_gate, merge_gate_results
from mangomas.eval.judge_calibration import JudgeStatus
from mangomas.eval.protocol import ScorerContext
from mangomas.eval.scorers.composite import build_composite
from mangomas.eval.scorers.embedding import EmbeddingScorer, _cosine_similarity
from mangomas.eval.scorers.json_field import JsonFieldScorer
from mangomas.eval.scorers.json_field import _as_number as _scorer_as_number
from mangomas.harness.isolation import (
    DOCKER_CONTAINER_PYTHON,
    DockerIsolationBackend,
    NoIsolationBackend,
    WslBwrapIsolationBackend,
    resolve_sandbox_argv,
    windows_to_wsl_path,
)
from mangomas.harness.repair_sandbox import validate_loopback_llm_url, validate_promotion_target
from mangomas.harness.tool_policy import validate_tool_arguments
from mangomas.secrets.gcp import GCPSecretManagerProvider
from mangomas.workflow.predicate._json_field import _as_number as _predicate_as_number
from mangomas.workflow.predicate._spec import PredicateSpec
from tests.constants.live import DEFAULT_VERTEX_TEST_MODEL

_RUNNER = CliRunner()
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── D-1: eval parallelism validation & orchestrator cleanup ───────────────────


@pytest.mark.parametrize("bad_parallelism", [0, -1])
def test_d1_eval_settings_rejects_non_positive_parallelism(bad_parallelism: int) -> None:
    """``EvalSettings`` must reject ``parallelism < 1`` at parse time."""
    with pytest.raises(ValidationError, match=r"eval\.parallelism must be >= 1"):
        EvalSettings(parallelism=bad_parallelism)


def test_d1_cli_eval_zero_parallelism_exits_config_error(tmp_path: Path) -> None:
    """``mangomas eval -p 0`` must exit 2 (``EXIT_CONFIG_ERROR``) with no traceback."""
    ds = tmp_path / "min.jsonl"
    ds.write_text(
        json.dumps({"id": "r1", "messages": [{"role": "user", "content": "hi"}], "expected": "hi"})
        + "\n",
        encoding="utf-8",
    )
    result = _RUNNER.invoke(app, ["eval", "-d", str(ds), "-t", "echo", "-p", "0"])
    assert result.exit_code == EXIT_CONFIG_ERROR, (
        f"expected exit {EXIT_CONFIG_ERROR}, got {result.exit_code}; "
        f"output={result.output!r} exc={result.exception!r}"
    )
    assert "Eval configuration error:" in result.output


def test_d1_cli_eval_closes_orchestrator_when_runner_init_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``EvalRunner(...)`` raises after ``build_or_exit``, ``orch`` is still closed."""
    ds = tmp_path / "min.jsonl"
    ds.write_text(
        json.dumps({"id": "r1", "messages": [{"role": "user", "content": "hi"}], "expected": "hi"})
        + "\n",
        encoding="utf-8",
    )
    fake_orch = object()
    closed: list[object] = []

    monkeypatch.setattr("mangomas.cli._runtime._build", lambda: fake_orch)

    async def _fake_close(orch: object) -> None:
        closed.append(orch)

    monkeypatch.setattr("mangomas.cli._runtime._close_orchestrator", _fake_close)

    def _boom_runner(*_args: object, **_kwargs: object) -> object:
        raise ValueError("synthetic runner init failure")

    monkeypatch.setattr("mangomas.cli.commands.eval.EvalRunner", _boom_runner)

    with pytest.raises(ValueError, match="synthetic runner init failure"):
        _RUNNER.invoke(app, ["eval", "-d", str(ds), "-t", "echo"], catch_exceptions=False)
    assert closed == [fake_orch]


# ── D-2: CLI rag query forwards cfg.vector.max_top_k ──────────────────────────


def test_d2_cli_rag_query_forwards_vector_max_top_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mangomas rag query --top-k N`` must obey ``MANGOMAS_VECTOR__MAX_TOP_K``."""
    recorded_top_k: list[int] = []

    class _FakeEmbeddings:
        async def embed(self, text: str) -> list[float]:
            _ = text
            return [1.0, 0.0]

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        async def aclose(self) -> None:
            return None

    class _FakeVectorStore:
        async def upsert(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)

        async def delete_by_source(self, source: str) -> int:
            _ = source
            return 0

        async def query(
            self,
            embedding: list[float],
            *,
            top_k: int,
        ) -> list[Any]:
            _ = embedding
            recorded_top_k.append(top_k)
            return [
                VectorMatch(
                    id="c1",
                    document="chunk text",
                    metadata={"source": "doc.md", "index": 0},
                    score=0.95,
                )
            ][:top_k]

        async def aclose(self) -> None:
            return None

    class _FakeOrch:
        def __init__(self) -> None:
            self.context = types.SimpleNamespace(
                embeddings=_FakeEmbeddings(),
                vector_store=_FakeVectorStore(),
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setenv("MANGOMAS_EMBEDDINGS__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_VECTOR__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_VECTOR__TOP_K", "1")
    monkeypatch.setenv("MANGOMAS_VECTOR__MAX_TOP_K", "1")
    monkeypatch.setattr("mangomas.cli._runtime._build", _FakeOrch)

    result = _RUNNER.invoke(app, ["rag", "query", "hello", "--top-k", "4"])
    assert result.exit_code == 0, result.output
    assert recorded_top_k == [1]


# ── D-3: EmbeddingSimilarityScorer IEEE-754 finiteness & [-1, 1] clamping ────


@pytest.mark.parametrize(
    ("vec_a", "vec_b"),
    [
        ([float("nan"), 1.0], [1.0, 1.0]),
        ([1.0, 0.0], [float("inf"), 1.0]),
        ([1e308, 1e308], [1e308, 1e308]),
    ],
)
def test_d3_cosine_similarity_fails_closed_on_non_finite(
    vec_a: list[float],
    vec_b: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-finite embedding inputs or overflowing norms collapse safely to ``0.0``."""
    with caplog.at_level("WARNING"):
        cos = _cosine_similarity(vec_a, vec_b)
    assert math.isfinite(cos)
    assert cos == 0.0


async def test_d3_cosine_similarity_and_scorer_clamp_ieee_drift() -> None:
    """Float rounding drift above ``1.0`` must clamp to ``[-1.0, 1.0]`` -> ``[0.0, 1.0]``."""
    drift_vec = [0.3, 0.3, 0.3]
    cos = _cosine_similarity(drift_vec, drift_vec)
    assert -1.0 <= cos <= 1.0

    class _DriftEmbedder:
        async def embed(self, text: str) -> list[float]:
            return drift_vec if text == "a" else [float("nan"), 1.0, 0.0]

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [await self.embed(t) for t in texts]

        async def aclose(self) -> None:
            return None

    scorer = EmbeddingScorer(threshold=0.5)
    drift_score = await scorer.score("a", "a", context=ScorerContext(embeddings=_DriftEmbedder()))
    nan_score = await scorer.score("a", "b", context=ScorerContext(embeddings=_DriftEmbedder()))
    zero_thresh = await EmbeddingScorer(threshold=0.0).score(
        "a", "b", context=ScorerContext(embeddings=_DriftEmbedder())
    )
    assert 0.0 <= drift_score.score <= 1.0 and drift_score.passed is True
    assert nan_score.score == 0.0
    assert nan_score.passed is False
    assert nan_score.metadata.get("error") == "invalid_embedding"
    assert zero_thresh.score == 0.0
    assert zero_thresh.passed is False
    assert zero_thresh.metadata.get("error") == "invalid_embedding"

    class _OrthogonalEmbedder:
        async def embed(self, text: str) -> list[float]:
            return [1.0, 0.0] if text == "a" else [0.0, 1.0]

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [await self.embed(t) for t in texts]

        async def aclose(self) -> None:
            return None

    ortho_score = await scorer.score(
        "a", "b", context=ScorerContext(embeddings=_OrthogonalEmbedder())
    )
    assert ortho_score.score == pytest.approx(0.5)
    assert ortho_score.passed is True
    assert "error" not in ortho_score.metadata


# ── D-4: Non-finite floats & union types in json_field, predicates, ToolPolicy ─


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_d4_json_field_and_predicate_reject_non_finite_numbers(bad: float) -> None:
    """``NaN``/``±Inf`` must fail ``_as_number`` and schema checks in both packages."""
    assert _scorer_as_number(bad) is None
    assert _predicate_as_number(bad) is None
    with pytest.raises(ConfigError):
        JsonFieldScorer(check={"path": "score", "at_least": bad})
    with pytest.raises(ConfigError):
        JsonFieldScorer(check={"path": "score", "equals": bad})
    with pytest.raises(ValidationError):
        PredicateSpec(kind="json_field", field="score", at_least=bad)
    with pytest.raises(ValidationError):
        PredicateSpec(kind="json_field", field="score", equals=bad)


async def test_d4_json_field_rejects_nested_non_finite_and_normalizes_thresholds() -> None:
    """Nested non-finite operands/values fail closed, and real scalar bounds JSON-serialize."""
    with pytest.raises(ConfigError):
        JsonFieldScorer(check={"path": "payload", "equals": {"n": float("inf")}})
    with pytest.raises(ConfigError):
        JsonFieldScorer(check={"path": "items", "equals": [1, {"x": float("nan")}]})

    scorer = JsonFieldScorer(check={"path": "value", "equals": {"n": 100}})
    inf_pred = await scorer.score('{"value": {"n": 1e400}}', "")
    assert inf_pred.passed is False
    assert inf_pred.score == 0.0
    assert inf_pred.metadata.get("error") == "non_finite_value"

    frac_range = JsonFieldScorer(check={"path": "score", "at_least": Fraction(1, 2)})
    frac_result = await frac_range.score('{"score": 0.75}', "")
    assert frac_result.passed is True
    # Report sinks serialize result metadata with json.dumps (must not raise TypeError).
    encoded = json.dumps(frac_result.metadata)
    assert "0.5" in encoded


def test_d4_predicate_spec_preserves_huge_int_equals() -> None:
    """Arbitrary-precision Python ints like ``10**400`` are valid finite integers for equals."""
    huge = 10**400
    spec = PredicateSpec(kind="json_field", field="big", equals=huge)
    assert spec.equals == huge


def test_d4_tool_policy_rejects_non_finite_number_and_checks_union_types() -> None:
    """``ToolPolicy`` must reject ``NaN``/``Inf`` on ``number`` and enforce union ``type`` lists."""
    spec = ToolSpec(
        name="demo",
        description="demo tool",
        effects=ToolEffects.READ_ONLY,
        parameters_schema={
            "type": "object",
            "properties": {
                "weight": {"type": "number"},
                "label": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
    )
    validate_tool_arguments(spec, {"weight": 1.5, "label": "ok"})
    validate_tool_arguments(spec, {"weight": 2, "label": None})

    for bad_num in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ToolExecutionError, match="weight"):
            validate_tool_arguments(spec, {"weight": bad_num})

    with pytest.raises(ToolExecutionError, match="label"):
        validate_tool_arguments(spec, {"label": 123})


# ── D-5: falsy non-dict options / metadata coercion ───────────────────────────


@pytest.mark.parametrize("falsy_non_dict", [[], "", 0, False])
def test_d5_eval_composite_dataset_and_config_reject_falsy_non_dict(
    falsy_non_dict: Any,
) -> None:
    """Falsy non-dicts (``[]``, ``""``, ``0``, ``False``) raise rather than coercing to ``{}``."""
    with pytest.raises(ConfigError, match="options"):
        build_composite([{"name": "exact_match", "options": falsy_non_dict}])
    with pytest.raises(ConfigError, match="options"):
        _build_scorer(["exact_match", "contains"], {"exact_match": falsy_non_dict})
    with pytest.raises(DatasetError, match="metadata"):
        _parse_row(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "expected": "hi",
                "metadata": falsy_non_dict,
            },
            index=1,
            source="test",
        )


# ── D-6: merge_gate_results judge_note & baseline bool/NaN guards ─────────────


def test_d6_merge_gate_results_preserves_excluded_judge_note_regardless_of_order() -> None:
    """When any verdict has ``judge_excluded=True``, its ``judge_note`` survives merging."""
    cal_threshold = GateResult(
        passed=True,
        actual_mean_score=0.5,
        actual_pass_rate=0.5,
        min_mean_score=0.5,
        min_pass_rate=None,
        fail_on_error=False,
        errored=0,
        kind="threshold",
        judge_excluded=False,
        judge_note=None,
    )
    uncal_reg = evaluate_regression_gate(
        ReportDiff(
            baseline_mean_score=1.0,
            current_mean_score=0.5,
            mean_score_delta=-0.5,
            baseline_pass_rate=1.0,
            current_pass_rate=0.5,
            pass_rate_delta=-0.5,
            passed_delta=-1,
            failed_delta=1,
            errored_delta=0,
            regressed_rows=["r1"],
        ),
        max_mean_score_drop=0.1,
        judge=JudgeStatus(
            calibrated=False,
            reason="no calibration record",
            judge_model="m",
            self_preference="not_measured",
        ),
    )
    merged = merge_gate_results([cal_threshold, uncal_reg])
    assert merged is not None
    assert merged.judge_excluded is True
    assert merged.judge_note == uncal_reg.judge_note


@pytest.mark.parametrize(
    "bad_payload",
    [
        ["not-a-mapping"],
        {
            "scorer": "exact_match",
            "agent_name": "chat",
            "dataset_size": 1,
            "passed": 1,
            "failed": 0,
            "errored": 0,
            "mean_score": True,
            "duration_ms": 1.0,
            "rows": [],
        },
        {
            "scorer": "exact_match",
            "agent_name": "chat",
            "dataset_size": 1,
            "passed": 1,
            "failed": 0,
            "errored": 0,
            "mean_score": float("nan"),
            "duration_ms": 1.0,
            "rows": [],
        },
        {
            "scorer": "exact_match",
            "agent_name": "chat",
            "dataset_size": 1,
            "passed": 1,
            "failed": 0,
            "errored": 0,
            "mean_score": 1.0,
            "duration_ms": False,
            "rows": [],
        },
        {
            "scorer": "exact_match",
            "agent_name": "chat",
            "dataset_size": 1,
            "passed": 1,
            "failed": 0,
            "errored": 0,
            "mean_score": 1.0,
            "duration_ms": float("inf"),
            "rows": [],
        },
    ],
)
def test_d6_baseline_report_from_dict_rejects_bool_nan_and_non_mapping(bad_payload: Any) -> None:
    """``load_baseline`` must reject non-mappings, booleans, and non-finite metrics."""
    with pytest.raises(ConfigError, match="Invalid baseline report structure"):
        _report_from_dict(bad_payload)


# ── D-8: resolve_sandbox_argv empty & NoIsolationBackend PYTHONPATH ───────────


def test_d8_resolve_sandbox_argv_rejects_empty_and_no_isolation_sets_pythonpath(
    tmp_path: Path,
) -> None:
    """Empty argv raises ``ToolExecutionError`` and ``NoIsolationBackend`` prepends staged paths."""
    with pytest.raises(ToolExecutionError, match="must not be empty"):
        resolve_sandbox_argv([], interpreter="python", backend_name="none")

    captured_envs: list[dict[str, str]] = []

    def _capture_runner(
        cmd: list[str],
        *,
        env: dict[str, str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_envs.append(dict(env))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    backend = NoIsolationBackend(runner=_capture_runner)
    backend.run(
        ["{python}", "-c", "pass"],
        cwd=tmp_path,
        env={"PYTHONPATH": "/existing/site"},
        timeout_seconds=5.0,
    )
    backend.run(["{python}", "-c", "pass"], cwd=tmp_path, env={}, timeout_seconds=5.0)

    resolved = tmp_path.resolve()
    expected_prefix = f"{resolved / 'src'}{os.pathsep}{resolved}"
    assert captured_envs[0]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured_envs[0]["PYTHONPATH"] == f"{expected_prefix}{os.pathsep}/existing/site"
    assert captured_envs[1]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured_envs[1]["PYTHONPATH"] == expected_prefix


def test_d8_no_isolation_loads_partial_staged_file_ahead_of_editable_install(
    tmp_path: Path,
) -> None:
    """Single staged file without ``__init__.py`` must shadow the installed package (#7)."""
    real_chat = (_REPO_ROOT / "src" / "mangomas" / "agents" / "chat.py").read_text(encoding="utf-8")
    staged_chat = tmp_path / "src" / "mangomas" / "agents" / "chat.py"
    staged_chat.parent.mkdir(parents=True)
    staged_chat.write_text(f'{real_chat}\nSTAGED_MARKER = "from_staging_ok"\n', encoding="utf-8")

    backend = NoIsolationBackend(interpreter=sys.executable)
    proc = backend.run(
        [
            "{python}",
            "-c",
            (
                "import mangomas.agents.chat as c, mangomas.config as cfg; "
                "assert getattr(c, 'STAGED_MARKER', None) == 'from_staging_ok'; "
                "assert hasattr(cfg, 'Settings')"
            ),
        ],
        cwd=tmp_path,
        env={},
        timeout_seconds=15.0,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r} stdout={proc.stdout!r}"


@pytest.mark.parametrize(
    "hook_relpath",
    [
        "src/usercustomize.py",
        "sitecustomize.py",
        "sitecustomize/__init__.py",
        "src/usercustomize/__init__.py",
        "src/sitecustomize.pyc",
        "src/usercustomize.cpython-311.pyc",
        "sitecustomize.pyw",
        "src/usercustomize.pyd",
        "sitecustomize.so",
        "src/evil.pth",
    ],
)
def test_d8_no_isolation_rejects_candidate_startup_hooks(
    tmp_path: Path,
    hook_relpath: str,
) -> None:
    """Staged ``sitecustomize*`` / ``usercustomize*`` / ``*.pth`` must be refused (#8)."""
    hook = tmp_path / hook_relpath
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("import os; os._exit(0)\n", encoding="utf-8")

    backend = NoIsolationBackend(interpreter=sys.executable)
    with pytest.raises(ToolExecutionError):
        backend.run(
            ["{python}", "-c", "import sys; sys.exit(1)"],
            cwd=tmp_path,
            env={},
            timeout_seconds=15.0,
        )


def test_d8_no_isolation_rejects_early_os_exit_bypass(tmp_path: Path) -> None:
    """``os._exit(0)`` bypassing oracle sentinel must raise ``ToolExecutionError`` (#8)."""
    backend = NoIsolationBackend(interpreter=sys.executable)
    with pytest.raises(ToolExecutionError):
        backend.run(
            ["{python}", "-c", "import os; os._exit(0)"],
            cwd=tmp_path,
            env={},
            timeout_seconds=15.0,
        )
    with pytest.raises(ToolExecutionError):
        backend.run(
            ["{python}", "-Bc", "import os; os._exit(0)"],
            cwd=tmp_path,
            env={},
            timeout_seconds=15.0,
        )

    ok = backend.run(
        ["{python}", "-c", "print('oracle_done')"],
        cwd=tmp_path,
        env={},
        timeout_seconds=15.0,
    )
    assert ok.returncode == 0
    assert "oracle_done" in ok.stdout


# ── D-9: loopback URL port overflow & case-insensitive protected promotion ────


def test_d9_repair_sandbox_port_overflow_and_case_insensitive_protected_target(
    tmp_path: Path,
) -> None:
    """Out-of-range ports map to ``ConfigError`` and uppercase protected names remain blocked."""
    with pytest.raises(ConfigError, match="parsed"):
        validate_loopback_llm_url("http://127.0.0.1:99999/v1")

    with pytest.raises(ToolExecutionError, match="protected or governance-surface"):
        validate_promotion_target(
            tmp_path,
            "SRC/MANGOMAS/ERRORS.PY",
            allowed_paths={"SRC/MANGOMAS/ERRORS.PY"},
        )


# ── D-10: Docker --mount CSV + -- separator, WSL paths, RepairSettings ────────


def test_d10_docker_wsl_and_repair_settings_hardening(tmp_path: Path) -> None:
    """Docker checks mount paths + ``--``; WSL handles ``\\\\?\\``; RepairSettings blocks flags."""
    bad_dir = tmp_path / "comma,dir"
    bad_dir.mkdir()
    docker = DockerIsolationBackend(
        runner=lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    with pytest.raises(ToolExecutionError, match="--mount"):
        docker.run(["{python}", "-c", "pass"], cwd=bad_dir, env={}, timeout_seconds=5.0)

    good_cmds: list[list[str]] = []

    def _capture_docker(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        good_cmds.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    DockerIsolationBackend(runner=_capture_docker).run(
        ["{python}", "-c", "pass"],
        cwd=tmp_path,
        env={},
        timeout_seconds=5.0,
    )
    assert good_cmds[0][-5:] == ["--", "mangomas:oracle", DOCKER_CONTAINER_PYTHON, "-c", "pass"]

    assert windows_to_wsl_path(r"\\?\C:\Users\dev\repo") == "/mnt/c/Users/dev/repo"
    for bad_wsl in ("C:relative.py", r"\\server\share\repo"):
        with pytest.raises(ToolExecutionError, match="WSL"):
            windows_to_wsl_path(bad_wsl)

    for field, bad_val in (
        ("oracle_image", "--privileged"),
        ("oracle_memory_limit", "--kernel-memory=1g"),
        ("wsl_distro", "--exec"),
        ("wsl_python", "-c"),
        ("oracle_image", "img bad"),
        ("wsl_distro", "Ubuntu\nbad"),
    ):
        with pytest.raises(ValidationError):
            RepairSettings.model_validate({field: bad_val})


# ── D-12: Vertex test model constant tracks current Gemini Flash ──────────────


def test_d12_default_vertex_test_model_matches_current_flash() -> None:
    """``DEFAULT_VERTEX_TEST_MODEL`` must point to ``gemini-2.5-flash``."""
    assert DEFAULT_VERTEX_TEST_MODEL == "gemini-2.5-flash"


# ── D-13: GCPSecretManagerProvider imports directly from secretmanager submodule


def test_d13_gcp_secrets_avoids_namespace_package_attr_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``secrets/gcp.py`` must import from ``google.cloud.secretmanager`` directly for P2 mypy."""
    gcp_source = (_REPO_ROOT / "src" / "mangomas" / "secrets" / "gcp.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(gcp_source)
    bad_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "google.cloud"
        and any(alias.name == "secretmanager" for alias in node.names)
    ]
    assert bad_imports == [], (
        "`from google.cloud import secretmanager` fails P2 `mypy --strict` "
        "[attr-defined] on namespace package `google.cloud`"
    )

    sentinel_client = object()
    fake_sm = types.ModuleType("google.cloud.secretmanager")
    fake_sm.SecretManagerServiceClient = lambda: sentinel_client  # type: ignore[attr-defined]
    fake_cloud = types.ModuleType("google.cloud")
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", fake_sm)

    provider = GCPSecretManagerProvider(
        project_id="proj",
        timeout_seconds=1.0,
        default_version="latest",
    )
    assert provider._ensure_client() is sentinel_client


@pytest.mark.parametrize(
    "bad_wsl_path",
    [
        "//server/share/repo",
        "//?/UNC/server/share/repo",
        "\\\\?\\Volume{1234}\\repo",
        "//?/Volume{1234}/repo",
        "\\\\.\\PhysicalDrive0",
        "//./C:/repo",
        "\\\\?\\C:relative",
    ],
)
def test_d10_critic_windows_to_wsl_rejects_forward_unc_and_device_paths(bad_wsl_path: str) -> None:
    """Finding #3: reject forward-slash UNC and non-drive Win32 device/extended paths."""
    with pytest.raises(ToolExecutionError):
        windows_to_wsl_path(bad_wsl_path)


def test_d10_critic_windows_to_wsl_accepts_forward_extended_drive_and_validates_init() -> None:
    """Forward extended drive ``//?/C:/...`` maps cleanly and backend __init__ validates tokens."""
    assert windows_to_wsl_path("//?/C:/Users/dev/repo") == "/mnt/c/Users/dev/repo"
    with pytest.raises(ToolExecutionError):
        DockerIsolationBackend(image="--privileged")
    with pytest.raises(ToolExecutionError):
        DockerIsolationBackend(memory_limit="256m --cap-add=ALL")
    with pytest.raises(ToolExecutionError):
        WslBwrapIsolationBackend(distro="--exec")


def test_d6_critic_baseline_validates_counts_slices_and_optional_floats() -> None:
    """Finding #4: baseline rejects bool/float/negative counts, NaN optionals, and bad slices."""
    valid_base = {
        "scorer": "exact_match",
        "agent_name": "chat",
        "dataset_size": 1,
        "passed": 1,
        "failed": 0,
        "errored": 0,
        "mean_score": 1.0,
        "duration_ms": 1.0,
        "rows": [],
    }
    assert _report_from_dict({**valid_base, "slices": None}).slices == {}
    loaded = _report_from_dict(
        {
            **valid_base,
            "mean_cost_usd": 0.01,
            "pass_pow_k": 1.0,
            "trial_pass_rate": 1.0,
            "slices": {
                "cat": {
                    "size": 1,
                    "passed": 1,
                    "failed": 0,
                    "errored": 0,
                    "mean_score": 1.0,
                }
            },
        }
    )
    assert loaded.mean_cost_usd == 0.01

    bad_payloads = [
        {**valid_base, "dataset_size": True},
        {**valid_base, "passed": -1},
        {**valid_base, "trials": 0},
        {**valid_base, "mean_cost_usd": float("nan")},
        {**valid_base, "pass_pow_k": float("inf")},
        {**valid_base, "trial_pass_rate": True},
        {**valid_base, "slices": ["not-a-mapping"]},
    ]
    for payload in bad_payloads:
        with pytest.raises(ConfigError, match="Invalid baseline report structure"):
            _report_from_dict(payload)


def test_d6_critic_gate_rejects_nan_pass_drop_and_preserves_excluded_regression_note() -> None:
    """Finding #4: NaN pass_rate_delta fails gate and excluded note wins over non-excluded."""
    nan_pass_diff = ReportDiff(
        baseline_mean_score=1.0,
        current_mean_score=1.0,
        mean_score_delta=0.0,
        baseline_pass_rate=1.0,
        current_pass_rate=float("nan"),
        pass_rate_delta=float("nan"),
        passed_delta=0,
        failed_delta=0,
        errored_delta=0,
    )
    res = evaluate_regression_gate(nan_pass_diff, max_pass_rate_drop=0.05)
    assert res.passed is False
    assert any("pass_rate drop" in reason for reason in res.reasons)
    finite_pass_diff = ReportDiff(
        baseline_mean_score=1.0,
        current_mean_score=1.0,
        mean_score_delta=0.0,
        baseline_pass_rate=1.0,
        current_pass_rate=0.98,
        pass_rate_delta=-0.02,
        passed_delta=0,
        failed_delta=0,
        errored_delta=0,
    )
    assert evaluate_regression_gate(finite_pass_diff, max_pass_rate_drop=0.05).passed is True

    uncalibrated = JudgeStatus(
        judge_model="fake-judge",
        calibrated=False,
        sample_size=0,
        cohens_kappa=None,
        spearman_rho=None,
        measured_at=None,
        reason="uncalibrated",
    )
    threshold_non_excluded = GateResult(
        passed=True,
        actual_mean_score=0.9,
        actual_pass_rate=1.0,
        fail_on_error=True,
        kind="threshold",
        judge_excluded=False,
        judge_note=uncalibrated.summary(),
    )
    regression_excluded = evaluate_regression_gate(
        ReportDiff(
            baseline_mean_score=1.0,
            current_mean_score=0.5,
            mean_score_delta=-0.5,
            baseline_pass_rate=1.0,
            current_pass_rate=1.0,
            pass_rate_delta=0.0,
            passed_delta=0,
            failed_delta=0,
            errored_delta=0,
        ),
        max_mean_score_drop=0.1,
        judge=uncalibrated,
    )
    merged = merge_gate_results([threshold_non_excluded, regression_excluded])
    assert merged is not None
    assert merged.judge_excluded is True
    assert (
        merged.judge_note is not None and "regression thresholds not applied" in merged.judge_note
    )
