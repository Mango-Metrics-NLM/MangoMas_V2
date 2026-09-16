"""Tests for the workflow HTTP endpoints (spec 0008 / ADR-0012).

The feature is OFF by default in the test process (no ``MANGOMAS_WORKFLOW__*``
env), so a request without a per-request ``definition`` must be rejected, while a
request that carries a ``definition`` runs regardless — mirroring the CLI.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from mangomas.api.app import create_app
from mangomas.core import Orchestrator
from tests.constants import (
    STUB_REPLY,
    WORKFLOW_RUN_ROUTE,
    WORKFLOW_VALIDATE_ROUTE,
)

_AGENT_GRAPH = json.dumps({"name": "t", "root": {"kind": "agent", "agent": "chat"}})
_GHOST_AGENT = "ghost"
_GHOST_GRAPH = json.dumps({"name": "t", "root": {"kind": "agent", "agent": _GHOST_AGENT}})
# Spelled out, not rebuilt with ``!r`` — see tests/test_errors.py.
_GHOST_AGENT_MESSAGE = f"Unknown agent: '{_GHOST_AGENT}'"
_FANOUT_CONCAT_GRAPH = json.dumps(
    {
        "name": "t",
        "root": {
            "kind": "fan_out",
            "join": "concat",
            "branches": [{"kind": "agent", "agent": "chat"}, {"kind": "agent", "agent": "chat"}],
        },
    }
)
_LOOP_GRAPH = json.dumps(
    {
        "name": "t",
        "root": {
            "kind": "loop",
            "agent": "chat",
            "max_steps": 3,
            "accept": {"kind": "contains", "value": "stub"},
        },
    }
)

_RUN_BODY = {"messages": [{"role": "user", "content": "hi"}]}


def test_run_single_agent_returns_final_response(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            WORKFLOW_RUN_ROUTE,
            json={"request": _RUN_BODY, "definition": _AGENT_GRAPH},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["agent"] == "chat"
        assert body["content"] == STUB_REPLY


def test_run_fan_out_concat(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            WORKFLOW_RUN_ROUTE,
            json={"request": _RUN_BODY, "definition": _FANOUT_CONCAT_GRAPH},
        )
        assert r.status_code == 200
        # concat join newline-joins each branch's content.
        assert r.json()["content"] == f"{STUB_REPLY}\n{STUB_REPLY}"


def test_run_loop_accepts_on_predicate(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            WORKFLOW_RUN_ROUTE,
            json={"request": _RUN_BODY, "definition": _LOOP_GRAPH},
        )
        assert r.status_code == 200
        assert r.json()["content"] == STUB_REPLY


def test_run_disabled_without_definition_returns_400(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(WORKFLOW_RUN_ROUTE, json={"request": _RUN_BODY})
        assert r.status_code == 400
        assert r.json()["error"] == "config_error"


def test_run_empty_definition_returns_400(orchestrator: Orchestrator) -> None:
    # An empty (but non-None) definition skips the "disabled" branch and hits the
    # "no definition" branch — feature off means cfg.definition is None too.
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(WORKFLOW_RUN_ROUTE, json={"request": _RUN_BODY, "definition": ""})
        assert r.status_code == 400
        assert r.json()["error"] == "config_error"


def test_run_malformed_definition_returns_400(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            WORKFLOW_RUN_ROUTE,
            json={"request": _RUN_BODY, "definition": "{bad json"},
        )
        assert r.status_code == 400
        assert r.json()["error"] == "config_error"


def test_run_unknown_agent_returns_404(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            WORKFLOW_RUN_ROUTE,
            json={"request": _RUN_BODY, "definition": _GHOST_GRAPH},
        )
        assert r.status_code == 404
        body = r.json()
        assert body["error"] == "agent_not_found"
        # The second route that publishes AgentNotFound: same unquoted message.
        assert body["message"] == _GHOST_AGENT_MESSAGE


def test_validate_ok_returns_name_and_root_kind(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(WORKFLOW_VALIDATE_ROUTE, json={"definition": _AGENT_GRAPH})
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True, "name": "t", "root_kind": "agent"}


def test_validate_malformed_returns_400(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(WORKFLOW_VALIDATE_ROUTE, json={"definition": "{bad json"})
        assert r.status_code == 400
        assert r.json()["error"] == "config_error"


def test_validate_disabled_without_definition_returns_400(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(WORKFLOW_VALIDATE_ROUTE, json={})
        assert r.status_code == 400
        assert r.json()["error"] == "config_error"
