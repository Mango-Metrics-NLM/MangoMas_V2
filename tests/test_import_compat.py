"""Facade-identity contract for decomposed modules (spec-0015 / ADR-0019).

A module that grows past readability is split into a package, and its old
import path becomes a **permanent re-export facade** — not a deprecation shim.
The guarantee is stronger than "the import still works": every public name
reached through the facade must be the *same object* as the one its home
module defines.

Identity, not equality, is the assertion that matters. Two `Settings` classes
that merely compare equal would still break `isinstance` checks, pydantic model
resolution, and `monkeypatch.setattr` targeting — silently, and only at
runtime.

**What this file cannot prove**, recorded so nobody mistakes green here for
total safety: a facade preserves object identity, not module-global *name
binding*. `monkeypatch.setattr(facade, "helper", ...)` rebinds the name in the
facade only; a submodule that calls `helper()` through its own globals never
sees the patch. That is a real seam change and needs its own test at the call
site — see the amended acceptance bar in `specs/0015-package-decomposition.md`.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import ModuleType

import pytest

import mangomas.config as facade

# Decomposed packages and the group modules their facade re-exports from.
# Extend this as spec-0015 proceeds through telemetry/ and cli/.
_FACADES: dict[str, tuple[str, ...]] = {
    "mangomas.config": (
        "_root",
        "_shared",
        "agents",
        "api",
        "evaluation",
        "harness",
        "llm",
        "observability",
        "rag",
        "secrets",
        "storage",
        "workflow",
    ),
}


# Module-level infrastructure that is public by naming convention but is not
# part of any module's API. The codebase uses a bare `logger` in 43 modules and
# `_logger` in none, so renaming to satisfy a test would break the convention
# rather than the other way round.
_NON_API_NAMES = frozenset({"logger"})


def _public_names(module: ModuleType) -> list[str]:
    exported = getattr(module, "__all__", None)
    if exported is not None:
        return sorted(exported)
    return sorted(n for n in vars(module) if not n.startswith("_"))


def _owned_names(package: str, submodule: str) -> list[str]:
    """Return the public names a group module *defines*, not ones it imports.

    Read from the source AST rather than from `vars()` + `__module__`. That
    attribute exists on classes and functions but not on plain constants or
    module objects, so an attribute-based check reports an imported `int`,
    `str` or `logging` module as locally owned — which wrongly demanded that
    `DEFAULT_TENANT` (imported from `mangomas.tenancy`) be re-exported here.
    A definition is a definition; the AST says so unambiguously.
    """
    mod = importlib.import_module(f"{package}.{submodule}")
    source_path = getattr(mod, "__file__", None)
    assert source_path is not None, f"{package}.{submodule} has no source file"
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))

    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return [n for n in names if not n.startswith("_") and n not in _NON_API_NAMES]


# ── Self-guards ───────────────────────────────────────────────────────────────


def test_facade_registry_is_populated() -> None:
    """An empty registry would make every parametrised test below vacuous."""
    assert _FACADES
    assert all(subs for subs in _FACADES.values())


def test_config_facade_exports_are_non_empty() -> None:
    assert _public_names(facade)


# ── The contract ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _public_names(facade))
def test_config_facade_name_is_importable(name: str) -> None:
    """Every advertised name resolves. `__all__` listing a name the package no
    longer defines would break `from mangomas.config import *` at runtime while
    every direct import kept working."""
    assert hasattr(facade, name), (
        f"mangomas.config.__all__ advertises {name!r} but does not define it"
    )


@pytest.mark.parametrize("submodule", _FACADES["mangomas.config"])
def test_config_facade_reexports_are_identical_objects(submodule: str) -> None:
    """`facade.X is home_module.X` for every public name the group owns.

    Identity is what `isinstance`, pydantic field resolution and monkeypatch
    targeting all depend on. A re-export that produced a copy would satisfy
    equality and still break all three.
    """
    home = importlib.import_module(f"mangomas.config.{submodule}")
    for name in _owned_names("mangomas.config", submodule):
        if not hasattr(facade, name):
            continue
        assert getattr(facade, name) is getattr(home, name), (
            f"mangomas.config.{name} is not the same object as mangomas.config.{submodule}.{name}"
        )


def test_every_owned_public_name_reaches_the_facade() -> None:
    """No public name may be stranded in a group module.

    This is the direction that rots silently: adding a constant to
    `config/llm.py` and forgetting the facade leaves every existing
    `from mangomas.config import ...` caller unable to see it, with nothing
    failing until someone tries.
    """
    stranded: list[str] = []
    for submodule in _FACADES["mangomas.config"]:
        if submodule.startswith("_"):
            continue  # private group modules are deliberately not re-exported
        for name in _owned_names("mangomas.config", submodule):
            if not hasattr(facade, name):
                stranded.append(f"{submodule}.{name}")
    assert stranded == [], f"public names not re-exported by mangomas.config: {sorted(stranded)}"


def test_settings_identity_survives_both_import_paths() -> None:
    """The named case that matters most: `Settings` drives every consumer.

    Resolved through `importlib` rather than two function-level `from` imports:
    the point is to compare what each *path* yields, and going through the
    module objects says that directly instead of relying on import statements
    whose bindings a reader has to trace.
    """
    via_facade = importlib.import_module("mangomas.config").Settings
    via_home = importlib.import_module("mangomas.config._root").Settings

    assert via_facade is via_home


def test_group_modules_do_not_import_the_root() -> None:
    """Dependency direction: `_root` imports the groups, never the reverse.

    A group importing `_root` would create the cycle the split exists to avoid,
    and would make `mangomas.config.llm` transitively construct the whole tree.
    """
    offenders = []
    for submodule in _FACADES["mangomas.config"]:
        if submodule == "_root":
            continue
        mod = importlib.import_module(f"mangomas.config.{submodule}")
        source = getattr(mod, "__file__", None)
        if source is None:  # pragma: no cover - namespace package, not expected
            continue
        with open(source, encoding="utf-8") as handle:
            if "config._root" in handle.read():
                offenders.append(submodule)
    assert offenders == [], f"group modules importing _root: {offenders}"
