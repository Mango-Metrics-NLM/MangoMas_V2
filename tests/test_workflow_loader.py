"""Tests for load_workflow (path-or-inline JSON → WorkflowGraph, ConfigError boundary)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mangomas.errors import ConfigError
from mangomas.workflow import WorkflowGraph, load_workflow
from tests.constants import WORKFLOW_SCHEMA_VERSION_CURRENT

_VALID_GRAPH: dict[str, object] = {
    "schema_version": WORKFLOW_SCHEMA_VERSION_CURRENT,
    "name": "demo",
    "root": {"kind": "agent", "agent": "chat"},
}


def test_inline_json_string() -> None:
    graph = load_workflow(json.dumps(_VALID_GRAPH))
    assert isinstance(graph, WorkflowGraph)
    assert graph.name == "demo"


def test_inline_json_with_leading_whitespace() -> None:
    graph = load_workflow("   \n" + json.dumps(_VALID_GRAPH))
    assert graph.name == "demo"


def test_file_path(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(_VALID_GRAPH), encoding="utf-8")
    graph = load_workflow(str(path))
    assert graph.root.kind == "agent"


def test_file_path_with_trailing_whitespace(tmp_path: Path) -> None:
    """A path with a trailing newline (common from env vars/CLI) must still load."""
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(_VALID_GRAPH), encoding="utf-8")
    graph = load_workflow(str(path) + "\n")
    assert graph.name == "demo"


def test_malformed_json_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_workflow("{not json")


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_workflow(str(tmp_path / "nope.json"))


def test_invalid_graph_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="invalid workflow graph"):
        load_workflow(json.dumps({"name": "x", "root": {"kind": "bogus"}}))


def test_unsupported_schema_version_raises_config_error() -> None:
    payload = {**_VALID_GRAPH, "schema_version": 999}
    with pytest.raises(ConfigError, match="unsupported workflow schema_version"):
        load_workflow(json.dumps(payload))


def test_binary_file_raises_config_error(tmp_path: Path) -> None:
    """A non-UTF-8 file must normalize to ConfigError, not crash (UnicodeDecodeError)."""
    path = tmp_path / "graph.bin"
    path.write_bytes(b"\xff\xfe\x00\x01not-utf8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_workflow(str(path))
