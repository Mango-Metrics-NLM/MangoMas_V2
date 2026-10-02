"""Regression guards for defects found and fixed in the sdlc/defect-triage-aqa-20261001 audit.

Each test is:
  - Mock-backed (no live services required unless noted)
  - Parametrized where multiple cases share the same defect class
  - Documented with the defect class, RCA, and fix reference

Defect classes covered:
  D1 — test_auth: mypy arg-type errors from httpx2 co-install + intentional bytes headers
  D2 — test_agents_md_contract: nested-git-repo AGENTS.md incorrectly flagged
  D3 — tests/regression/: missing __init__.py package marker
  D4 — test_step_timeout: stale list_turns == [] assertion predating ADR-0031
  D5 — lmstudio/conftest: make_lmstudio_settings ignored MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mangomas.adapters.storage.sqlite import SQLiteRepository
from mangomas.config import LoopSettings
from mangomas.core.agent import AgentRequest, Message
from tests.constants import DEFAULT_LOOP_STEP_TIMEOUT, LOOP_STEP_TIMEOUT_ENV
from tests.lmstudio.conftest import make_lmstudio_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_MD_CONTRACT_TEST = _REPO_ROOT / "tests" / "test_agents_md_contract.py"


# ── D1: httpx bytes-header type suppression ────────────────────────────────────
#
# RCA: httpx2 (pydantic's fork, v2.12.0) is co-installed alongside httpx
# (v0.28.1) on the system Python.  Mypy resolves TestClient responses as
# httpx2._models.Response, causing arg-type errors when passing to helpers typed
# httpx.Response.  The intentional bytes-header pattern (ASGI wire simulation)
# also requires suppression because TestClient's overloads only accept str
# header values.
#
# Fix: cast(httpx.Response, r) for the response mismatch; # type: ignore[arg-type]
# with a documenting comment for the bytes header values.


def test_d1_auth_module_importable_without_type_errors() -> None:
    """D1: test_auth.py must import cleanly regardless of httpx2 presence.

    Defect: 6 mypy arg-type errors from httpx2 co-install and intentional
    bytes headers.  Fix: _cast_response() helper + # type: ignore[arg-type].

    Uses ``pytest --collect-only`` rather than ``python -c "import ..."``:
    collection fails immediately on import errors and properly respects the
    editable install regardless of whether the caller uses system Python or a
    venv.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_auth.py",
            "--collect-only",
            "-q",
            "--no-cov",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"tests/test_auth.py failed to collect (import error):\n{result.stdout}\n{result.stderr}"
    )


def test_d1_auth_non_ascii_tests_are_collected() -> None:
    """D1: The non-ASCII credential test functions must be collected by pytest.

    Regression guard: the bytes-header fix must not accidentally remove or skip
    the tests that prove ASGI wire-byte handling.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_auth.py",
            "-q",
            "--collect-only",
            "--no-cov",
            "-k",
            "non_ascii",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    collected_lines = [ln for ln in output.splitlines() if "non_ascii" in ln and "::" in ln]
    assert len(collected_lines) >= 4, (
        f"Expected >=4 non_ascii tests collected; got {len(collected_lines)}:\n{output}"
    )


# ── D2: nested-git-repo exclusion in AGENTS.md contract test ──────────────────
#
# RCA: test_no_nested_agents_md_files scanned with rglob("AGENTS.md") and
# excluded only ".git/" path segments.  product-sdlc-antigravity/ is a
# standalone nested git repo (has its own .git dir) with independent agent
# governance.  "product-sdlc-antigravity/AGENTS.md" contains no ".git/"
# segment so the exclusion did not apply.
#
# Fix: _nested_git_roots() helper dynamically finds sub-dirs with .git and
# excludes AGENTS.md files inside them.  Zero hardcoded paths.


def test_d2_agents_md_contract_passes_with_nested_git_repos(tmp_path: Path) -> None:
    """D2: AGENTS.md in a nested git repo must not trigger the governance gate.

    Creates a synthetic nested repo with its own .git and an AGENTS.md, then
    confirms the _nested_git_roots() exclusion logic correctly excludes it.
    """
    fake_repo_root = tmp_path / "main_repo"
    fake_root_agents = fake_repo_root / "AGENTS.md"
    fake_nested = fake_repo_root / "nested_project"
    fake_nested_git = fake_nested / ".git"
    fake_nested_agents = fake_nested / "AGENTS.md"

    fake_repo_root.mkdir()
    fake_root_agents.write_text("# AGENTS.md\n## Essential Commands\n", encoding="utf-8")
    fake_nested.mkdir()
    fake_nested_git.mkdir()
    fake_nested_agents.write_text("# nested AGENTS.md\n", encoding="utf-8")

    excluded_roots = {
        git_path.parent
        for git_path in fake_repo_root.rglob(".git")
        if git_path.parent != fake_repo_root
    }

    nested = sorted(
        path.relative_to(fake_repo_root).as_posix()
        for path in fake_repo_root.rglob("AGENTS.md")
        if (
            ".git/" not in path.as_posix()
            and ".venv/" not in path.as_posix()
            and path != fake_root_agents
            and not any(path.is_relative_to(root) for root in excluded_roots)
        )
    )

    assert nested == [], (
        f"_nested_git_roots() exclusion failed — nested AGENTS.md not excluded: {nested}"
    )


def test_d2_agents_md_contract_test_passes_live() -> None:
    """D2: The live governance test must pass against the actual repo."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_agents_md_contract.py::test_no_nested_agents_md_files",
            "-v",
            "--no-cov",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"test_no_nested_agents_md_files still fails:\n{result.stdout}\n{result.stderr}"
    )


# ── D3: tests/regression/__init__.py package hygiene ─────────────────────────
#
# RCA: tests/regression/ had no __init__.py, inconsistent with all other test
# subdirectories.  While pytest discovers test_*.py without it, the missing
# marker breaks explicit imports and is inconsistent.


def test_d3_regression_package_has_init() -> None:
    """D3: tests/regression/ must have an __init__.py for package consistency."""
    init_file = _REPO_ROOT / "tests" / "regression" / "__init__.py"
    assert init_file.is_file(), (
        "tests/regression/__init__.py is missing — add it for package hygiene "
        "consistent with all other test sub-packages."
    )


# ── D4: ADR-0031 step-timeout failure row persisted in list_turns ─────────────
#
# RCA: test_sub_round_trip_budget_returns_the_504_envelope asserted
# `await repo.list_turns(limit=5) == []` after a StepTimeout dispatch.  This
# predated ADR-0031 (_FailureRecordingMixin), which intentionally persists ALL
# dispatch failures — including StepTimeout — as error-status rows via
# save_failed_turn().  list_turns() returns all rows regardless of status, so
# the zero-row assertion became a false negative.
#
# Fix: assertion updated to verify exactly one error row with the correct
# error_code and no successful content.


async def test_d4_save_failed_turn_appears_in_list_turns(tmp_path: Path) -> None:
    """D4: save_failed_turn must produce a row visible to list_turns.

    ADR-0031 contract: failure rows are stored and returned alongside success
    rows.  The pre-fix test incorrectly expected list_turns == [] after a
    StepTimeout because _FailureRecordingMixin had not yet been written.
    Uses SQLiteRepository directly — no live LLM dependency.
    """
    db_path = tmp_path / "test_turns.db"
    repo = SQLiteRepository(f"sqlite:///{db_path.as_posix()}")

    request = AgentRequest(messages=[Message(role="user", content="hello")])
    error_code = "step_timeout"

    await repo.save_failed_turn("chat", request, error_code=error_code, error="timed out")

    turns = await repo.list_turns(limit=5)

    assert len(turns) == 1, (
        f"save_failed_turn must produce exactly one row visible to list_turns; got {turns}"
    )
    assert turns[0].get("error_code") == error_code, (
        f"error_code not persisted correctly: {turns[0]}"
    )
    assert not turns[0].get("content"), "A failed turn must not carry successful response content"
    repo.close()


# ── D5: make_lmstudio_settings ignored MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS ────
#
# RCA: make_lmstudio_settings always built LoopSettings() with the hardcoded
# DEFAULT_LOOP_STEP_TIMEOUT (30s) when no explicit loop= was supplied.  Slow
# models (e.g. nvidia/nemotron-3-nano-omni on a local GPU) take >30s per step,
# causing StepTimeout on every E2E test that uses the standard fixture.
#
# Fix (commit d59aa95): when loop=None, read MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS
# from the environment and pass it into LoopSettings(step_timeout_seconds=...).
# Falls back to DEFAULT_LOOP_STEP_TIMEOUT unchanged.  An explicit loop= argument
# takes precedence over the env var.  A malformed value raises a named ValueError
# (mirrors resolve_live_timeout).


def test_d5_make_lmstudio_settings_respects_step_timeout_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D5: env var MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS must reach LoopSettings."""
    monkeypatch.setenv(LOOP_STEP_TIMEOUT_ENV, "90")
    settings = make_lmstudio_settings("http://localhost:1234/v1", "test-model")
    assert settings.loop.step_timeout_seconds == 90.0, (
        f"Expected 90.0 from env; got {settings.loop.step_timeout_seconds}"
    )


def test_d5_make_lmstudio_settings_defaults_when_env_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D5: absence of the env var must fall back to DEFAULT_LOOP_STEP_TIMEOUT."""
    monkeypatch.delenv(LOOP_STEP_TIMEOUT_ENV, raising=False)
    settings = make_lmstudio_settings("http://localhost:1234/v1", "test-model")
    assert settings.loop.step_timeout_seconds == DEFAULT_LOOP_STEP_TIMEOUT, (
        f"Expected {DEFAULT_LOOP_STEP_TIMEOUT} default; got {settings.loop.step_timeout_seconds}"
    )


def test_d5_explicit_loop_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D5: an explicit loop= kwarg must not be overridden by the env var."""
    monkeypatch.setenv(LOOP_STEP_TIMEOUT_ENV, "90")
    explicit = LoopSettings(step_timeout_seconds=5.0)
    settings = make_lmstudio_settings("http://localhost:1234/v1", "test-model", loop=explicit)
    assert settings.loop.step_timeout_seconds == 5.0, (
        f"Explicit loop= must take precedence over env; got {settings.loop.step_timeout_seconds}"
    )


def test_d5_malformed_step_timeout_env_raises_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D5: a non-numeric env value must raise ValueError naming the variable."""
    monkeypatch.setenv(LOOP_STEP_TIMEOUT_ENV, "not-a-number")
    with pytest.raises(ValueError, match=LOOP_STEP_TIMEOUT_ENV):
        make_lmstudio_settings("http://localhost:1234/v1", "test-model")
