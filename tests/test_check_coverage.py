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

import pytest

from tests._script_loader import load_script_module

_REPO_ROOT = Path(__file__).resolve().parents[1]
_checker = load_script_module("check_coverage.py")

# A floor whose `include` ends in this targets one specific module, so a
# recursive glob would be meaningless.
_SINGLE_MODULE_SUFFIX = ".py"
_RECURSIVE_GLOB = "**/*.py"
_FLAT_GLOB = "*.py"


def _directory_floors() -> list[object]:
    """Return floors that scope a directory rather than a single module."""
    return [f for f in _checker.FLOORS if f.include.endswith(_FLAT_GLOB)]


def test_floors_are_declared() -> None:
    """Guard: an empty FLOORS list would make every assertion below vacuous."""
    assert _checker.FLOORS
    assert _directory_floors()


@pytest.mark.parametrize(
    "floor", _directory_floors(), ids=lambda f: str(f.label)
)
def test_directory_floors_use_a_recursive_glob(floor: object) -> None:
    """A flat `*.py` silently stops measuring a new subpackage — and passes.

    `coverage report --include` treats `**` as "zero or more directories", so
    the recursive form is a strict superset: it matches every top-level file a
    flat glob does, plus anything nested. There is no case where the flat form
    is the correct choice for a directory.
    """
    include = str(getattr(floor, "include"))
    assert include.endswith(_RECURSIVE_GLOB), (
        f"floor {getattr(floor, 'label')!r} uses the non-recursive {include!r}; "
        f"use '**/*.py' so a future subpackage is still measured"
    )


@pytest.mark.parametrize("floor", _checker.FLOORS, ids=lambda f: str(f.label))
def test_every_floor_target_exists_on_disk(floor: object) -> None:
    """A floor pointing at a moved or renamed path matches nothing and passes.

    This is the same fail-open shape as the flat glob, reached a different way:
    spec-0015 turns `config.py` and `telemetry.py` into packages, at which
    point their single-module floors stop resolving.
    """
    include = str(getattr(floor, "include"))
    root = include.split("*", 1)[0].rstrip("/")
    assert (_REPO_ROOT / root).exists(), (
        f"floor {getattr(floor, 'label')!r} targets {include!r}, but {root!r} "
        f"does not exist — the floor now measures nothing and passes silently"
    )


def test_global_floor_is_recursive() -> None:
    """The global floor is the backstop for anything the per-package floors
    miss, so it above all must not be scoped away."""
    assert _checker.GLOBAL_FLOOR.include.endswith(_RECURSIVE_GLOB)


def test_floor_labels_are_unique() -> None:
    """Two floors sharing a label makes a gate failure ambiguous to read."""
    labels = [str(getattr(f, "label")) for f in _checker.FLOORS]
    assert len(labels) == len(set(labels)), sorted(labels)
