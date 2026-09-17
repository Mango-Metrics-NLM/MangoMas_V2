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
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import mangomas.composition as composition_facade
import mangomas.config as facade

# Resolved from `__file__`, not the CWD. `pytest` can be invoked from anywhere,
# and this suite itself contains a `monkeypatch.chdir` — a relative
# `Path("pyproject.toml")` reads whatever directory the process happens to be
# in. Same idiom as `tests/test_check_coverage.py`, at the same depth.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Decomposed packages and the group modules their facade re-exports from.
# spec-0015 is complete at three: config/, telemetry/ and cli/. `composition/`
# is a fourth, later decomposition of the same ADR-0019 shape (not part of
# spec-0015's own requirement list — see the composition-facade section below).
_FACADES: dict[str, tuple[str, ...]] = {
    "mangomas.cli": (
        "_app",
        "_runtime",
        "commands._eval_config",
        "commands.chat",
        "commands.eval",
        "commands.rag",
        "commands.workflow",
        "exit_codes",
    ),
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
        "signal",
        "storage",
        "workflow",
    ),
    "mangomas.composition": (
        "_registries",
        "agents",
        "builder",
        "embeddings",
        "harness",
        "llm",
        "memory",
        "rag",
        "secrets",
        "signal",
        "storage",
        "vector",
    ),
    "mangomas.api.middleware": (
        "access_log",
        "backpressure",
        "tenancy",
    ),
    "mangomas.core.orchestrator": ("_client",),
    "mangomas.workflow.predicate": ("_client",),
    "mangomas.adapters.llm.vertex": ("_client",),
}


# The module that *is* the facade. For `config` and `telemetry` that is the
# package itself, but `cli`'s facade is `cli/main.py`: `[project.scripts]`
# resolves `mangomas.cli.main:app`, so the entry point — not `cli/__init__.py` —
# is the path that must keep working.
_FACADE_MODULES: dict[str, str] = {"mangomas.cli": "mangomas.cli.main"}


def _facade_of(package: str) -> ModuleType:
    return importlib.import_module(_FACADE_MODULES.get(package, package))


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
#   `_build` /
#   `_close_orchestrator` — the orchestrator patch seam. Every CLI suite
#                       replaces these; a facade that rebound rather than
#                       re-exported would leave the real ones reachable and
#                       the tests would build live orchestrators while passing.
#   `_emit_sinks`     — asserted directly by `tests/eval/test_cli_eval.py`.
_PRIVATE_FACADE_CONTRACT: dict[str, dict[str, str]] = {
    "mangomas.cli": {
        "_build": "_runtime",
        "_close_orchestrator": "_runtime",
        "_emit_sinks": "commands.eval",
    },
    "mangomas.telemetry": {
        "_state": "_state",
        "_scoped_tracers": "_state",
        "_build_span_exporter": "exporters",
        "_build_metric_reader": "exporters",
        "_lazy_cloud_trace_exporter": "exporters",
        "_lazy_cloud_monitoring_exporter": "exporters",
    },
    # Composition's factory functions are underscore-prefixed by convention
    # (they are wiring internals, not an API meant for casual use) yet are
    # still part of the documented facade contract: `tests/composition/`
    # imports every one of them directly from `mangomas.composition`, and
    # `mango-*-dev` agents monkeypatch several through this exact path. The
    # `_owned_names()` helper filters underscore-prefixed names as "not
    # public", so without an explicit entry here almost this entire facade
    # would be invisible to the identity/completeness tests below — which is
    # exactly how a stray `__all__` typo (`_vector_embedding_factory` instead
    # of `_vertex_embedding_factory`, caught in review, not by any test)
    # shipped once already.
    "mangomas.composition": {
        "_HarnessOrchestrator": "harness",
        "_build_gcp_secrets_provider": "secrets",
        "_build_rag_tools": "rag",
        "_attach_cognitive_extras": "signal",
        "_chroma_vector_factory": "vector",
        "_file_memory_factory": "memory",
        "_lmstudio_embedding_factory": "embeddings",
        "_lmstudio_factory": "llm",
        "_memory_registry": "_registries",
        "_postgres_factory": "storage",
        "_resolve_llm_secrets": "secrets",
        "_sentence_transformers_embedding_factory": "embeddings",
        "_sqlite_factory": "storage",
        "_storage_registry": "_registries",
        "_vector_registry": "_registries",
        "_vertex_embedding_factory": "embeddings",
        "_vertex_factory": "llm",
    },
    "mangomas.api.middleware": {
        "_BAGGAGE_KEY": "access_log",
    },
    "mangomas.adapters.llm.vertex": {
        "_translate_vertex_error": "_client",
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
        if "config._root" in Path(source).read_text(encoding="utf-8"):
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


# ── CLI facade (spec-0015 R1) ─────────────────────────────────────────────────


def _module_scope_imports(module: str) -> list[str]:
    """Every module name *module* imports, as dotted strings.

    AST-based rather than a substring scan, which the config and telemetry
    guards above could get away with and this one cannot: the command modules'
    docstrings name `mangomas.cli._app` precisely to explain why they must not
    import it, and a text search would read the explanation as the offence.

    Walks the whole tree, not just module scope, so a deferred import inside a
    function counts too — that is still a cycle, just one that fires on call
    rather than on import.
    """
    mod = importlib.import_module(module)
    source = getattr(mod, "__file__", None)
    assert source is not None, f"{module} has no source file"
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("submodule", _FACADES["mangomas.cli"])
def test_cli_facade_reexports_are_identical_objects(submodule: str) -> None:
    """`main.X is home.X` for every public name a CLI group module owns.

    `app` is the one that would hurt: `[project.scripts]` resolves
    `mangomas.cli.main:app`, so a facade re-exporting a *different* Typer
    instance would give the console script an app with no commands on it while
    every import in the suite still succeeded.
    """
    facade_mod = _facade_of("mangomas.cli")
    home = importlib.import_module(f"mangomas.cli.{submodule}")
    for name in _owned_names("mangomas.cli", submodule):
        if not hasattr(facade_mod, name):
            continue
        assert getattr(facade_mod, name) is getattr(home, name), (
            f"mangomas.cli.main.{name} is not the same object as mangomas.cli.{submodule}.{name}"
        )


def test_every_owned_cli_name_reaches_the_facade() -> None:
    """No public name may be stranded in a command module.

    Unlike the config equivalent this does **not** skip underscore-prefixed
    group modules: `_app` is where `app` itself is defined, so skipping it by
    naming convention would skip the single most load-bearing re-export in the
    package.
    """
    stranded: list[str] = []
    facade_mod = _facade_of("mangomas.cli")
    for submodule in _FACADES["mangomas.cli"]:
        for name in _owned_names("mangomas.cli", submodule):
            if not hasattr(facade_mod, name):
                stranded.append(f"{submodule}.{name}")
    assert stranded == [], f"public names not re-exported by mangomas.cli.main: {sorted(stranded)}"


@pytest.mark.parametrize(("name", "home"), sorted(_PRIVATE_FACADE_CONTRACT["mangomas.cli"].items()))
def test_cli_private_contract_names_are_identical(name: str, home: str) -> None:
    facade_mod = _facade_of("mangomas.cli")
    home_mod = importlib.import_module(f"mangomas.cli.{home}")
    assert hasattr(facade_mod, name), f"mangomas.cli.main no longer re-exports {name!r}"
    assert getattr(facade_mod, name) is getattr(home_mod, name)


def test_cli_command_modules_do_not_import_the_assembly_root() -> None:
    """Dependency direction: `_app` imports the commands, never the reverse.

    `cli/__init__.py` does `from mangomas.cli.main import app`, so the package
    already has a live import cycle that the split widens: a command module
    importing `_app` or `main` would be reached mid-initialisation, at which
    point `app` may not exist yet. That failure depends on which module the
    process imports first, so it can hide from a suite that always enters
    through the same door.
    """
    offenders: list[str] = []
    for submodule in _FACADES["mangomas.cli"]:
        if not submodule.startswith("commands."):
            continue
        for imported in _module_scope_imports(f"mangomas.cli.{submodule}"):
            if imported in {"mangomas.cli._app", "mangomas.cli.main"}:
                offenders.append(f"{submodule} -> {imported}")
    assert offenders == [], f"command modules importing the assembly root: {offenders}"


def test_cli_base_modules_import_nothing_from_their_own_package() -> None:
    """`exit_codes` and `_runtime` are the base layer.

    Every command module depends on both, so a local import here — even a
    sibling one — would put them inside the cycle instead of underneath it.
    """
    offenders: list[str] = []
    for submodule in ("exit_codes", "_runtime"):
        for imported in _module_scope_imports(f"mangomas.cli.{submodule}"):
            if imported.startswith("mangomas.cli"):
                offenders.append(f"{submodule} -> {imported}")
    assert offenders == [], f"base modules importing from mangomas.cli: {offenders}"


# ── Composition facade (ADR-0019, outside spec-0015's own requirement list) ──


def test_composition_facade_exports_are_non_empty() -> None:
    assert _public_names(composition_facade)


@pytest.mark.parametrize("name", _public_names(composition_facade))
def test_composition_facade_name_is_importable(name: str) -> None:
    """Every name in `__all__` must actually resolve.

    This is exactly the check a stray `__all__` typo breaks: a name *listed*
    but never bound under that spelling. No import statement anywhere
    references the wrong name, so every existing `from mangomas.composition
    import X` keeps working — only `__all__`'s own advertised surface lies.
    That is precisely how `_vector_embedding_factory` (should have been
    `_vertex_embedding_factory`) shipped in this package's first commit and
    needed a manual review pass to catch, rather than failing here.
    """
    assert hasattr(composition_facade, name), (
        f"mangomas.composition.__all__ advertises {name!r} but does not define it"
    )


@pytest.mark.parametrize("submodule", _FACADES["mangomas.composition"])
def test_composition_facade_reexports_are_identical_objects(submodule: str) -> None:
    """`facade.X is home_module.X` for every *public* (non-underscore) name a
    submodule owns — `build_orchestrator`, `agent_registry`, `AgentFactory`,
    etc. Composition's factory functions are underscore-prefixed even though
    `__all__` exports them; those go through
    `test_composition_private_contract_names_are_identical` below instead,
    mirroring the cli/telemetry private-contract pattern.
    """
    home = importlib.import_module(f"mangomas.composition.{submodule}")
    for name in _owned_names("mangomas.composition", submodule):
        if not hasattr(composition_facade, name):
            continue
        assert getattr(composition_facade, name) is getattr(home, name), (
            f"mangomas.composition.{name} is not the same object as "
            f"mangomas.composition.{submodule}.{name}"
        )


@pytest.mark.parametrize(
    ("name", "home"), sorted(_PRIVATE_FACADE_CONTRACT["mangomas.composition"].items())
)
def test_composition_private_contract_names_are_identical(name: str, home: str) -> None:
    home_mod = importlib.import_module(f"mangomas.composition.{home}")
    assert hasattr(composition_facade, name), f"mangomas.composition no longer re-exports {name!r}"
    assert getattr(composition_facade, name) is getattr(home_mod, name)


def test_every_owned_public_name_reaches_the_composition_facade() -> None:
    """No public name may be stranded in a composition submodule."""
    stranded: list[str] = []
    for submodule in _FACADES["mangomas.composition"]:
        for name in _owned_names("mangomas.composition", submodule):
            if not hasattr(composition_facade, name):
                stranded.append(f"{submodule}.{name}")
    assert stranded == [], (
        f"public names not re-exported by mangomas.composition: {sorted(stranded)}"
    )


# ── API middleware facade (ADR-0019, applied again) ───────────────────────────


@pytest.mark.parametrize("submodule", _FACADES["mangomas.api.middleware"])
def test_middleware_facade_reexports_are_identical_objects(submodule: str) -> None:
    facade_mod = importlib.import_module("mangomas.api.middleware")
    home = importlib.import_module(f"mangomas.api.middleware.{submodule}")
    for name in _owned_names("mangomas.api.middleware", submodule):
        if not hasattr(facade_mod, name):
            continue
        assert getattr(facade_mod, name) is getattr(home, name), (
            f"mangomas.api.middleware.{name} is not the same object as "
            f"mangomas.api.middleware.{submodule}.{name}"
        )


def test_every_owned_middleware_name_reaches_the_facade() -> None:
    stranded: list[str] = []
    facade_mod = importlib.import_module("mangomas.api.middleware")
    for submodule in _FACADES["mangomas.api.middleware"]:
        for name in _owned_names("mangomas.api.middleware", submodule):
            if not hasattr(facade_mod, name):
                stranded.append(f"{submodule}.{name}")
    assert stranded == [], (
        f"public names not re-exported by mangomas.api.middleware: {sorted(stranded)}"
    )


@pytest.mark.parametrize(
    ("name", "home"), sorted(_PRIVATE_FACADE_CONTRACT["mangomas.api.middleware"].items())
)
def test_middleware_private_contract_names_are_identical(name: str, home: str) -> None:
    facade_mod = importlib.import_module("mangomas.api.middleware")
    home_mod = importlib.import_module(f"mangomas.api.middleware.{home}")
    assert hasattr(facade_mod, name), f"mangomas.api.middleware no longer re-exports {name!r}"
    assert getattr(facade_mod, name) is getattr(home_mod, name)


# ── core.tools facade (spec-0015 R4) ─────────────────────────────────────────

# Every pre-extraction name that moved from `core/tools.py` to
# `core/structured.py`. `core/tools.py` is a *module* facade, not a package
# root, so the `_FACADES` machinery above (which enumerates group modules)
# does not apply; the contract is a fixed roster instead. Private names are
# listed deliberately: `_ERROR_DETAIL_TRUNCATE` is asserted by
# `tests/test_tools.py::test_error_detail_truncate_matches_config` through the
# facade, and `_extract_json_span` is the shared span heuristic `ToolCallParser`
# itself calls — a facade that rebound either would drift silently.
_CORE_TOOLS_FACADE_NAMES: tuple[str, ...] = (
    "_DEFAULT_STRUCTURED_PROMPT_TEMPLATE",
    "_ERROR_DETAIL_TRUNCATE",
    "_extract_json_span",
    "build_structured_prompt",
    "parse_or_recover",
)


def test_core_tools_facade_roster_is_populated() -> None:
    """An empty roster would make the parametrised test below vacuous."""
    assert _CORE_TOOLS_FACADE_NAMES


@pytest.mark.parametrize("name", _CORE_TOOLS_FACADE_NAMES)
def test_core_tools_facade_reexports_are_identical_objects(name: str) -> None:
    """`core.tools.X is core.structured.X` for every name the extraction moved.

    Identity, not mere importability: `tests/agents/test_prompt.py` computes
    its oracle via the facade path while `agents/_structured.py` imports the
    home module — a copy that satisfied equality would still let the two
    drift apart at runtime.
    """
    facade_mod = importlib.import_module("mangomas.core.tools")
    home_mod = importlib.import_module("mangomas.core.structured")
    assert hasattr(facade_mod, name), f"mangomas.core.tools no longer re-exports {name!r}"
    assert getattr(facade_mod, name) is getattr(home_mod, name), (
        f"mangomas.core.tools.{name} is not the same object as mangomas.core.structured.{name}"
    )


def test_core_structured_is_a_protected_path() -> None:
    """Extracted contract code must not quietly leave governance.

    spec-0015 R4's governance follow-through: `core/structured.py` joined
    `[tool.mangomas.governance].protected_paths` in the same commit that
    created it. Without this pin, removing the entry would fail nothing —
    the fallback-lock-step tests in `tests/harness/test_governance.py` only
    prove table == fallback, which both dropping it satisfies.
    """
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    protected = pyproject["tool"]["mangomas"]["governance"]["protected_paths"]
    assert "src/mangomas/core/structured.py" in protected


def test_console_script_entry_point_matches_the_facade() -> None:
    """`pyproject.toml` names the facade, and the facade is what tests patch.

    Pinned because the two could drift apart silently: repointing the entry
    point at `mangomas.cli._app:app` would work perfectly for users and quietly
    make this whole file's guarantees irrelevant to what actually ships.
    """
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["mangomas"] == "mangomas.cli.main:app"


# ── God-file decomposition facades (ADR-0019) ────────────────────────────────

# These three modules were decomposed from single-file modules into packages
# with `__init__.py` facades re-exporting from `_client.py`. The contract is
# the same as config/telemetry/cli above: identity, not just importability.


@pytest.mark.parametrize(
    "package",
    [
        "mangomas.core.orchestrator",
        "mangomas.workflow.predicate",
        "mangomas.adapters.llm.vertex",
    ],
)
def test_decomposed_facade_reexports_are_identical_objects(package: str) -> None:
    """Every public name in the facade is the same object as in `_client`."""
    facade_mod = importlib.import_module(package)
    home = importlib.import_module(f"{package}._client")
    for name in _owned_names(package, "_client"):
        if not hasattr(facade_mod, name):
            continue
        assert getattr(facade_mod, name) is getattr(home, name), (
            f"{package}.{name} is not the same object as {package}._client.{name}"
        )


@pytest.mark.parametrize(
    "package",
    [
        "mangomas.core.orchestrator",
        "mangomas.workflow.predicate",
        "mangomas.adapters.llm.vertex",
    ],
)
def test_decomposed_facade_exports_are_non_empty(package: str) -> None:
    """The facade must export at least one name (vacuity guard)."""
    facade_mod = importlib.import_module(package)
    assert _public_names(facade_mod), f"{package} facade exports nothing"


@pytest.mark.parametrize(
    ("name", "home"), sorted(_PRIVATE_FACADE_CONTRACT["mangomas.adapters.llm.vertex"].items())
)
def test_vertex_private_contract_names_are_identical(name: str, home: str) -> None:
    """``_translate_vertex_error`` must be the same object through the facade."""
    facade_mod = importlib.import_module("mangomas.adapters.llm.vertex")
    home_mod = importlib.import_module(f"mangomas.adapters.llm.vertex.{home}")
    assert hasattr(facade_mod, name), f"mangomas.adapters.llm.vertex no longer re-exports {name!r}"
    assert getattr(facade_mod, name) is getattr(home_mod, name)
