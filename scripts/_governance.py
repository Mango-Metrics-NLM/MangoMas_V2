"""Shared, stdlib-only governance primitives for scripts/ CI/hook tooling.

Both ``scripts/check_protected_paths.py`` (the CI gate) and
``scripts/lint_agent_frontmatter.py`` (the ``PreToolUse``/``PostToolUse``
hooks) read the same ``[tool.mangomas.governance]`` table and match the
same ``BREAKING-CHANGE`` marker convention against different text (commit
messages for the former, a staged diff for the latter's legacy pre-commit
path) — this module holds that shared logic once instead of twice.

It stays stdlib-only and outside ``src/mangomas/`` deliberately: the
``PreToolUse`` hook must run in an interpreter where ``pip install -e .``
has never happened. See ``src/mangomas/harness/governance.py``'s module
docstring for the mirror-image reasoning on the ``mangomas``-package side,
which keeps its own independent copy of this same logic for exactly that
reason — the two are not meant to converge.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

DEFAULT_PYPROJECT_PATH: Final[Path] = Path("pyproject.toml")


class GovernanceLoadError(Exception):
    """Raised when ``[tool.mangomas.governance]`` is missing, empty, or malformed."""


def load_governance(
    pyproject_path: Path = DEFAULT_PYPROJECT_PATH,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(protected_paths, marker_aliases)`` from *pyproject_path*.

    Raises :class:`GovernanceLoadError` on any read/parse/shape failure —
    callers decide whether to fail loudly (the CI gate) or fall back to
    safe defaults with a logged warning (the advisory hook).
    """
    try:
        raw = pyproject_path.read_bytes()
    except OSError as exc:
        raise GovernanceLoadError(f"cannot read {pyproject_path}: {exc}") from exc
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise GovernanceLoadError(f"malformed TOML in {pyproject_path}: {exc}") from exc
    try:
        governance = doc["tool"]["mangomas"]["governance"]
        protected_paths = frozenset(governance["protected_paths"])
        marker_aliases = frozenset(governance["breaking_change_marker_aliases"])
    except (KeyError, TypeError) as exc:
        raise GovernanceLoadError(
            f"[tool.mangomas.governance] missing or malformed key: {exc}"
        ) from exc
    if not protected_paths or not marker_aliases:
        raise GovernanceLoadError("[tool.mangomas.governance] tables must be non-empty")
    return protected_paths, marker_aliases


def find_breaking_change_marker(text: str, marker_aliases: frozenset[str]) -> str | None:
    """Return the first marker in *marker_aliases* found as its own line, a
    ``Marker:``-style trailer, or an *added* diff line (an optional leading
    ``+``), in *text*, or ``None``.

    A bare substring test (``marker in text``) treats a message like "This
    is NOT a BREAKING-CHANGE, just an internal cleanup." as an approval,
    since the literal marker text still appears mid-sentence. Anchoring to
    the start of a line (the git-trailer convention — c.f. ``Signed-off-by:``)
    closes that false positive while still accepting this repo's marker
    styles: a ``BREAKING-CHANGE: <detail>`` trailer in a commit message, the
    legacy whole-line alias ``# approved-breaking-change``, and — for
    diff-text callers such as the legacy staged-diff check — the marker on a
    line the diff *adds*, never a line it only *deletes*
    (``-BREAKING-CHANGE...``), which a bare substring test would have
    wrongly accepted too.
    """
    for marker in marker_aliases:
        pattern = re.compile(rf"^\+?[ \t]*{re.escape(marker)}[ \t]*(:|$)", re.MULTILINE)
        if pattern.search(text):
            return marker
    return None
