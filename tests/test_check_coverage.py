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

import re
import tomllib
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


# ── Exclusion patterns ────────────────────────────────────────────────────────
#
# `exclude_lines` is the third fail-open shape in this file, and the one that
# bit hardest. A pattern that over-matches does not report anything: coverage
# simply stops counting the lines it swallowed, and the file's percentage goes
# *up*. `"\\.\\.\\."` — meant for Protocol stub bodies — matched every
# `typer.Argument(..., help="…")` in a CLI command signature, and because
# coverage excludes the whole block when the excluded line belongs to a `def`
# header, entire command bodies disappeared. `cli/commands/rag.py` reported 16
# statements where coverage's own parser sees 53, and two untested `--verbose`
# branches sat inside the invisible region while the file reported 100%.
#
# The guard is deliberately **semantic**: it matches the configured patterns
# against real source lines rather than asserting the patterns' shape. A shape
# check (`assert "\\.\\.\\." not in patterns`) passes for any differently-worded
# regex with the same defect.

# Real lines, copied from `src/`, that must stay measured.
_MUST_STAY_MEASURED = (
    '    path: str = typer.Argument(..., help="File or directory of *.txt / *.md to ingest"),',
    '    message: str = typer.Argument(..., help="User message"),',
    "    definition: str | None = typer.Option(None, ...),",
    "        results = await retriever.search(text, top_k=top_k)",
)

# The Protocol stub bodies the patterns exist for. `src/` uses both forms — an
# ellipsis on its own line (30 sites) and one inline after the signature (3, in
# `secrets/provider.py`, `eval/scorers/embedding.py`,
# `adapters/embeddings/_shared.py`). Asserted positively so nobody "fixes" an
# over-match by deleting a pattern: dropping the inline one alone breaks the
# `secrets` package's 100% floor, which is how the second form was found.
_MUST_STAY_EXCLUDED = (
    "        ...",
    "    ...",
    "    def get(self, name: str) -> str | None: ...",
    "    async def embed(self, text: str) -> list[float]: ...",
)


def _exclude_patterns() -> list[str]:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["coverage"]["report"]["exclude_lines"]
    assert patterns, "exclude_lines is empty — this guard would be vacuous"
    return cast("list[str]", patterns)


@pytest.mark.parametrize("line", _MUST_STAY_MEASURED)
def test_no_exclude_pattern_swallows_real_code(line: str) -> None:
    """Executable code must not match any exclusion pattern."""
    offenders = [p for p in _exclude_patterns() if re.search(p, line)]
    assert offenders == [], (
        f"exclude_lines pattern(s) {offenders} match real source:\n  {line}\n"
        f"Coverage will drop this line — and, if it sits in a `def` header, the "
        f"whole function body — while the file's percentage rises."
    )


@pytest.mark.parametrize("line", _MUST_STAY_EXCLUDED)
def test_protocol_stub_bodies_are_still_excluded(line: str) -> None:
    """The pattern must keep doing the job it was added for."""
    assert any(re.search(p, line) for p in _exclude_patterns()), (
        f"no exclude_lines pattern matches a bare ellipsis stub:\n  {line}\n"
        f"Every Protocol `base.py` body would now count as uncovered."
    )
