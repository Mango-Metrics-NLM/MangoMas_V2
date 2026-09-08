"""Contracts package must not import mangomas internals."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACTS_SRC = _REPO_ROOT / "mango-integration-contracts" / "src" / "mango_contracts"
_MANGOMAS_SRC = _REPO_ROOT / "src" / "mangomas"
_CONTRACT_TESTS = Path(__file__).resolve().parent


def _mangomas_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "mangomas" or alias.name.startswith("mangomas."):
                    found.append(f"{path.name}: import {alias.name}")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "mangomas" or node.module.startswith("mangomas."))
        ):
            found.append(f"{path.name}: from {node.module}")
    return found


def _contracts_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "mango_contracts" or alias.name.startswith("mango_contracts."):
                    found.append(f"{path.name}: import {alias.name}")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "mango_contracts" or node.module.startswith("mango_contracts."))
        ):
            found.append(f"{path.name}: from {node.module}")
    return found


def test_contracts_do_not_import_mangomas() -> None:
    offenders: list[str] = []
    for path in _CONTRACTS_SRC.rglob("*.py"):
        offenders.extend(_mangomas_imports(path))
    assert offenders == []


def test_contract_tests_do_not_import_mangomas() -> None:
    offenders: list[str] = []
    for path in _CONTRACT_TESTS.rglob("*.py"):
        offenders.extend(_mangomas_imports(path))
    assert offenders == []


def test_mangomas_does_not_import_contracts_yet() -> None:
    """Runtime emission is a later PR; this change must stay byte-identical."""
    offenders: list[str] = []
    for path in _MANGOMAS_SRC.rglob("*.py"):
        offenders.extend(_contracts_imports(path))
    assert offenders == []
