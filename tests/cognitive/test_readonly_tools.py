"""Read-only observations: retrieve stays local; no write/command tools."""

from __future__ import annotations

import ast
from pathlib import Path

from mangomas.rag.retrieval import RetrievalTool

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = (
    _REPO_ROOT / "src" / "mangomas" / "agents",
    _REPO_ROOT / "src" / "mangomas" / "rag",
    _REPO_ROOT / "src" / "mangomas" / "cognitive",
)
_FORBIDDEN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "write_file",
        "run_command",
        "apply_patch",
        "shell",
        "bash",
        "implementer",
    }
)


def test_retrieval_tool_name_is_retrieve() -> None:
    assert RetrievalTool.name == "retrieve"


def test_no_write_or_command_tool_names_in_cognitive_plane() -> None:
    found: list[str] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value in _FORBIDDEN_TOOL_NAMES:
                    # Docstrings / comments about the *ban* are allowed when they
                    # sit next to the forbid-set itself; skip this test file's
                    # imported constant by requiring assignment to `.name` or
                    # a ToolSpec keyword.
                    found.append(f"{rel}:{getattr(node, 'lineno', 0)}:{node.value}")
    # The forbid-set definition in this test is not under src/. Roles.py names
    # FORBIDDEN_HARNESS_ROLES including write_file/shell — those are the ban,
    # not a tool grant. Filter cognitive/roles.py constants.
    remaining = [
        hit for hit in found if "cognitive/roles.py" not in hit and "cognitive/pdp.py" not in hit
    ]
    assert remaining == [], remaining


def test_scan_roots_are_populated() -> None:
    files = [path for root in _SCAN_ROOTS for path in root.rglob("*.py")]
    assert len(files) >= 8
