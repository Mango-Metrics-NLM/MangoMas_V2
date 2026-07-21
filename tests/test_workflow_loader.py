"""Tests for workflow graph loading + settings bridge (spec 0005 / ADR-0007)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mangomas.config import Settings, WorkflowSettings
from mangomas.errors import ConfigError
from mangomas.workflow.loader import (
    graph_from_settings,
    load_graph_file,
    load_graph_json,
    parse_graph,
)
from mangomas.workflow.models import WorkflowGraph

_VALID_DEF: dict[str, object] = {
    "name": "demo",
    "nodes": [
        {"id": "a", "agent": "planner"},
        {"id": "b", "agent": "reviewer", "depends_on": ["a"]},
    ],
}
_CYCLE_DEF: dict[str, object] = {
    "nodes": [
        {"id": "a", "agent": "planner", "depends_on": ["b"]},
        {"id": "b", "agent": "reviewer", "depends_on": ["a"]},
    ],
}


# ── parse_graph ───────────────────────────────────────────────────────────────


def test_parse_graph_valid() -> None:
    graph = parse_graph(_VALID_DEF)
    assert isinstance(graph, WorkflowGraph)
    assert graph.name == "demo"
    assert [node.id for node in graph.nodes] == ["a", "b"]


def test_parse_graph_non_dict() -> None:
    with pytest.raises(ConfigError, match="must be a JSON object"):
        parse_graph(["not", "a", "dict"])


def test_parse_graph_invalid_structure() -> None:
    with pytest.raises(ConfigError, match="Invalid workflow graph"):
        parse_graph(_CYCLE_DEF)


# ── load_graph_json ───────────────────────────────────────────────────────────


def test_load_graph_json_valid() -> None:
    assert load_graph_json(json.dumps(_VALID_DEF)).name == "demo"


def test_load_graph_json_bad_json() -> None:
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_graph_json("{not json")


def test_load_graph_json_non_object() -> None:
    with pytest.raises(ConfigError, match="must be a JSON object"):
        load_graph_json("[]")


# ── load_graph_file ───────────────────────────────────────────────────────────


def test_load_graph_file_valid(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(_VALID_DEF), encoding="utf-8")
    assert load_graph_file(path).name == "demo"


def test_load_graph_file_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="could not be read"):
        load_graph_file(tmp_path / "nope.json")


# ── graph_from_settings ───────────────────────────────────────────────────────


def test_graph_from_settings_disabled_returns_none() -> None:
    assert graph_from_settings(WorkflowSettings(enabled=False)) is None


def test_graph_from_settings_enabled_without_definition() -> None:
    with pytest.raises(ConfigError, match="required"):
        graph_from_settings(WorkflowSettings(enabled=True))


def test_graph_from_settings_blank_definition() -> None:
    with pytest.raises(ConfigError, match="required"):
        graph_from_settings(WorkflowSettings(enabled=True, definition="   "))


def test_graph_from_settings_inline_json() -> None:
    graph = graph_from_settings(WorkflowSettings(enabled=True, definition=json.dumps(_VALID_DEF)))
    assert graph is not None
    assert graph.name == "demo"


def test_graph_from_settings_file_path(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(_VALID_DEF), encoding="utf-8")
    graph = graph_from_settings(WorkflowSettings(enabled=True, definition=str(path)))
    assert graph is not None
    assert graph.name == "demo"


# ── Settings wiring ───────────────────────────────────────────────────────────


def test_settings_default_workflow_disabled() -> None:
    settings = Settings()
    assert settings.workflow.enabled is False
    assert settings.workflow.definition is None
