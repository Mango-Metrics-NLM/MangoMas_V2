"""Shared helper for loading ``scripts/*.py`` modules in tests.

The ``scripts/`` directory is not a Python package — it ships executable
entry points used by hooks and CI.  Tests that exercise those scripts
need to import them by file path; this helper centralises that pattern
so we don't duplicate ``importlib.util.spec_from_file_location`` boilerplate
across every test file.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

# Single source of truth: repo root resolves from this file's location.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR: Final[Path] = _REPO_ROOT / "scripts"


def load_script_module(script_name: str, *, alias: str | None = None) -> ModuleType:
    """Import ``scripts/<script_name>`` as an isolated module.

    Parameters
    ----------
    script_name:
        Filename including the ``.py`` suffix, e.g. ``"lint_agent_frontmatter.py"``.
    alias:
        Optional ``sys.modules`` registration name. Defaults to a synthetic
        ``"_<stem>_under_test"`` so each test file gets a fresh import.

    Raises
    ------
    FileNotFoundError
        When the script does not exist under ``scripts/``.
    """
    path = _SCRIPTS_DIR / script_name
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")

    module_name = alias or f"_{path.stem}_under_test"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
