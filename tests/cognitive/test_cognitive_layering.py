"""Cognitive package layering: contracts in, harness/adapters/agents out."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COGNITIVE = _REPO_ROOT / "src" / "mangomas" / "cognitive"
_MANGOMAS = _REPO_ROOT / "src" / "mangomas"

# Kernel + this package. ``mangomas.correlation`` is the same ContextVar
# ``eval/runner`` reads; it is not the Claude Code harness.
_ALLOWED_MANGOMAS_PREFIXES = (
    "mangomas.cognitive",
    "mangomas.core",
    "mangomas.config",
    "mangomas.errors",
    "mangomas.correlation",
)

_BANNED_PREFIXES = (
    "mangomas.harness",
    "mangomas.adapters",
    "mangomas.agents",
    "mangomas.api",
    "mangomas.cli",
    "mangomas.eval",
    "mangomas.workflow",
    "mangomas.telemetry",
    "mangomas.composition",
    "harness.shared",
    "harness.execution",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _is_allowed_mangomas(name: str) -> bool:
    if name == "mangomas":
        # ``from mangomas import __version__`` — package root, not a layer.
        return True
    return any(
        name == prefix or name.startswith(prefix + ".") for prefix in _ALLOWED_MANGOMAS_PREFIXES
    )


def test_cognitive_does_not_import_harness_or_adapters() -> None:
    offenders: list[str] = []
    for path in _COGNITIVE.rglob("*.py"):
        for name in _imports(path):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in _BANNED_PREFIXES):
                offenders.append(f"{path.name}: {name}")
            if "ExecutionBroker" in name or name.endswith("command_actions"):
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_cognitive_mangomas_imports_are_allow_listed() -> None:
    """Any new ``mangomas.*`` import must be an explicit kernel/sibling allow."""
    offenders: list[str] = []
    for path in _COGNITIVE.rglob("*.py"):
        for name in _imports(path):
            if (name == "mangomas" or name.startswith("mangomas.")) and not _is_allowed_mangomas(
                name
            ):
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


def test_producer_imports_correlation_explicitly() -> None:
    """Non-vacuity: the allow-list would pass if nobody imported correlation."""
    producer = (_COGNITIVE / "producer.py").read_text(encoding="utf-8")
    assert "mangomas.correlation" in producer


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
