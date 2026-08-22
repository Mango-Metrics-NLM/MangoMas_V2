"""Contract tests binding the ``Dockerfile`` to ``.dockerignore``.

``deploy.yml`` builds the production image with ``docker build .``. Any path a
``COPY`` instruction names must therefore survive ``.dockerignore`` filtering —
an excluded path is simply absent from the build context and the build fails at
that ``COPY``. This regression previously shipped undetected: ``pyproject.toml``
declares ``readme = "README.md"``, the builder stage copies it, and
``.dockerignore`` excluded it.

Both files are parsed at runtime rather than restated here, so the tests follow
the real manifests instead of a hard-coded snapshot of them.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_DOCKERIGNORE = _REPO_ROOT / ".dockerignore"

# ``COPY [--flag ...] <src>... <dest>`` — capture the argument span, minus flags.
_COPY_RE = re.compile(r"^\s*COPY\s+(?P<args>.+)$", re.IGNORECASE)


def _copy_sources() -> list[str]:
    """Return every build-context source path named by a ``COPY`` in the Dockerfile."""
    sources: list[str] = []
    for line in _DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = _COPY_RE.match(line)
        if match is None:
            continue
        args = [a for a in match.group("args").split() if not a.startswith("--")]
        # A ``COPY --from=<stage>`` reads from an earlier stage, not the build
        # context, so .dockerignore does not apply to it.
        if "--from=" in match.group("args"):
            continue
        # Last token is the destination; everything before it is a source.
        sources.extend(args[:-1])
    return sources


def _ignore_rules() -> list[tuple[str, bool]]:
    """Return ``(pattern, is_negation)`` pairs in file order.

    Order is preserved because Docker resolves a path against *every* rule and
    the **last** match wins — a later ``!pattern`` re-includes something an
    earlier rule excluded.
    """
    rules: list[tuple[str, bool]] = []
    for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        if line:
            rules.append((line.rstrip("/"), negated))
    return rules


def _exclusion_rule(source: str, rules: list[tuple[str, bool]]) -> str | None:
    """Return the pattern excluding *source*, or ``None`` when it survives filtering.

    Follows Docker's `.dockerignore` semantics rather than approximating them:
    patterns are globs (``fnmatch``), a rule covering a directory also covers
    everything beneath it, and every rule is evaluated in order so the last
    match decides. Anything less can both miss a glob exclusion and report a
    path as excluded that a later negation puts back.
    """
    candidate = source.rstrip("/").removeprefix("./")
    # Test the path and each ancestor, since ignoring a directory ignores its
    # contents (`docs/` excludes `docs/adr/0001.md`).
    paths = [candidate]
    paths.extend(str(parent) for parent in PurePosixPath(candidate).parents if str(parent) != ".")

    matched: str | None = None
    for pattern, negated in rules:
        if any(fnmatch(path, pattern) for path in paths):
            matched = None if negated else pattern
    return matched


def test_dockerfile_declares_copy_instructions() -> None:
    """Guard the parser itself — a silent empty parse would vacuously pass."""
    assert _copy_sources(), "no COPY sources parsed from the Dockerfile"


@pytest.mark.parametrize("source", _copy_sources())
def test_copied_path_is_not_excluded_by_dockerignore(source: str) -> None:
    excluded_by = _exclusion_rule(source, _ignore_rules())
    assert excluded_by is None, (
        f"Dockerfile copies {source!r}, but .dockerignore excludes it via "
        f"{excluded_by!r}; `docker build .` would fail at that COPY."
    )


def test_readme_is_in_build_context() -> None:
    """``pyproject.toml`` sets ``readme = 'README.md'``, so the wheel build needs it."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'readme = "README.md"' in pyproject
    assert _exclusion_rule("README.md", _ignore_rules()) is None


# ── lockfile wiring (supply-chain baseline, roadmap item 0.3) ─────────────────

_LOCKFILE = _REPO_ROOT / "requirements.lock"
# Vacuity floor with headroom (deliberately below the current pin count): a
# lockfile emptied to a header would satisfy an existence check alone.
_MIN_LOCKED_PINS = 10


def test_runtime_wheel_install_is_constrained_by_the_lockfile() -> None:
    """The runtime `pip install` must pass requirements.lock as constraints.

    Without ``-c``, the wheel install resolves pyproject's ``>=`` ranges at
    build time — the build still *succeeds*, so nothing but this test notices
    that image contents stopped being reproducible.
    """
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    assert "-c /tmp/requirements.lock" in dockerfile, (
        "runtime wheel install no longer passes requirements.lock as a constraints file"
    )


def test_lockfile_exists_and_actually_pins() -> None:
    """Every non-comment lockfile line is an exact ``==`` pin, and enough exist.

    A constraints file with ranges (or an emptied one) silently degrades the
    Dockerfile's ``-c`` back to floating resolution.
    """
    lines = [line.strip() for line in _LOCKFILE.read_text(encoding="utf-8").splitlines()]
    pins = [line for line in lines if line and not line.startswith("#")]
    assert len(pins) >= _MIN_LOCKED_PINS, f"only {len(pins)} pins — lockfile emptied?"
    unpinned = [pin for pin in pins if "==" not in pin]
    assert unpinned == []


# ── matcher semantics ─────────────────────────────────────────────────────────
# The guard above is only as trustworthy as its matcher, so pin the three
# Dockerfile behaviours an exact/prefix-only implementation silently gets wrong.


@pytest.mark.parametrize(
    ("rules", "source", "expected"),
    [
        # Globs must match, not be compared literally.
        ([("*.pyc", False)], "module.pyc", "*.pyc"),
        ([("*.pyc", False)], "module.py", None),
        ([(".env.*", False)], ".env.local", ".env.*"),
        # A directory rule covers everything beneath it.
        ([("docs", False)], "docs/adr/0001.md", "docs"),
        ([("docs", False)], "docsite/index.md", None),
        # Last match wins, so a later negation re-includes.
        ([(".env.*", False), (".env.example", True)], ".env.example", None),
        # ...and order matters: the same rules reversed keep it excluded.
        ([(".env.example", True), (".env.*", False)], ".env.example", ".env.*"),
        # An unmatched path survives.
        ([("tests", False), ("docs", False)], "pyproject.toml", None),
    ],
)
def test_exclusion_rule_follows_dockerignore_semantics(
    rules: list[tuple[str, bool]], source: str, expected: str | None
) -> None:
    assert _exclusion_rule(source, rules) == expected


def test_ignore_rules_preserve_negation_and_order() -> None:
    """Negations must be parsed, not skipped — dropping them breaks last-match-wins."""
    rules = _ignore_rules()
    assert rules, "no rules parsed from .dockerignore"
    assert any(negated for _, negated in rules), (
        ".dockerignore has a negation (!.env.example); the parser dropped it"
    )
    # Comments and blank lines never become rules.
    assert all(pattern and not pattern.startswith(("#", "!")) for pattern, _ in rules)
