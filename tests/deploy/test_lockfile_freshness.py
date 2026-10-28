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

The lock is generated ``--strip-extras`` (see its header), so
``uvicorn[standard]`` resolves to ``uvicorn`` plus its closure. Comparison is
therefore on the PEP 503-normalised *base* name.

The ``cloud`` extra is in scope (spec-0038). The production image installs
``mangomas[cloud]`` (``Dockerfile`` ``ARG MANGOMAS_EXTRAS``) through the same
``-c``, so a cloud SDK the lock does not pin floats exactly like a base
dependency would — and ``make pip-audit`` would never see it. ``cloud`` is a
meta-extra (``mangomas[vertex,postgres,gcp]``), so it is resolved through
pyproject's own extras table rather than restated here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tests.constants.deploy import CLOUD_EXTRA, IMAGE_EXTRAS_BUILD_ARG, LINUX_ONLY_LOCK_CANARY
from tests.deploy import _dockerfile, _makefile

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_LOCKFILE = _REPO_ROOT / _makefile.variable("RUNTIME_LOCKFILE")
_LOCKFILE_HEADER = _REPO_ROOT / _makefile.variable("LOCKFILE_HEADER")

#: ``mangomas[vertex,postgres]`` inside an extra — a self-reference whose
#: bracketed extras are expanded recursively into their own requirements.
_SELF_EXTRA_PATTERN = re.compile(r"^\s*mangomas\s*\[(?P<extras>[^\]]+)\]\s*$")

#: A requirement's leading distribution name, before any extras, specifier,
#: environment marker or URL. Mirrors PEP 508's ``name`` production loosely
#: enough for this repo's hand-written ``[project].dependencies``.
_NAME_PATTERN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

#: Vacuity floor. Zero parsed runtime names would make the membership assertion
#: below pass over an empty set — the silent-pass shape this repo has hit before
#: (see ``.claude/skills/mango-mutation-proof/SKILL.md``). Kept deliberately
#: below the current count so an ordinary dependency removal does not trip it.
_MIN_RUNTIME_DEPENDENCIES = 5
#: Same vacuity floor for the ``cloud`` meta-extra (vertex + postgres + gcp
#: name five distributions today).
_MIN_CLOUD_DEPENDENCIES = 3


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


def _extra_names(table: dict[str, list[str]], extra: str, seen: set[str]) -> set[str]:
    """Expand *extra* through any ``mangomas[...]`` self-references it contains."""
    if extra in seen:
        return set()
    seen.add(extra)
    assert extra in table, f"pyproject declares no extra named {extra!r}"
    names: set[str] = set()
    for requirement in table[extra]:
        self_ref = _SELF_EXTRA_PATTERN.match(requirement)
        if self_ref is not None:
            for nested in self_ref.group("extras").split(","):
                names |= _extra_names(table, nested.strip(), seen)
            continue
        name = _requirement_name(requirement)
        if name is not None:
            names.add(name)
    return names


def extra_dependencies(extra: str, *, text: str | None = None) -> set[str]:
    """Return the normalised names *extra* installs, self-references expanded.

    ``cloud = ["mangomas[vertex,postgres,gcp]"]`` therefore yields the union of
    those three extras' requirements, which is what ``pip install
    mangomas[cloud]`` resolves and what the lock must pin.
    """
    raw = _PYPROJECT.read_text(encoding="utf-8") if text is None else text
    table: dict[str, list[str]] = tomllib.loads(raw)["project"]["optional-dependencies"]
    return _extra_names(table, extra, set())


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

    When this fails the lock is stale: regenerate it with ``make lock`` (the
    command in its own header), never by hand-editing.
    """
    missing = missing_from_lockfile()
    assert missing == set(), (
        f"{sorted(missing)} declared in {_PYPROJECT.name} but not pinned in "
        f"{_LOCKFILE.name}. The runtime image installs the lock as a constraints "
        f"file, and constraints pin without adding — so these float while "
        f"everything else is reproducible. Regenerate the lock."
    )


def test_cloud_extra_resolves_to_real_distributions() -> None:
    """Guard the denominator of the cloud check: the meta-extra must expand."""
    names = extra_dependencies(CLOUD_EXTRA)
    assert len(names) >= _MIN_CLOUD_DEPENDENCIES, (
        f"{CLOUD_EXTRA!r} expanded to only {sorted(names)} — self-reference "
        f"expansion broken, or the meta-extra emptied?"
    )
    # A self-reference must never survive as a name of its own.
    assert "mangomas" not in names


def test_cloud_extra_distributions_are_pinned_in_the_lockfile() -> None:
    """Every distribution ``mangomas[cloud]`` installs is pinned in the lock.

    The image installs the cloud extras through ``-c requirements.lock``
    (spec-0038). Before this contract the lock pinned the base closure only,
    so the Vertex, Cloud SQL and Secret Manager SDKs floated in production and
    ``make pip-audit`` never audited them. ``make lock`` compiles
    ``--extra cloud`` (``LOCK_EXTRAS``) precisely so this holds.
    """
    missing = extra_dependencies(CLOUD_EXTRA) - locked_distributions()
    assert missing == set(), (
        f"{sorted(missing)} are installed by mangomas[{CLOUD_EXTRA}] but not "
        f"pinned in {_LOCKFILE.name}; the production image installs that extra "
        f"through the lock, so these float. Run `make lock`."
    )


def test_lock_extras_cover_the_image_default_extras() -> None:
    """``make lock`` compiles the extras the Dockerfile installs by default.

    Two independent names for the same set would drift silently; this binds
    the Makefile's ``LOCK_EXTRAS`` to the image's ``ARG MANGOMAS_EXTRAS``.
    """
    image_extras = {
        extra.strip()
        for extra in _dockerfile.build_arg_default(IMAGE_EXTRAS_BUILD_ARG).split(",")
        if extra.strip()
    }
    lock_extras = set(_makefile.variable("LOCK_EXTRAS").split())
    assert image_extras <= lock_extras, (
        f"the image installs extras {sorted(image_extras)} but `make lock` compiles "
        f"{sorted(lock_extras)}; pins would be missing for the difference"
    )


def test_lockfile_starts_with_the_tracked_header() -> None:
    """The lock's preamble is byte-for-byte ``requirements.lock.header``.

    pip-compile's own header is suppressed (``--no-header``) because it records
    the compiling machine's command line and paths; the tracked header carries
    the regeneration contract instead, and the Makefile prepends it. A lock
    regenerated by hand with ``--no-header`` alone would drop the preamble —
    the foot-gun the previous in-file instructions had.
    """
    header = _LOCKFILE_HEADER.read_text(encoding="utf-8")
    assert header.strip(), f"{_LOCKFILE_HEADER.name} is empty"
    assert header.endswith("\n")
    lock = _LOCKFILE.read_text(encoding="utf-8")
    assert lock.startswith(header), (
        f"{_LOCKFILE.name} does not begin with {_LOCKFILE_HEADER.name}; "
        f"regenerate with `make lock`, which prepends it"
    )


def test_lockfile_never_names_the_project_itself() -> None:
    """The self-referential ``cloud`` extra must not leak a ``mangomas`` pin.

    pip-compile emits ``mangomas @ file://<path>`` (or ``mangomas==<version>``)
    for a self-referencing extra. As a *constraint* that line would pin the
    wheel being installed to a path that does not exist in the image, so
    ``make lock`` strips it; this keeps a hand-regenerated lock honest.
    """
    assert "mangomas" not in locked_distributions()
    offending = [
        line
        for line in _LOCKFILE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^mangomas(\s|==|@|\[|$)", line)
    ]
    assert offending == [], f"{_LOCKFILE.name} names the project itself: {offending}"


def test_lockfile_was_compiled_on_the_image_platform() -> None:
    """A Linux-only transitive pin must be present: it fingerprints the host.

    pip-compile resolves environment markers on the compiling host, not the
    image. A lock regenerated on Windows silently loses ``uvloop`` (excluded
    on ``win32`` by ``uvicorn[standard]``) — ``-c`` cannot hold a pin the
    resolver never emitted — and the Linux image then resolves it
    unconstrained, invisible to ``make pip-audit -r``. The Makefile guard
    refuses such hosts; this catches a lock that bypassed the guard.
    """
    assert LINUX_ONLY_LOCK_CANARY in locked_distributions(), (
        f"{_LOCKFILE.name} does not pin {LINUX_ONLY_LOCK_CANARY!r}; it was compiled "
        f"on a non-Linux host. Run `make lock` on Linux + Python 3.11 (a container "
        f"or WSL on Windows)."
    )


# ── mutation proofs (mango-mutation-proof, two-sided) ─────────────────────────


def test_a_cloud_sdk_dropped_from_the_lock_is_detected_as_missing() -> None:
    """Removing one cloud pin from the lock text must surface that name.

    Discriminates the cloud contract from the base one: ``asyncpg`` is a
    ``cloud`` (postgres) requirement and *not* a ``[project]`` dependency, so
    only ``extra_dependencies`` can report it.
    """
    real = _LOCKFILE.read_text(encoding="utf-8")
    mutated = "\n".join(line for line in real.splitlines() if not line.startswith("asyncpg=="))
    assert mutated != real, "asyncpg is no longer pinned — fixture needs updating"
    assert "asyncpg" not in runtime_dependencies(), "fixture no longer discriminates"
    missing = extra_dependencies(CLOUD_EXTRA) - locked_distributions(text=mutated)
    assert missing == {"asyncpg"}


def test_an_unknown_extra_is_rejected_not_empty() -> None:
    """A typo in the extra name must fail loudly, never resolve to ``set()``."""
    with pytest.raises(AssertionError, match="no extra named"):
        extra_dependencies("definitely-not-an-extra")


def test_self_reference_expansion_terminates_on_a_cycle() -> None:
    """A pyproject whose extras reference each other must not recurse forever."""
    cyclic = (
        "[project]\nname = 'x'\ndependencies = []\n"
        "[project.optional-dependencies]\n"
        "a = ['mangomas[b]', 'alpha>=1']\nb = ['mangomas[a]', 'beta>=1']\n"
    )
    assert extra_dependencies("a", text=cyclic) == {"alpha", "beta"}


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
