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
    "mangomas.telemetry": (
        "_state",
        "exporters",
        "logs",
        "meters",
        "scoped",
        "tracing",
    ),
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


# Private names that are deliberately part of a facade's contract because
# something outside the package reaches them. These are invisible to the
# public-surface tests above (which filter leading underscores), so a broken
# re-export here would go unnoticed — and two of them are load-bearing at
# runtime, not just in tests:
#
#   `_state`          — `tests/test_telemetry.py` runs `t._state.configured` in
#                       a subprocess; two instances would defeat
#                       `configure_telemetry`'s idempotency guard.
#   `_scoped_tracers` — cleared between tests by `_reset()`. A facade that
#                       rebuilt the dict rather than re-exporting the same
#                       object would leave stale cached providers behind and
#                       fail order-dependently: green alone, red in a full run.
_PRIVATE_FACADE_CONTRACT: dict[str, dict[str, str]] = {
    "mangomas.telemetry": {
        "_state": "_state",
        "_scoped_tracers": "_state",
        "_build_span_exporter": "exporters",
        "_build_metric_reader": "exporters",
        "_lazy_cloud_trace_exporter": "exporters",
        "_lazy_cloud_monitoring_exporter": "exporters",
    },
}


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


# ── Telemetry facade (spec-0015 R3) ───────────────────────────────────────────


@pytest.mark.parametrize("submodule", _FACADES["mangomas.telemetry"])
def test_telemetry_facade_reexports_are_identical_objects(submodule: str) -> None:
    facade_mod = importlib.import_module("mangomas.telemetry")
    home = importlib.import_module(f"mangomas.telemetry.{submodule}")
    for name in _owned_names("mangomas.telemetry", submodule):
        if not hasattr(facade_mod, name):
            continue
        assert getattr(facade_mod, name) is getattr(home, name), (
            f"mangomas.telemetry.{name} is not the same object as "
            f"mangomas.telemetry.{submodule}.{name}"
        )


@pytest.mark.parametrize(
    ("name", "home"), sorted(_PRIVATE_FACADE_CONTRACT["mangomas.telemetry"].items())
)
def test_telemetry_private_contract_names_are_identical(name: str, home: str) -> None:
    """Private names outside code reaches, asserted by identity.

    `_scoped_tracers` in particular must be the *same dict*: the suite clears it
    between tests, and clearing a copy would silently leave cached providers
    live.
    """
    facade_mod = importlib.import_module("mangomas.telemetry")
    home_mod = importlib.import_module(f"mangomas.telemetry.{home}")
    assert hasattr(facade_mod, name), f"mangomas.telemetry no longer re-exports {name!r}"
    assert getattr(facade_mod, name) is getattr(home_mod, name)


def test_telemetry_base_modules_do_not_import_siblings() -> None:
    """`_state`, `logs` and `exporters` are the base layer: nothing local.

    Stricter than the config equivalent because telemetry is a layered DAG
    rather than a flat partition. If a base module grows a sibling import, the
    layering claim in the package docstring stops being true.
    """
    offenders: list[str] = []
    for submodule in ("_state", "logs", "exporters"):
        mod = importlib.import_module(f"mangomas.telemetry.{submodule}")
        source = getattr(mod, "__file__", None)
        assert source is not None
        text = Path(source).read_text(encoding="utf-8")
        if "from mangomas.telemetry" in text or "import mangomas.telemetry" in text:
            offenders.append(submodule)
    assert offenders == [], f"base modules importing a sibling: {offenders}"


def test_no_telemetry_module_imports_a_cloud_sdk_at_module_scope() -> None:
    """The `gcp` extra must stay optional: importing the package must not need it.

    `exporters.py` is now the only module allowed to name a cloud SDK, and only
    inside its `_lazy_*` helpers. A module literally called `exporters` reads
    like the natural home for such an import, so this is enforced rather than
    left to review.
    """
    package = importlib.import_module("mangomas.telemetry")
    pkg_dir = Path(str(package.__file__)).parent
    offenders: list[str] = []
    for path in sorted(pkg_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only — nested imports are the lazy ones
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.startswith("opentelemetry.exporter") for n in names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"cloud SDK imported at module scope: {offenders}"
