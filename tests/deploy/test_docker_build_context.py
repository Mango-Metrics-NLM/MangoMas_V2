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
from pathlib import Path

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


def _ignore_patterns() -> list[str]:
    """Return the effective (non-comment, non-negated) ``.dockerignore`` patterns."""
    patterns: list[str] = []
    for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line)
    return patterns


def _is_excluded(source: str, patterns: list[str]) -> str | None:
    """Return the pattern excluding *source*, or ``None`` when it survives filtering."""
    candidate = source.rstrip("/")
    for pattern in patterns:
        normalised = pattern.rstrip("/")
        if candidate == normalised or candidate.startswith(f"{normalised}/"):
            return pattern
    return None


def test_dockerfile_declares_copy_instructions() -> None:
    """Guard the parser itself — a silent empty parse would vacuously pass."""
    assert _copy_sources(), "no COPY sources parsed from the Dockerfile"


@pytest.mark.parametrize("source", _copy_sources())
def test_copied_path_is_not_excluded_by_dockerignore(source: str) -> None:
    excluded_by = _is_excluded(source, _ignore_patterns())
    assert excluded_by is None, (
        f"Dockerfile copies {source!r}, but .dockerignore excludes it via "
        f"{excluded_by!r}; `docker build .` would fail at that COPY."
    )


def test_readme_is_in_build_context() -> None:
    """``pyproject.toml`` sets ``readme = 'README.md'``, so the wheel build needs it."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'readme = "README.md"' in pyproject
    assert _is_excluded("README.md", _ignore_patterns()) is None
