r"""Contract tests for the per-package coverage gate.

`scripts/_checker.py` is the authoritative coverage gate, and its floor
globs have a **fail-open** failure mode: `coverage report --include=<glob>`
that matches fewer files than intended still prints a percentage and still
passes. Nothing reports that the glob stopped covering something.

That already happened once. `api/*.py` was flat when `api/` grew a `routes/`
subpackage, so the new routers went unmeasured while the gate stayed green.
`cli/` is next in line — spec-0015 decomposes `cli/main.py` into a command
package — so the rule is pinned here rather than left to review.

## The invariant this module owns (spec-0020 R2)

Four defects of one shape have been found in this repo's gates:

1. the flat `api/*.py` glob above;
2. a non-recursive `cli` glob, ahead of the spec-0015 split;
3. an unanchored `"\.\.\."` in `exclude_lines`, which matched
   `typer.Argument(...)` and dropped whole command bodies from measurement;
4. no guard that the floor list covers every package at all.

They share one signature: **a gate config that stops covering something and
reports success anyway.** A green gate cannot distinguish "we checked everything
and it passed" from "we checked less than you think and it passed." Defect 3 is
the sharpest — an over-matching exclusion makes the percentage go *up*, because
the lines it swallows are the untested ones, so the symptom looks like an
improvement.

Every guard for that class lives in this module, so the fifth instance has an
obvious home instead of being rediscovered. When you add one, add it here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Protocol, cast

import pytest

from tests._script_loader import load_script_module


class _FloorLike(Protocol):
    """The shape `_checker.Floor` exposes.

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


# ── Floor completeness (spec-0020 R1) ─────────────────────────────────────────
#
# The fourth instance of the class named in this module's docstring, and the one
# with the widest blast radius: the other three break a floor that exists, this
# one is about a floor that never gets written. Add a package to
# `src/mangomas/`, forget its floor, and only the 95% global applies — a package
# can sit at 60% indefinitely without any gate objecting.

# Entries that deliberately carry no floor of their own.
_FLOOR_EXEMPT = frozenset(
    {
        "__init__.py",  # re-export surface; measured by whichever floor imports it
        "__pycache__",
        "py.typed",  # PEP 561 marker, not code
    }
)


def _floor_roots() -> set[str]:
    """The path each floor anchors on, with the glob tail removed."""
    return {f.include.split("*", 1)[0].rstrip("/") for f in _all_floors()}


def _top_level_source_entries() -> list[str]:
    src = _REPO_ROOT / "src" / "mangomas"
    return sorted(
        p.name for p in src.iterdir() if p.name not in _FLOOR_EXEMPT and not p.name.startswith(".")
    )


def test_source_tree_is_non_empty() -> None:
    """Self-guard: an empty listing would make the completeness check vacuous —
    the same failure this module exists to catch, one level up."""
    assert _top_level_source_entries()


@pytest.mark.parametrize("entry", _top_level_source_entries())
def test_every_top_level_source_path_has_a_floor(entry: str) -> None:
    """Every package and module under `src/mangomas/` is named by some floor.

    Without this, a new package inherits only the 95% global backstop, and
    nothing says so. The global is an *average*: a small package at 40% moves it
    by a fraction of a point and the gate stays green, which is precisely the
    fail-open signature — less was checked than the reader believes.

    A floor is a deliberate statement about what a surface is worth. Adding one
    should be a decision, not something a contributor can skip by accident.
    """
    roots = _floor_roots()
    expected = f"src/mangomas/{entry}"
    assert expected in roots, (
        f"{expected!r} has no entry in scripts/_checker.py::FLOORS. "
        f"Add one (choose the floor deliberately — 100% for a small pure "
        f"module, 95% to match its siblings), or add it to _FLOOR_EXEMPT with "
        f"a reason."
    )


# ── The gate's own logic (spec-0023 R5) ──────────────────────────────────────
#
# `_check` and `main` were the least-covered code in `scripts/` (the module sat
# at 24%): the coverage gate itself was the one script no test exercised. A
# defect here — an inverted returncode test, a swallowed failure, a missing
# `sys.exit` — makes the *entire* per-package gate pass while measuring
# nothing, and by construction no coverage number would reveal it. These drive
# `_check`/`main` against a stubbed subprocess so both verdicts are proven.


class _FakeCompleted:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = "TOTAL 100 0 42%\n"
        self.stderr = ""


def _stub_run(returncode: int, calls: list[list[str]]) -> object:
    def _run(cmd: list[str], **_kwargs: object) -> _FakeCompleted:
        calls.append(cmd)
        return _FakeCompleted(returncode)

    return _run


def test_check_returns_true_when_coverage_command_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(_checker.subprocess, "run", _stub_run(0, calls))
    assert _checker._check(_checker.GLOBAL_FLOOR) is True
    # The floor must actually reach the coverage invocation — a gate that
    # forgets --fail-under passes unconditionally.
    assert f"--fail-under={_checker.GLOBAL_FLOOR.minimum}" in calls[0]
    assert f"--include={_checker.GLOBAL_FLOOR.include}" in calls[0]


def test_check_returns_false_when_coverage_command_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(_checker.subprocess, "run", _stub_run(2, calls))
    assert _checker._check(_checker.GLOBAL_FLOOR) is False
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert _checker.GLOBAL_FLOOR.include in out


def test_main_exits_nonzero_when_any_floor_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate must fail the build, not merely print a complaint."""
    monkeypatch.setattr(_checker, "_check", lambda _floor: False)
    with pytest.raises(SystemExit) as excinfo:
        _checker.main()
    assert excinfo.value.code == 1


def test_main_succeeds_when_every_floor_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_checker, "_check", lambda _floor: True)
    _checker.main()
    assert "All coverage floors met." in capsys.readouterr().out


def test_main_checks_every_declared_floor_plus_the_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gate that silently skips a floor is the fail-open shape this guards."""
    seen: list[str] = []

    def _record(floor: _FloorLike) -> bool:
        seen.append(floor.label)
        return True

    monkeypatch.setattr(_checker, "_check", _record)
    _checker.main()
    assert seen == [floor.label for floor in _checker.FLOORS] + [_checker.GLOBAL_FLOOR.label]


# ── docs/testing/regression.md ────────────────────────────────────────────────

_REGRESSION_DOC = _REPO_ROOT / "docs" / "testing" / "regression.md"
# `| `label` | 95% |` — a floor row in the doc's per-package table.
_DOC_FLOOR_ROW_RE = re.compile(r"^\|\s*`(?P<label>[a-z_]+)`\s*\|\s*(?P<floor>\d+)%")


def _documented_floors() -> dict[str, int]:
    return {
        m.group("label"): int(m.group("floor"))
        for line in _REGRESSION_DOC.read_text(encoding="utf-8").splitlines()
        if (m := _DOC_FLOOR_ROW_RE.match(line))
    }


def test_regression_doc_floor_table_matches_the_script() -> None:
    """`docs/testing/regression.md` must list the floors the gate enforces.

    The doc already says "if this table and that script ever disagree, the
    script wins and this table is the bug" — an honest disclaimer, and an
    admission that nothing checked it. It had drifted: six packages the gate
    enforces (`headers`, `entry_points`, `config`, `telemetry`, `metrics`,
    `harness`) were absent from the table, so a reader auditing coverage policy
    saw fourteen floors where twenty exist.

    Direction matters both ways. A missing row understates the policy; a row
    for a floor that no longer exists overstates it, and both send a reader
    looking for a gate that is not there.
    """
    declared = {floor.label: floor.minimum for floor in _all_floors()}
    documented = _documented_floors()

    assert documented, f"no floor rows parsed from {_REGRESSION_DOC.name} — table reformatted?"

    undocumented = sorted(set(declared) - set(documented))
    assert undocumented == [], f"enforced but undocumented floor(s): {undocumented}"

    phantom = sorted(set(documented) - set(declared))
    assert phantom == [], f"documented but not enforced: {phantom}"

    wrong = sorted(
        f"{label}: doc says {documented[label]}%, gate enforces {declared[label]}%"
        for label in declared
        if documented[label] != declared[label]
    )
    assert wrong == [], "floor value(s) disagree:\n  " + "\n  ".join(wrong)
