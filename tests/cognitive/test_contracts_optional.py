"""The contracts package is optional until ``MANGOMAS_SIGNAL__ENABLED=true`` (P0-C1).

``mango_contracts`` is a sibling checkout, not a PyPI dependency, so a base
install does not have it. Two contracts hold: flag-off dispatch never imports
it (so its absence is invisible), and flag-on composition refuses a build
without it as a typed ``ConfigError`` naming the install — not an
``ImportError`` on the first planner request.
"""

from __future__ import annotations

import json
import re
import sys

import pytest
from tests.constants import IN_MEMORY_SQLITE_URL
from tests.fakes import FakeLLM

from mangomas.agents import PlannerAgent
from mangomas.cognitive._contracts import _CONTRACTS_INSTALL_HINT, require_contracts
from mangomas.composition import build_orchestrator
from mangomas.config import Settings
from mangomas.core import AgentContext, AgentRequest, Message, Orchestrator
from mangomas.errors import ConfigError

_CONTRACTS_MODULE = "mango_contracts"
_PLAN_REPLY = json.dumps({"goal": "x", "steps": [{"id": 1, "description": "do"}]})


def _hide_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import mango_contracts`` raise ImportError for this test only.

    ``sys.modules[name] = None`` is the interpreter's own "import must fail"
    marker; every already-imported submodule is dropped too so a cached
    ``mango_contracts.enums`` cannot satisfy a ``from … import``.
    """
    for name in [
        m for m in sys.modules if m == _CONTRACTS_MODULE or m.startswith("mango_contracts.")
    ]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, _CONTRACTS_MODULE, None)
    # The producer module caches the contracts symbols it imported at module
    # level; forget it so the next import (if any) goes through sys.modules.
    monkeypatch.delitem(sys.modules, "mangomas.cognitive.producer", raising=False)


async def test_flag_off_dispatch_never_imports_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A planner turn with no sink attached must not touch the contracts package."""
    _hide_contracts(monkeypatch)
    ctx = AgentContext(llm=FakeLLM(reply=_PLAN_REPLY), repo=None)
    orch = Orchestrator(ctx)
    orch.register(PlannerAgent())
    response = await orch.dispatch(
        "planner", AgentRequest(messages=[Message(role="user", content="plan")])
    )
    assert response.content == _PLAN_REPLY
    assert "mangomas.cognitive.producer" not in sys.modules


def test_flag_on_without_contracts_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build time, typed, and naming the install — never a 500 on first request.

    Mutation (proved while writing): drop the ``require_contracts()`` call in
    ``composition/signal.py`` and this builds fine, deferring the ImportError
    to the first planner request.
    """
    _hide_contracts(monkeypatch)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = IN_MEMORY_SQLITE_URL
    settings.signal.enabled = True
    with pytest.raises(ConfigError, match=re.escape("pip install ./mango-integration-contracts")):
        build_orchestrator(settings)


def test_require_contracts_passes_when_installed() -> None:
    """The other direction: with the package present the probe is a no-op."""
    require_contracts()
    assert "mango-integration-contracts" in _CONTRACTS_INSTALL_HINT
