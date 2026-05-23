"""Shared fixtures for the evaluation harness tests."""

from __future__ import annotations

from pathlib import Path

import pytest

# Importing this package registers all built-in scorers — required for any
# test that resolves a scorer by name through ``scorer_registry``.
import mangomas.eval.scorers  # noqa: F401
from mangomas.agents import ChatAgent
from mangomas.core import AgentContext, Orchestrator
from tests.fakes import FakeLLM, FakeRepository

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return _FIXTURE_DIR


@pytest.fixture
def eval_orchestrator() -> Orchestrator:
    """Minimal orchestrator wired with a ``FakeLLM`` and ``FakeRepository``.

    Used by every eval test that needs to dispatch an agent; the ``FakeLLM``
    echoes ``STUB_REPLY`` so exact-match scoring against the all-pass dataset
    is deterministic.
    """
    llm = FakeLLM()
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch
