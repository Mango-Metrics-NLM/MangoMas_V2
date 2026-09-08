"""Inventory of execution bypasses in the cognitive-plane source.

A future ExecutionBroker is only as strong as the paths that go around it.
This scan fails closed when ``src/mangomas/agents`` or ``src/mangomas/core``
gains a process-spawn call or ``subprocess`` import that is not on the
explicit allowlist (empty today).
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = (
    _REPO_ROOT / "src" / "mangomas" / "agents",
    _REPO_ROOT / "src" / "mangomas" / "core",
)
# Process spawns in the cognitive plane must be listed here with a reason.
# An empty tuple is the point: today's agents and core do not spawn.
_ALLOWED_SPAWN_SITES: tuple[str, ...] = ()


def _is_spawn_func(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "Popen", "posix_spawn"}:
        return True
    return isinstance(node, ast.Name) and node.id in {"Popen", "system"}


def _shell_true(call: ast.Call) -> bool:
    return any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def _is_subprocess_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(
            alias.name == "subprocess" or alias.name.startswith("subprocess.")
            for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        return node.module == "subprocess" or (node.module or "").startswith("subprocess.")
    return False


def _spawn_sites() -> list[str]:
    found: list[str] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (_is_spawn_func(node.func) or _shell_true(node)):
                    found.append(f"{rel}:{node.lineno}")
                elif isinstance(node, (ast.Import, ast.ImportFrom)) and _is_subprocess_import(node):
                    found.append(f"{rel}:{node.lineno}:subprocess-import")
    return found


def test_agents_and_core_have_no_process_spawns() -> None:
    found = tuple(_spawn_sites())
    assert found == _ALLOWED_SPAWN_SITES


def test_allowlist_cannot_silently_empty_the_scan() -> None:
    """Both directions: the scan roots exist, so a miss is a real miss."""
    files = [path for root in _SCAN_ROOTS for path in root.rglob("*.py")]
    assert files, "scan roots exist but contain no Python files"
    assert len(files) >= 8
    for root in _SCAN_ROOTS:
        assert root.is_dir(), f"scan root vanished: {root}"
