"""Inventory of execution bypasses in the cognitive and orchestration planes.

A future ExecutionBroker is only as strong as the paths that go around it.
This scan fails closed when ``src/mangomas/agents``, ``src/mangomas/core``,
``src/mangomas/cognitive``, ``src/mangomas/rag``, ``src/mangomas/workflow``, or
``src/mangomas/eval`` gains a process-spawn call, a native-API import
(``subprocess``, ``ctypes``, ``winreg``), or a filesystem write/delete call
that is not on the explicit allowlist.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = (
    _REPO_ROOT / "src" / "mangomas" / "agents",
    _REPO_ROOT / "src" / "mangomas" / "core",
    _REPO_ROOT / "src" / "mangomas" / "cognitive",
    _REPO_ROOT / "src" / "mangomas" / "rag",
    _REPO_ROOT / "src" / "mangomas" / "workflow",
    _REPO_ROOT / "src" / "mangomas" / "eval",
)
# Process spawns in the scanned planes must be listed here with a reason.
# An empty tuple is the point: none of these six packages may spawn processes.
_ALLOWED_SPAWN_SITES: tuple[str, ...] = ()
# Write/delete opens under the scanned planes must be explicitly allowlisted.
# The cognitive JSONL sink appends signal envelopes (default-off, ADR-0029) and
# the JSON-file eval sink writes its report; agents, core, rag, and workflow
# have zero write sites.
_ALLOWED_WRITE_SITES: tuple[str, ...] = (
    "src/mangomas/cognitive/sink.py:210",
    "src/mangomas/eval/sinks/json_file.py:53",
)

_SPAWN_ATTRS: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "Popen",
        "posix_spawn",
        "posix_spawnp",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "startfile",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
)

_BANNED_IMPORT_MODULES: frozenset[str] = frozenset({"subprocess", "ctypes", "winreg"})

_WRITE_OR_DELETE_ATTRS: frozenset[str] = frozenset(
    {
        "write_text",
        "write_bytes",
        "unlink",
        "rmdir",
        "removedirs",
        "remove",
        "rename",
        "replace",
        "rmtree",
        "move",
        "copytree",
        "touch",
    }
)


# Modules whose functions are write/delete or spawn primitives when imported
# by name (`from shutil import rmtree as nuke; nuke(p)` is an `ast.Name` call).
_PRIMITIVE_MODULES: frozenset[str] = frozenset({"os", "shutil", "pathlib", "asyncio"})
_OPEN_MODE_RE = re.compile(r"[rwxabt+]+")


def _imported_aliases(tree: ast.AST, names: frozenset[str]) -> frozenset[str]:
    """Local names bound by `from <primitive module> import <name> [as alias]`."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in (
            _PRIMITIVE_MODULES
        ):
            aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name in names
            )
    return frozenset(aliases)


def _is_spawn_func(node: ast.AST, aliases: frozenset[str] = frozenset()) -> bool:
    if isinstance(node, ast.Attribute) and node.attr in _SPAWN_ATTRS:
        return True
    return isinstance(node, ast.Name) and (node.id in _SPAWN_ATTRS or node.id in aliases)


def _shell_true(call: ast.Call) -> bool:
    return any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def _is_banned_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] in _BANNED_IMPORT_MODULES for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        return root in _BANNED_IMPORT_MODULES
    return False


def _parsed_files(roots: Sequence[Path], repo_root: Path) -> Iterator[tuple[str, ast.Module]]:
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            yield path.relative_to(repo_root).as_posix(), tree


def _spawn_sites(roots: Sequence[Path] = _SCAN_ROOTS, repo_root: Path = _REPO_ROOT) -> list[str]:
    found: list[str] = []
    for rel, tree in _parsed_files(roots, repo_root):
        aliases = _imported_aliases(tree, _SPAWN_ATTRS)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                _is_spawn_func(node.func, aliases) or _shell_true(node)
            ):
                found.append(f"{rel}:{node.lineno}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)) and _is_banned_import(node):
                found.append(f"{rel}:{node.lineno}:subprocess-import")
    return sorted(found)


def _is_write_mode(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _OPEN_MODE_RE.fullmatch(node.value) is not None
        and any(flag in node.value for flag in "wax")
    )


def _is_write_open(call: ast.Call, aliases: frozenset[str] = frozenset()) -> bool:
    """True for a writing ``open``/``Path.open`` or an os/shutil/Path write/delete helper.

    ``open(path, "w")`` carries the mode second; ``Path(p).open("a")`` carries
    it first, so an attribute ``.open`` checks both leading positions.
    """
    if isinstance(call.func, ast.Attribute) and call.func.attr in _WRITE_OR_DELETE_ATTRS:
        return True
    if isinstance(call.func, ast.Name) and call.func.id in aliases:
        return True
    if isinstance(call.func, ast.Name) and call.func.id == "open":
        positional = call.args[1:2]
    elif isinstance(call.func, ast.Attribute) and call.func.attr == "open":
        positional = call.args[:2]
    else:
        return False
    keyword_modes = [kw.value for kw in call.keywords if kw.arg == "mode"]
    return any(_is_write_mode(node) for node in [*positional, *keyword_modes])


def _write_sites(roots: Sequence[Path] = _SCAN_ROOTS, repo_root: Path = _REPO_ROOT) -> list[str]:
    found: list[str] = []
    for rel, tree in _parsed_files(roots, repo_root):
        aliases = _imported_aliases(tree, _WRITE_OR_DELETE_ATTRS)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_write_open(node, aliases):
                found.append(f"{rel}:{node.lineno}")
    return sorted(found)


def test_agents_and_core_have_no_process_spawns() -> None:
    found = tuple(_spawn_sites())
    assert found == _ALLOWED_SPAWN_SITES


def test_allowlist_cannot_silently_empty_the_scan() -> None:
    """Both directions: the scan roots exist, so a miss is a real miss."""
    files = [path for root in _SCAN_ROOTS for path in root.rglob("*.py")]
    assert files, "scan roots exist but contain no Python files"
    assert len(files) >= 30
    for root in _SCAN_ROOTS:
        assert root.is_dir(), f"scan root vanished: {root}"


def test_write_allowlist_matches_the_scan() -> None:
    """Phantom allowlist entries (sites that do not exist) must fail."""
    found = tuple(_write_sites())
    assert found == _ALLOWED_WRITE_SITES


def test_scan_detects_unallowlisted_write_and_spawn(tmp_path: Path) -> None:
    """Mutation-proof: injected write/spawn sites in a scanned tree are found and rejected."""
    agents = tmp_path / "src" / "mangomas" / "agents"
    agents.mkdir(parents=True)
    (agents / "rogue.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from shutil import rmtree as nuke\n"
        "from os import system as run_shell\n"
        "Path('x').open('a')\n"
        "nuke('x')\n"
        "run_shell('true')\n"
        "os.system('true')\n",
        encoding="utf-8",
    )
    writes = tuple(_write_sites((agents,), tmp_path))
    spawns = tuple(_spawn_sites((agents,), tmp_path))
    assert writes == ("src/mangomas/agents/rogue.py:5", "src/mangomas/agents/rogue.py:6")
    assert spawns == ("src/mangomas/agents/rogue.py:7", "src/mangomas/agents/rogue.py:8")
    assert writes != _ALLOWED_WRITE_SITES
    assert spawns != _ALLOWED_SPAWN_SITES


def test_spawn_scanner_detects_banned_forms() -> None:
    """Mutation-proof: the helpers fire on subprocess / asyncio / startfile / ctypes.

    An empty allowlist plus a scan that never matches would stay green forever.
    These snippets are the defects the inventory claims to catch.
    """
    tree = ast.parse(
        "import subprocess\n"
        "import ctypes\n"
        "import winreg\n"
        "import os, asyncio\n"
        "os.system('true')\n"
        "os.startfile('calc.exe')\n"
        "asyncio.create_subprocess_exec('echo')\n"
        "subprocess.run(['echo'], shell=True)\n"
        "from subprocess import Popen\n"
    )
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (_is_spawn_func(node.func) or _shell_true(node)):
            hits.append(f"call:{getattr(node, 'lineno', 0)}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and _is_banned_import(node):
            hits.append(f"import:{getattr(node, 'lineno', 0)}")
    assert any(h.startswith("import:") for h in hits)
    assert any(h.startswith("call:") for h in hits)
    assert len(hits) >= 8


def test_write_scanner_detects_write_open_and_path_write() -> None:
    tree = ast.parse(
        "from pathlib import Path\n"
        "import shutil, os\n"
        "open('x', 'w')\n"
        "Path('x').write_text('n')\n"
        "Path('x').write_bytes(b'n')\n"
        "handle.open('x', mode='a')\n"
        "Path('x').unlink()\n"
        "shutil.rmtree('x')\n"
        "os.remove('x')\n"
        "Path('x').open('a', encoding='utf-8')\n"
        "Path('x').open('rb')\n"
        "archive.open('member.txt')\n"
        "from os import remove\n"
        "remove('x')\n"
    )
    aliases = _imported_aliases(tree, _WRITE_OR_DELETE_ATTRS)
    hits = sorted(
        getattr(node, "lineno", 0)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_write_open(node, aliases)
    )
    assert hits == [3, 4, 5, 6, 7, 8, 9, 10, 14]


def test_phantom_spawn_allowlist_entry_is_not_in_the_scan() -> None:
    """Deleting a real allowlist entry must fail; inventing one must also fail."""
    found = set(_spawn_sites())
    phantom = "src/mangomas/agents/chat.py:1:subprocess-import"
    assert phantom not in found
    assert found == set(_ALLOWED_SPAWN_SITES)
