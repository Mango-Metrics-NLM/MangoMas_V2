"""Contract test pinning the CLI's public surface.

`mangomas` is a console script declared in `pyproject.toml`
(`mangomas = "mangomas.cli.main:app"`), so its command tree, option names and
exit codes are a user-facing contract in the same way the HTTP routes are —
but nothing asserted them. A refactor could drop a command, rename an option,
or leave a sub-app unregistered, and only a human running the CLI would notice.

This exists specifically ahead of the spec-0015 `cli/main.py` decomposition.
The entry point resolves `mangomas.cli.main:app`, and a facade preserves object
identity but not assembly order: if the sub-apps are registered after `app` is
re-exported, the object still imports fine and simply has fewer commands. That
failure is invisible to an import-compat test.

Deliberately asserts **structure, not rendered text**. Golden-file `--help`
output breaks on any Typer or Rich version bump, which trains people to
regenerate the snapshot rather than read the diff — the opposite of a useful
guard. Command names, nesting and option names are the actual contract and are
stable across those upgrades.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from typer.main import get_command

from mangomas.cli.main import app
from tests.constants import (
    EXPECTED_CLI_COMMANDS,
    EXPECTED_CLI_PARAMS,
    EXPECTED_CLI_ROOT_COMMANDS,
)


def _subcommands(command: Any) -> dict[str, Any]:
    """Return a command's children, or `{}` for a leaf.

    One accessor for all three call sites. Typer's `get_command` is typed as
    returning `Command`, which has no `.commands` — that lives on `TyperGroup`,
    and `TyperGroup` does **not** subclass `click.Group` in current versions.
    So this is duck-typed on purpose, and centralising it keeps the reason in
    one place rather than repeating a `getattr` three times.
    """
    children = getattr(command, "commands", None)
    return dict(children) if children else {}


def _walk(command: Any, prefix: str = "") -> dict[str, Any]:
    """Return {dotted path: click command} for the whole tree.

    Duck-types on `.commands` rather than `isinstance(..., click.Group)`:
    Typer's `TyperGroup` does **not** subclass `click.Group` in current
    versions (it derives from `typer._click.core.Command`), so an isinstance
    check silently walks nothing and every assertion below passes vacuously.
    """
    found: dict[str, Any] = {}
    for name, sub in _subcommands(command).items():
        path = f"{prefix}{name}"
        found[path] = sub
        found.update(_walk(sub, prefix=f"{path} "))
    return found


def _tree() -> dict[str, Any]:
    return _walk(get_command(app))


def _param_names(command: Any) -> set[str]:
    """Long options plus positional argument names for one command.

    Positionals are detected by their opts carrying no leading dashes, not by
    an empty `opts` list — click populates `Argument.opts` with the bare
    argument name (`['message']`), so an emptiness check finds nothing and a
    dropped or renamed positional would slip through.
    """
    names: set[str] = set()
    for param in getattr(command, "params", []):
        opts = list(getattr(param, "opts", [])) + list(getattr(param, "secondary_opts", []))
        long_opts = [o for o in opts if o.startswith("--")]
        if long_opts:
            names.update(long_opts)
        elif opts:  # positional argument
            names.add(f"<{opts[0]}>")
    return names


# ── Self-guards ───────────────────────────────────────────────────────────────


def test_command_tree_is_non_empty() -> None:
    """`_walk` returning {} would make every assertion below vacuously true —
    which is exactly what happened with an `isinstance(click.Group)` check."""
    assert _tree()


def test_expected_surface_constants_are_populated() -> None:
    assert EXPECTED_CLI_COMMANDS
    assert EXPECTED_CLI_ROOT_COMMANDS
    assert EXPECTED_CLI_PARAMS


# ── The contract ──────────────────────────────────────────────────────────────


def test_command_tree_matches_the_declared_set() -> None:
    """Set equality, so a change names what appeared or vanished."""
    on_app = set(_tree())
    assert on_app == set(EXPECTED_CLI_COMMANDS), (
        f"unexpected: {sorted(on_app - set(EXPECTED_CLI_COMMANDS))}; "
        f"missing: {sorted(set(EXPECTED_CLI_COMMANDS) - on_app)}"
    )


def test_root_commands_are_all_registered() -> None:
    """Catches a sub-app registered too late to be picked up.

    This is the decomposition's most likely silent failure: `app` is exported
    before `app.add_typer(rag_app, ...)` runs, so `mangomas rag` simply does
    not exist while every import still succeeds.
    """
    root = set(_subcommands(get_command(app)))
    assert root == set(EXPECTED_CLI_ROOT_COMMANDS), (
        f"unexpected: {sorted(root - set(EXPECTED_CLI_ROOT_COMMANDS))}; "
        f"missing: {sorted(set(EXPECTED_CLI_ROOT_COMMANDS) - root)}"
    )


@pytest.mark.parametrize("path", sorted(EXPECTED_CLI_PARAMS))
def test_command_options_are_unchanged(path: str) -> None:
    """Renaming or dropping a flag or positional breaks every script invoking it."""
    command = _tree().get(path)
    assert command is not None, f"command {path!r} no longer exists"
    assert _param_names(command) == set(EXPECTED_CLI_PARAMS[path]), (
        f"{path}: options changed — "
        f"unexpected {sorted(_param_names(command) - set(EXPECTED_CLI_PARAMS[path]))}, "
        f"missing {sorted(set(EXPECTED_CLI_PARAMS[path]) - _param_names(command))}"
    )


def test_entry_point_target_resolves_to_the_assembled_app() -> None:
    """`pyproject.toml` declares `mangomas = "mangomas.cli.main:app"`.

    Resolved the way the console script does — import the module named before
    the colon, then take the attribute after it — so a facade that re-exported
    a *different* or partially-assembled Typer instance is caught here rather
    than by a user.
    """
    module = importlib.import_module("mangomas.cli.main")
    entry_app = module.app
    assert entry_app is app
    assert set(_subcommands(get_command(entry_app))) == set(EXPECTED_CLI_ROOT_COMMANDS)
