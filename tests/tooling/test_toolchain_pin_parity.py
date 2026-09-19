"""The exact-pinned lint/type toolchain stays in lockstep with pre-commit.

``pyproject.toml`` pins ``ruff`` and ``mypy`` exactly and its comments claim
they are "kept in lockstep with the ruff-pre-commit rev in
``.pre-commit-config.yaml``". Nothing enforced that claim, so the two could
drift silently — and they *would* have: a Dependabot PR bumping the pip pin
is green today, because the pip ecosystem never touches
``.pre-commit-config.yaml``. Merging one alone leaves contributors running
pre-commit on a different ruff than CI runs, which shows up as a formatting
diff nobody can reproduce.

This is the spec-0022 R15 pattern — a constraint written as a mechanism
survives agent turnover, a constraint written as a comment does not.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import NamedTuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PRECOMMIT = _REPO_ROOT / ".pre-commit-config.yaml"


class ToolPin(NamedTuple):
    """One exact-pinned tool and the pre-commit repo that must match it."""

    distribution: str
    precommit_repo: str


#: Every tool pinned exactly in ``[project.optional-dependencies].dev`` that also
#: runs as a pre-commit hook. Adding a tool here is how the contract grows — the
#: test body carries no tool names.
TOOL_PINS: tuple[ToolPin, ...] = (
    ToolPin("ruff", "https://github.com/astral-sh/ruff-pre-commit"),
    ToolPin("mypy", "https://github.com/pre-commit/mirrors-mypy"),
)


def pyproject_exact_pin(distribution: str, *, text: str | None = None) -> str | None:
    """Return the exact-pinned version of *distribution*, or ``None``.

    Reads the parsed ``dev`` extra rather than regexing the file, so a
    requirement written with extras or whitespace still resolves.
    """
    raw = _PYPROJECT.read_text(encoding="utf-8") if text is None else text
    data = tomllib.loads(raw)
    dev: list[str] = data["project"]["optional-dependencies"]["dev"]
    for requirement in dev:
        match = re.fullmatch(rf"\s*{re.escape(distribution)}\s*==\s*([^\s;]+)\s*", requirement)
        if match:
            return match.group(1)
    return None


def precommit_rev(repo_url: str, *, text: str | None = None) -> str | None:
    """Return the ``rev:`` for *repo_url*, leading ``v`` stripped, or ``None``.

    Deliberately a small line scan rather than a YAML dependency: this test must
    keep working in the stdlib-only situations the harness scripts also target,
    and the contract is "this repo's rev", not a full loader.
    """
    raw = _PRECOMMIT.read_text(encoding="utf-8") if text is None else text
    pending = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- repo:"):
            pending = stripped.split(":", 1)[1].strip() == repo_url
            continue
        if pending and stripped.startswith("rev:"):
            return stripped.split(":", 1)[1].strip().lstrip("v")
    return None


@pytest.mark.parametrize("pin", TOOL_PINS, ids=lambda p: p.distribution)
def test_pyproject_pin_matches_precommit_rev(pin: ToolPin) -> None:
    """The pip pin and the pre-commit rev name the same version."""
    pinned = pyproject_exact_pin(pin.distribution)
    rev = precommit_rev(pin.precommit_repo)
    assert pinned is not None, f"{pin.distribution} is not exact-pinned in the dev extra"
    assert rev is not None, f"{pin.precommit_repo} has no rev in {_PRECOMMIT.name}"
    assert pinned == rev, (
        f"{pin.distribution} desync: pyproject pins {pinned!r} but "
        f"{_PRECOMMIT.name} pins {rev!r}. Bump both together — a pip-only "
        f"Dependabot PR will not touch the pre-commit rev."
    )


@pytest.mark.parametrize("pin", TOOL_PINS, ids=lambda p: p.distribution)
def test_parity_check_detects_a_desync(pin: ToolPin) -> None:
    """Prove the guard fires (mango-mutation-proof): desync it and it must differ.

    Without this the assertion above could pass vacuously — for instance if both
    readers returned ``None`` and a future edit relaxed the not-None guards.
    """
    real = precommit_rev(pin.precommit_repo)
    assert real is not None
    mutated = _PRECOMMIT.read_text(encoding="utf-8").replace(
        f"rev: v{real}", "rev: v0.0.0-desynced", 1
    )
    assert precommit_rev(pin.precommit_repo, text=mutated) != pyproject_exact_pin(pin.distribution)


def test_unknown_distribution_reads_as_absent() -> None:
    """A tool not pinned in the dev extra yields ``None``, not a false match."""
    assert pyproject_exact_pin("definitely-not-a-real-distribution") is None


def test_unknown_precommit_repo_reads_as_absent() -> None:
    assert precommit_rev("https://example.invalid/not-a-repo") is None


# ── hook additional_dependencies parity (analysis §N1) ────────────────────────
# The pins above cover the *tools*. The mypy hook additionally carries an
# ``additional_dependencies`` list so mypy can resolve ``src/``'s imports, and
# its own comment claims that list "covers exactly the runtime deps". Nothing
# checked that: neither this module's ``TOOL_PINS`` (tool revs only) nor
# ``tests/tooling/test_precommit_parity.py`` (local-hook mirrors, validate-config
# files, lint-imports invocation) reads ``additional_dependencies`` at all.
#
# Two rules, because the entries are two different kinds of thing:
#
#   * A **direct** entry — one pyproject also declares in ``[project]
#     .dependencies`` — must carry the *identical* specifier. Anything else means
#     the hook typechecks ``src/`` against a range the application does not
#     declare, and the drift is invisible until a contributor sees a type error
#     nobody can reproduce.
#   * A **transitive-only** entry (``starlette`` today, reached via ``fastapi``)
#     has no pyproject specifier to match, so the contract is that its floor
#     **admits the locked version** — the version the runtime image actually
#     installs, per ``requirements.lock`` and ``Dockerfile``'s ``-c``. A floor
#     raised above what ships is the defect this catches.
#
# Both rules hold on the tree today; they are unenforced, not violated. The
# mutation proofs below are therefore what establish that either can fail.

_LOCKFILE = _REPO_ROOT / "requirements.lock"

#: Pre-commit hooks whose ``additional_dependencies`` mirror the runtime
#: dependency surface. The contract grows by adding a repo here — the test
#: bodies carry no distribution names, same convention as ``TOOL_PINS``.
DEPENDENCY_MIRRORING_HOOKS: tuple[str, ...] = ("https://github.com/pre-commit/mirrors-mypy",)

#: Vacuity floor: a scan that returns nothing would satisfy every ``all(...)``
#: below. Deliberately under the current count so removing one entry is not a
#: failure, but an emptied list is.
_MIN_HOOK_DEPENDENCIES = 4

_REQUIREMENT_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$")


def normalize_distribution(name: str) -> str:
    """Return *name* normalised per PEP 503 — runs of ``-_.`` to ``-``, folded."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _split_requirement(requirement: str) -> tuple[str, str] | None:
    """Return ``(normalised_name, specifier)`` for *requirement*, or ``None``."""
    match = _REQUIREMENT_PATTERN.match(requirement.strip())
    if match is None:
        return None
    return normalize_distribution(match.group(1)), match.group(2).strip()


def hook_additional_dependencies(repo_url: str, *, text: str | None = None) -> dict[str, str]:
    """Return ``{distribution: specifier}`` from *repo_url*'s hook, or ``{}``.

    A scoped line scan rather than a YAML dependency, for the reason
    :func:`precommit_rev` gives: this module's readers must keep working in the
    stdlib-only situations the harness scripts also target. The scan is anchored
    to the repo block so a second hook growing its own list cannot bleed in.
    """
    raw = _PRECOMMIT.read_text(encoding="utf-8") if text is None else text
    in_repo = False
    collecting = False
    found: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- repo:"):
            in_repo = stripped.split(":", 1)[1].strip() == repo_url
            collecting = False
            continue
        if not in_repo:
            continue
        if stripped.startswith("additional_dependencies:"):
            collecting = True
            continue
        if collecting:
            if not stripped.startswith("- "):
                # Any non-list line ends the block (next key, next hook, blank).
                if stripped and not stripped.startswith("#"):
                    collecting = False
                continue
            parsed = _split_requirement(stripped[2:])
            if parsed is not None:
                found[parsed[0]] = parsed[1]
    return found


def runtime_specifiers(*, text: str | None = None) -> dict[str, str]:
    """Return ``{distribution: specifier}`` from ``[project].dependencies``.

    Extras are stripped: ``uvicorn[standard]>=0.30`` yields ``(uvicorn, >=0.30)``,
    because the hook lists base names and the lock is ``--strip-extras``.
    """
    raw = _PYPROJECT.read_text(encoding="utf-8") if text is None else text
    declared: list[str] = tomllib.loads(raw)["project"]["dependencies"]
    resolved: dict[str, str] = {}
    for requirement in declared:
        without_extras = re.sub(r"\[[^\]]*\]", "", requirement)
        parsed = _split_requirement(without_extras)
        if parsed is not None:
            resolved[parsed[0]] = parsed[1]
    return resolved


def locked_versions(*, text: str | None = None) -> dict[str, str]:
    """Return ``{distribution: version}`` for every ``==`` pin in the lockfile."""
    raw = _LOCKFILE.read_text(encoding="utf-8") if text is None else text
    pinned: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "==" not in stripped:
            continue
        name, _, version = stripped.partition("==")
        pinned[normalize_distribution(name)] = version.strip()
    return pinned


def hook_dependency_disagreements(
    repo_url: str,
    *,
    hook_text: str | None = None,
    pyproject_text: str | None = None,
    lockfile_text: str | None = None,
) -> list[str]:
    """Return human-readable disagreements for *repo_url*'s hook; ``[]`` when clean.

    Pure over its inputs so both mutation proofs can drive it with modified text
    instead of editing tracked files.
    """
    from packaging.specifiers import SpecifierSet  # noqa: PLC0415 — test-only dep
    from packaging.version import Version  # noqa: PLC0415

    hooked = hook_additional_dependencies(repo_url, text=hook_text)
    runtime = runtime_specifiers(text=pyproject_text)
    locked = locked_versions(text=lockfile_text)
    problems: list[str] = []
    for name, specifier in sorted(hooked.items()):
        if name in runtime:
            if specifier != runtime[name]:
                problems.append(
                    f"{name}: hook says {specifier!r}, pyproject declares "
                    f"{runtime[name]!r} — the hook would typecheck src/ against a "
                    f"range the application does not declare"
                )
            continue
        version = locked.get(name)
        if version is None:
            problems.append(
                f"{name}: transitive-only hook entry with no {_LOCKFILE.name} pin, "
                f"so nothing establishes which version ships"
            )
        elif specifier and Version(version) not in SpecifierSet(specifier):
            problems.append(
                f"{name}: hook floor {specifier!r} excludes the locked {version} "
                f"that the runtime image installs"
            )
    return problems


@pytest.mark.parametrize("repo_url", DEPENDENCY_MIRRORING_HOOKS)
def test_hook_dependencies_are_actually_parsed(repo_url: str) -> None:
    """Guard the denominator: an empty scan makes every rule below vacuous."""
    hooked = hook_additional_dependencies(repo_url)
    assert len(hooked) >= _MIN_HOOK_DEPENDENCIES, (
        f"only {len(hooked)} additional_dependencies parsed for {repo_url} — "
        f"scan broken, or the hook's list was emptied?"
    )


@pytest.mark.parametrize("repo_url", DEPENDENCY_MIRRORING_HOOKS)
def test_hook_dependencies_agree_with_runtime_ranges(repo_url: str) -> None:
    """Direct entries match pyproject exactly; transitive ones admit the lock."""
    problems = hook_dependency_disagreements(repo_url)
    assert problems == [], (
        "pre-commit hook dependencies disagree with the runtime surface:\n  "
        + "\n  ".join(problems)
        + f"\n\nBump {_PRECOMMIT.name} and {_PYPROJECT.name} together — a "
        f"pre-commit-only Dependabot PR will not touch pyproject."
    )


@pytest.mark.parametrize("repo_url", DEPENDENCY_MIRRORING_HOOKS)
def test_a_raised_hook_floor_on_a_direct_dependency_is_detected(repo_url: str) -> None:
    """Prove the direct-entry rule fires (mango-mutation-proof).

    The discriminating mutation is the real one from the queue: a Dependabot
    ``pre_commit`` PR raising one floor while pyproject stays put. Pick the
    subject from the parsed data rather than naming a distribution, so the proof
    survives the hook's list changing.
    """
    direct = sorted(set(hook_additional_dependencies(repo_url)) & set(runtime_specifiers()))
    assert direct, "no direct entries to mutate"
    name = direct[0]
    real = f"{name}{hook_additional_dependencies(repo_url)[name]}"
    mutated = _PRECOMMIT.read_text(encoding="utf-8").replace(real, f"{name}>=99999.0", 1)
    assert mutated != _PRECOMMIT.read_text(encoding="utf-8"), f"{real!r} not found verbatim"
    problems = hook_dependency_disagreements(repo_url, hook_text=mutated)
    assert any(name in problem for problem in problems), problems


@pytest.mark.parametrize("repo_url", DEPENDENCY_MIRRORING_HOOKS)
def test_a_transitive_floor_above_the_locked_version_is_detected(repo_url: str) -> None:
    """Prove the transitive rule fires — the other side of the two-sided proof.

    Without this the transitive branch could pass vacuously: it is the branch
    with no pyproject specifier to compare, so an implementation that simply
    skipped unknown names would look identical on a healthy tree.
    """
    hooked = hook_additional_dependencies(repo_url)
    transitive = sorted(set(hooked) - set(runtime_specifiers()))
    if not transitive:
        pytest.skip("no transitive-only hook entries to mutate")
    name = transitive[0]
    real = f"{name}{hooked[name]}"
    mutated = _PRECOMMIT.read_text(encoding="utf-8").replace(real, f"{name}>=99999.0", 1)
    problems = hook_dependency_disagreements(repo_url, hook_text=mutated)
    assert any("excludes the locked" in problem for problem in problems), problems


@pytest.mark.parametrize("repo_url", DEPENDENCY_MIRRORING_HOOKS)
def test_a_transitive_entry_absent_from_the_lockfile_is_detected(repo_url: str) -> None:
    """A transitive entry the lock does not pin has nothing establishing its version."""
    hooked = hook_additional_dependencies(repo_url)
    transitive = sorted(set(hooked) - set(runtime_specifiers()))
    if not transitive:
        pytest.skip("no transitive-only hook entries")
    problems = hook_dependency_disagreements(repo_url, lockfile_text="# emptied\n")
    assert any("no requirements.lock pin" in problem for problem in problems), problems


def test_unknown_hook_repo_reads_as_absent() -> None:
    """A repo with no such hook yields ``{}``, not a false match."""
    assert hook_additional_dependencies("https://example.invalid/not-a-repo") == {}


def test_extras_are_stripped_from_runtime_specifiers() -> None:
    """``uvicorn[standard]>=0.30`` must resolve as ``uvicorn``, ``>=0.30``.

    The hook lists base names and the lockfile is generated ``--strip-extras``,
    so leaving the extras attached would make every extras-carrying dependency
    read as transitive-only and silently skip the stricter rule.
    """
    resolved = runtime_specifiers()
    assert not any("[" in name for name in resolved), resolved
