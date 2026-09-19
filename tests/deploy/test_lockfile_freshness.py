"""``requirements.lock`` must stay in sync with pyproject's runtime dependencies.

The lockfile is what the **production image installs**: ``Dockerfile`` passes it
to ``pip install`` as a constraints file (``-c``). Constraints *pin* versions —
they never *add* dependencies — so a runtime dependency added to
``[project].dependencies`` without regenerating the lock simply floats, while
everything around it stays pinned. The build still succeeds, the image is still
produced, and nothing signals that one dependency stopped being reproducible.

``tests/deploy/test_docker_build_context.py`` already pins the two adjacent
properties: that the Dockerfile passes ``-c``, and that every lockfile line is an
exact ``==`` pin. Neither compares the lockfile's *contents* to pyproject, which
is the gap this module closes.

Names, never specifiers
-----------------------
The lock pins the full transitive closure; pyproject states ``>=`` ranges. A
correctly generated lock therefore disagrees with pyproject on every version, so
asserting version agreement would fail the healthy case. The contract is
membership: every runtime distribution pyproject names must appear, pinned, in
the lock.

Extras are out of scope. The lock is generated ``--strip-extras`` (see its
header), so ``uvicorn[standard]`` resolves to ``uvicorn`` plus its closure.
Comparison is therefore on the PEP 503-normalised *base* name.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_LOCKFILE = _REPO_ROOT / "requirements.lock"

#: A requirement's leading distribution name, before any extras, specifier,
#: environment marker or URL. Mirrors PEP 508's ``name`` production loosely
#: enough for this repo's hand-written ``[project].dependencies``.
_NAME_PATTERN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

#: Vacuity floor. Zero parsed runtime names would make the membership assertion
#: below pass over an empty set — the silent-pass shape this repo has hit before
#: (see ``.claude/skills/mango-mutation-proof/SKILL.md``). Kept deliberately
#: below the current count so an ordinary dependency removal does not trip it.
_MIN_RUNTIME_DEPENDENCIES = 5


def normalize_distribution(name: str) -> str:
    """Return *name* normalised per PEP 503 — runs of ``-_.`` to ``-``, folded.

    ``opentelemetry_api``, ``OpenTelemetry-API`` and ``opentelemetry.api`` are
    one distribution; comparing raw strings would report a false desync.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str | None:
    """Return the normalised distribution name in *requirement*, or ``None``."""
    match = _NAME_PATTERN.match(requirement)
    return normalize_distribution(match.group(1)) if match else None


def runtime_dependencies(*, text: str | None = None) -> set[str]:
    """Return the normalised names in ``[project].dependencies``.

    Parsed via ``tomllib`` rather than regexed so a requirement carrying extras,
    a marker or unusual whitespace still resolves. *text* is injectable so the
    mutation proofs below can exercise a modified pyproject without touching disk.
    """
    raw = _PYPROJECT.read_text(encoding="utf-8") if text is None else text
    declared: list[str] = tomllib.loads(raw)["project"]["dependencies"]
    names = {_requirement_name(requirement) for requirement in declared}
    return {name for name in names if name is not None}


def locked_distributions(*, text: str | None = None) -> set[str]:
    """Return the normalised names pinned with ``==`` in the lockfile.

    Comment and continuation lines are skipped. Only ``==`` lines count: a line
    that lost its pin is not a lock, and ``test_lockfile_exists_and_actually_pins``
    in ``test_docker_build_context.py`` is what reports that separately.
    """
    raw = _LOCKFILE.read_text(encoding="utf-8") if text is None else text
    locked: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "==" not in stripped:
            continue
        name = _requirement_name(stripped)
        if name is not None:
            locked.add(name)
    return locked


def missing_from_lockfile(
    *, pyproject_text: str | None = None, lockfile_text: str | None = None
) -> set[str]:
    """Return runtime distributions pyproject declares that the lock omits."""
    return runtime_dependencies(text=pyproject_text) - locked_distributions(text=lockfile_text)


# ── the contract ──────────────────────────────────────────────────────────────


def test_runtime_dependencies_are_actually_parsed() -> None:
    """Guard the denominator: an empty parse makes the next test vacuous."""
    declared = runtime_dependencies()
    assert len(declared) >= _MIN_RUNTIME_DEPENDENCIES, (
        f"only {len(declared)} runtime dependencies parsed from "
        f"{_PYPROJECT.name} — parser broken, not pyproject emptied?"
    )


def test_lockfile_pins_are_actually_parsed() -> None:
    """Guard the other side: an empty lock would satisfy a subset check trivially."""
    assert locked_distributions(), f"no == pins parsed from {_LOCKFILE.name}"


def test_every_runtime_distribution_is_pinned_in_the_lockfile() -> None:
    """Every ``[project].dependencies`` name appears, pinned, in the lockfile.

    When this fails the lock is stale: regenerate it with the command in its own
    header (``pip-compile --strip-extras --no-header --output-file=requirements.lock
    pyproject.toml``), never by hand-editing.
    """
    missing = missing_from_lockfile()
    assert missing == set(), (
        f"{sorted(missing)} declared in {_PYPROJECT.name} but not pinned in "
        f"{_LOCKFILE.name}. The runtime image installs the lock as a constraints "
        f"file, and constraints pin without adding — so these float while "
        f"everything else is reproducible. Regenerate the lock."
    )


# ── mutation proofs (mango-mutation-proof, two-sided) ─────────────────────────


def test_a_new_runtime_dependency_is_detected_as_missing() -> None:
    """Adding a runtime dependency without regenerating the lock must fail.

    The discriminating mutation: the guarded defect is "pyproject grew, lock did
    not". Without this the assertion above could pass vacuously — for instance if
    a future edit made ``runtime_dependencies`` return the empty set.
    """
    real = _PYPROJECT.read_text(encoding="utf-8")
    synthetic = "definitely-not-a-real-distribution>=1.0"
    mutated = real.replace("dependencies = [", f'dependencies = [\n    "{synthetic}",', 1)
    assert mutated != real, "pyproject's dependencies table moved — fixture needs updating"
    missing = missing_from_lockfile(pyproject_text=mutated)
    assert normalize_distribution("definitely-not-a-real-distribution") in missing


def test_an_unpinned_lockfile_entry_is_detected_as_missing() -> None:
    """The other direction: a lockfile line that lost its ``==`` stops counting."""
    locked = locked_distributions()
    assert locked, "no pins to mutate"
    real = _LOCKFILE.read_text(encoding="utf-8")
    # Degrade every pin to a floor; nothing should then read as locked.
    assert locked_distributions(text=real.replace("==", ">=")) == set()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("opentelemetry_api", "opentelemetry-api"),
        ("OpenTelemetry-API", "opentelemetry-api"),
        ("opentelemetry.api", "opentelemetry-api"),
        ("pydantic__settings", "pydantic-settings"),
        ("pydantic", "pydantic"),
    ],
)
def test_normalization_follows_pep_503(raw: str, expected: str) -> None:
    """Without this, ``opentelemetry-api`` vs ``opentelemetry_api`` reads as a desync."""
    assert normalize_distribution(raw) == expected


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("uvicorn[standard]>=0.30", "uvicorn"),
        ("fastapi>=0.115", "fastapi"),
        ("httpx == 0.28.1", "httpx"),
        ('pydantic>=2.7; python_version >= "3.11"', "pydantic"),
        ("# a comment", None),
        ("", None),
    ],
)
def test_requirement_name_extraction(requirement: str, expected: str | None) -> None:
    """Extras, specifiers, spacing and markers must not leak into the name."""
    assert _requirement_name(requirement) == expected
