"""Cognitive package layering: contracts in, harness/adapters out."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COGNITIVE = _REPO_ROOT / "src" / "mangomas" / "cognitive"
_MANGOMAS = _REPO_ROOT / "src" / "mangomas"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_cognitive_does_not_import_harness_or_adapters() -> None:
    banned_prefixes = (
        "mangomas.harness",
        "mangomas.adapters",
        "harness.shared",
        "harness.execution",
    )
    offenders: list[str] = []
    for path in _COGNITIVE.rglob("*.py"):
        for name in _imports(path):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in banned_prefixes):
                offenders.append(f"{path.name}: {name}")
            if "ExecutionBroker" in name or name.endswith("command_actions"):
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_cognitive_does_not_import_agents() -> None:
    """Producer parses planner/reviewer JSON as dicts; it must not import agents."""
    offenders: list[str] = []
    for path in _COGNITIVE.rglob("*.py"):
        for name in _imports(path):
            if name == "mangomas.agents" or name.startswith("mangomas.agents."):
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_only_cognitive_imports_mango_contracts_at_runtime() -> None:
    offenders: list[str] = []
    for path in _MANGOMAS.rglob("*.py"):
        rel = path.relative_to(_MANGOMAS).as_posix()
        if rel.startswith("cognitive/"):
            continue
        for name in _imports(path):
            if name == "mango_contracts" or name.startswith("mango_contracts."):
                offenders.append(f"{rel}: {name}")
    assert offenders == []


def test_producer_and_sink_do_import_contracts() -> None:
    """Non-vacuity: the confine test would pass if nobody imported contracts."""
    producer = (_COGNITIVE / "producer.py").read_text(encoding="utf-8")
    sink = (_COGNITIVE / "sink.py").read_text(encoding="utf-8")
    assert "mango_contracts" in producer
    assert "mango_contracts" in sink
