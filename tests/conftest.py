"""Shared pytest fixtures."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from mangomas.adapters.storage import SQLiteRepository
from mangomas.agents import ChatAgent
from mangomas.cli import _runtime
from mangomas.config import Settings, get_settings
from mangomas.core import AgentContext, Orchestrator
from mangomas.secrets import secrets_registry
from tests.constants import ENV_GATE_SKIP_REASONS, GATED_RUNTIME_SKIP_REASON_RE
from tests.fakes import FakeLLM, FakeMemoryRepository, FakeRepository, FakeTool

# ── Cross-test isolation for lazy-registered cloud providers ──────────────────


@pytest.fixture(autouse=True)
def _teardown_lazy_gcp_secrets() -> Iterator[None]:
    """Pop any GCP secrets provider lazy-registered by build_orchestrator.

    The provider is registered inside ``build_orchestrator`` when
    ``secrets.provider == "gcp"`` and lives on the module-level
    ``secrets_registry`` instance. Without this teardown, a test that
    exercises the gcp path would leak a (project-id-bound) provider
    into the next test's ``secrets_registry.available()`` view.
    """
    yield
    secrets_registry._store.pop("gcp", None)


# ── Collection gates ────────────────────────────────────────────────────────


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip integration / cloud-provider tests unless explicitly enabled.

    Reasons come from ``tests.constants.ENV_GATE_SKIP_REASONS`` — the same
    table the zero-skip session guard below treats as sanctioned, so the two
    can never desync (spec-0022 R8).
    """
    enabled = {env: os.getenv(env) == "1" for env in ENV_GATE_SKIP_REASONS}
    skip = {env: pytest.mark.skip(reason=reason) for env, reason in ENV_GATE_SKIP_REASONS.items()}

    for item in items:
        path_parts = set(Path(str(item.fspath)).parts)
        if "integration" in path_parts and not enabled["RUN_INTEGRATION"]:
            item.add_marker(skip["RUN_INTEGRATION"])
        if "lmstudio" in item.keywords and not enabled["RUN_LMSTUDIO"]:
            item.add_marker(skip["RUN_LMSTUDIO"])
        if ("postgres" in path_parts or "postgres" in item.keywords) and not enabled[
            "RUN_POSTGRES"
        ]:
            item.add_marker(skip["RUN_POSTGRES"])
        if "vertex" in item.keywords and not enabled["RUN_VERTEX"]:
            item.add_marker(skip["RUN_VERTEX"])
        if "gcp_secrets" in item.keywords and not enabled["RUN_GCP_SECRETS"]:
            item.add_marker(skip["RUN_GCP_SECRETS"])
        if "gcp_trace" in item.keywords and not enabled["RUN_GCP_TRACE"]:
            item.add_marker(skip["RUN_GCP_TRACE"])
        if "embeddings_local" in item.keywords and not enabled["RUN_EMBEDDINGS_LOCAL"]:
            item.add_marker(skip["RUN_EMBEDDINGS_LOCAL"])
        # Gate on the explicit ``@pytest.mark.rag`` marker only — the
        # ``tests/rag/`` directory name would otherwise leak into ``keywords``
        # and wrongly skip the pure-domain chunker/models unit tests.
        if item.get_closest_marker("rag") is not None and not enabled["RUN_RAG"]:
            item.add_marker(skip["RUN_RAG"])
        if "langfuse" in item.keywords and not enabled["RUN_LANGFUSE"]:
            item.add_marker(skip["RUN_LANGFUSE"])


# ── Zero-skip session guard (spec-0022 R8) ────────────────────────────────────
#
# Escalate-only: an otherwise-green run fails if any test skipped for a reason
# outside the sanctioned env-gate table, or xfailed/xpassed at all. The repo
# has ~25 env-gated skips and zero xfails on a default run, so the ratchet
# binds on nothing today — it exists to stop rot: an ad-hoc
# ``pytest.skip("flaky")``, or a broken environment silently shedding the
# Hypothesis fuzz files (module-level ``importorskip`` surfaces as a *collect*
# report, which is why ``pytest_collectreport`` is hooked too — hypothesis and
# asyncpg are dev-extra deps, so such a skip means a broken install, not an
# optional feature). A red run is never masked: the guard only acts when
# ``exitstatus == 0``. tests/tooling/test_collection_gate.py proves both
# directions in a subprocess, including that mutating ``session.exitstatus``
# here actually changes the process exit code.

_UNSANCTIONED_OUTCOMES: list[str] = []
_SANCTIONED_SKIP_REASONS = frozenset(ENV_GATE_SKIP_REASONS.values())
_GATED_RUNTIME_SKIP_RE = re.compile(GATED_RUNTIME_SKIP_REASON_RE)


def _skip_reason(report: pytest.TestReport | pytest.CollectReport) -> str:
    # A skip's longrepr is the tuple ``(path, lineno, "Skipped: <reason>")``.
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr)
    return reason.removeprefix("Skipped: ")


def _reason_is_sanctioned(reason: str) -> bool:
    return reason in _SANCTIONED_SKIP_REASONS or bool(_GATED_RUNTIME_SKIP_RE.match(reason))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if hasattr(report, "wasxfail"):
        # xfail (skipped-with-wasxfail) and xpass (passed-with-wasxfail) alike:
        # the repo has zero, and an expected failure that never runs to green
        # is exactly the rot the guard exists to surface.
        _UNSANCTIONED_OUTCOMES.append(f"XFAIL/XPASS {report.nodeid}")
        return
    if report.skipped:
        reason = _skip_reason(report)
        if not _reason_is_sanctioned(reason):
            _UNSANCTIONED_OUTCOMES.append(f"SKIP {report.nodeid}: {reason}")


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.skipped:
        reason = _skip_reason(report)
        if not _reason_is_sanctioned(reason):
            _UNSANCTIONED_OUTCOMES.append(f"SKIP (collection) {report.nodeid}: {reason}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if exitstatus != 0 or not _UNSANCTIONED_OUTCOMES:
        return
    lines = "\n".join(f"  {line}" for line in _UNSANCTIONED_OUTCOMES)
    print(  # noqa: T201 -- terminal summary for a session-level guard
        f"\nzero-skip guard: {len(_UNSANCTIONED_OUTCOMES)} unsanctioned "
        f"outcome(s) on an otherwise-green run:\n{lines}\n"
        "Fix the test or gate it through ENV_GATE_SKIP_REASONS in "
        "tests/constants.py (spec-0022 R8)."
    )
    session.exitstatus = 1


# ── Settings fixture ──────────────────────────────────────────────────────────


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Return a fresh Settings instance and clear the lru_cache on teardown."""
    get_settings.cache_clear()
    yield Settings()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Read env-driven settings fresh per test so a cached value never leaks.

    Shared by every env-toggling API test (CORS / auth / backpressure /
    workflow-endpoint), which previously each redefined this fixture.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── Storage fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> Iterator[SQLiteRepository]:
    """In-memory SQLiteRepository (for storage-layer tests)."""
    r = SQLiteRepository(":memory:")
    yield r
    r.close()


@pytest.fixture
def fake_repo() -> FakeRepository:
    """Pure in-memory FakeRepository (for unit tests that don't touch SQLite)."""
    return FakeRepository()


@pytest.fixture
def fake_memory() -> FakeMemoryRepository:
    """Pure in-memory FakeMemoryRepository (for unit tests)."""
    return FakeMemoryRepository()


# ── LLM fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_tool() -> FakeTool:
    return FakeTool()


# ── Orchestrator fixture ──────────────────────────────────────────────────────


@pytest.fixture
def orchestrator(fake_llm: FakeLLM, repo: SQLiteRepository) -> Orchestrator:
    ctx = AgentContext(llm=fake_llm, repo=repo)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


# ── `--verbose` assertion seam ────────────────────────────────────────────────


@pytest.fixture
def cli_logging_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record `_runtime.configure_cli_logging` calls so a `--verbose` test can assert one.

    **Why a spy and not an observed log level.** The real seam calls
    `configure_telemetry`, which does `logging.basicConfig(..., force=True)` —
    that rips out pytest's own root handlers (the live-log null handler and the
    two `LogCaptureHandler`s), breaking `caplog` for every test that follows.
    Command tests therefore assert the *wiring* (the flag reaches the seam);
    the seam's real behaviour — that DEBUG survives a lazy `get_tracer()`, and
    that a non-verbose run honours `MANGOMAS_LOG_LEVEL` — is covered directly
    in `tests/test_cli_runtime.py`.

    Asserting the call tests what the command actually promises: that `--verbose`
    *requests* debug logging. It is mutation-sensitive by construction — an
    invocation without the flag records `verbose=False`, and a deleted call
    records nothing at all.

    Every command module calls it as `_runtime.configure_cli_logging(...)`, an
    attribute lookup on the module object, so one patch reaches all of them —
    the same seam shape as the rest of `mangomas.cli._runtime`.
    """
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(_runtime, "configure_cli_logging", lambda **kw: calls.append(kw))
    return calls
