"""Contract tests for the per-package coverage gate.

`scripts/check_coverage.py` is the authoritative coverage gate, and its floor
globs have a **fail-open** failure mode: `coverage report --include=<glob>`
that matches fewer files than intended still prints a percentage and still
passes. Nothing reports that the glob stopped covering something.

That already happened once. `api/*.py` was flat when `api/` grew a `routes/`
subpackage, so the new routers went unmeasured while the gate stayed green.
`cli/` is next in line — spec-0015 decomposes `cli/main.py` into a command
package — so the rule is pinned here rather than left to review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

import pytest

from tests._script_loader import load_script_module


class _FloorLike(Protocol):
    """The shape `check_coverage.Floor` exposes.

    `load_script_module` returns an untyped module, so its `FLOORS` entries are
    `Any`. Naming the shape here keeps `--strict` honest without scattering
    `getattr` calls that read as defensive but are not.
    """

    include: str
    minimum: int
    label: str


_REPO_ROOT = Path(__file__).resolve().parents[1]
_checker = load_script_module("check_coverage.py")

_RECURSIVE_GLOB = "**/*.py"
_FLAT_GLOB = "*.py"


def _all_floors() -> list[_FloorLike]:
    return cast("list[_FloorLike]", list(_checker.FLOORS))


def _directory_floors() -> list[_FloorLike]:
    """Return floors that scope a directory rather than a single module."""
    return [f for f in _all_floors() if f.include.endswith(_FLAT_GLOB)]


def test_floors_are_declared() -> None:
    """Guard: an empty FLOORS list would make every assertion below vacuous."""
    assert _all_floors()
    assert _directory_floors()


@pytest.mark.parametrize("floor", _directory_floors(), ids=lambda f: f.label)
def test_directory_floors_use_a_recursive_glob(floor: _FloorLike) -> None:
    """A flat `*.py` silently stops measuring a new subpackage — and passes.

    `coverage report --include` treats `**` as "zero or more directories", so
    the recursive form is a strict superset: it matches every top-level file a
    flat glob does, plus anything nested. There is no case where the flat form
    is the correct choice for a directory.
    """
    assert floor.include.endswith(_RECURSIVE_GLOB), (
        f"floor {floor.label!r} uses the non-recursive {floor.include!r}; "
        f"use '**/*.py' so a future subpackage is still measured"
    )


@pytest.mark.parametrize("floor", _all_floors(), ids=lambda f: f.label)
def test_every_floor_target_exists_on_disk(floor: _FloorLike) -> None:
    """A floor pointing at a moved or renamed path matches nothing and passes.

    This is the same fail-open shape as the flat glob, reached a different way:
    spec-0015 turns `config.py` and `telemetry.py` into packages, at which
    point their single-module floors stop resolving.
    """
    root = floor.include.split("*", 1)[0].rstrip("/")
    assert (_REPO_ROOT / root).exists(), (
        f"floor {floor.label!r} targets {floor.include!r}, but {root!r} does "
        f"not exist — the floor now measures nothing and passes silently"
    )


def test_global_floor_is_recursive() -> None:
    """The global floor is the backstop for anything the per-package floors
    miss, so it above all must not be scoped away."""
    global_floor = cast("_FloorLike", _checker.GLOBAL_FLOOR)
    assert global_floor.include.endswith(_RECURSIVE_GLOB)


def test_floor_labels_are_unique() -> None:
    """Two floors sharing a label makes a gate failure ambiguous to read."""
    labels = [f.label for f in _all_floors()]
    assert len(labels) == len(set(labels)), sorted(labels)
