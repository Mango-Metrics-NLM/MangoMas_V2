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
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_PYPROJECT_PATH: Final[Path] = Path("pyproject.toml")


class GovernanceLoadError(Exception):
    """Raised when ``[tool.mangomas.governance]`` is missing, empty, or malformed."""


def parse_governance(toml_text: str, *, source: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(protected_paths, marker_aliases)`` parsed from *toml_text*.

    Split out from :func:`load_governance` so the same shape validation serves
    a policy that never touches the filesystem — ``scripts/check_protected_paths.py``
    reads the base ref's table out of ``git show`` (ADR-0030), and a policy
    read from a git blob must be validated exactly as strictly as one read
    from disk.

    *source* is used only to name the origin in error messages ("which
    pyproject did this come from?"), which is the difference between a
    debuggable CI failure and a puzzling one.

    Raises :class:`GovernanceLoadError` on any parse/shape failure — callers
    decide whether to fail loudly (the CI gate) or fall back to safe defaults
    with a logged warning (the advisory hook).
    """
    try:
        doc = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise GovernanceLoadError(f"malformed TOML in {source}: {exc}") from exc
    try:
        governance = doc["tool"]["mangomas"]["governance"]
        protected_paths = frozenset(governance["protected_paths"])
        marker_aliases = frozenset(governance["breaking_change_marker_aliases"])
    except (KeyError, TypeError) as exc:
        raise GovernanceLoadError(
            f"[tool.mangomas.governance] in {source} missing or malformed key: {exc}"
        ) from exc
    if not protected_paths or not marker_aliases:
        raise GovernanceLoadError(
            f"[tool.mangomas.governance] tables in {source} must be non-empty"
        )
    return protected_paths, marker_aliases


def load_governance(
    pyproject_path: Path = DEFAULT_PYPROJECT_PATH,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(protected_paths, marker_aliases)`` from *pyproject_path*.

    Thin filesystem wrapper over :func:`parse_governance`; the validation
    lives there so a git-blob policy cannot drift from a file policy.

    Raises :class:`GovernanceLoadError` on any read/parse/shape failure.
    """
    try:
        raw = pyproject_path.read_bytes()
    except OSError as exc:
        raise GovernanceLoadError(f"cannot read {pyproject_path}: {exc}") from exc
    return parse_governance(raw.decode("utf-8"), source=str(pyproject_path))


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


@dataclass(frozen=True)
class MarkerScopes:
    """Which protected paths a range's approval markers cover.

    ``paths`` are the protected paths named by a scoped
    ``BREAKING-CHANGE: <path> - <rationale>`` trailer. ``has_unscoped`` records
    whether any marker named no path at all — the historical form, which still
    approves everything, so adopting the scoped form is additive rather than a
    cliff for branches carrying older markers.
    """

    paths: frozenset[str]
    has_unscoped: bool

    def approves(self, path: str) -> bool:
        """Return whether *path*'s change is approved by these markers."""
        return self.has_unscoped or normalize_repo_path(path) in self.paths


def normalize_repo_path(path: str) -> str:
    """Return *path* with Windows separators folded and a leading ``./`` stripped.

    The policy table is hand-edited on both Windows and POSIX, and a commit
    message may name either style, so comparisons happen on one canonical form.
    Mirrors ``mangomas.harness.governance.normalize_path`` — kept separate for
    the same reason the rest of this module is (``scripts/`` must not import
    ``mangomas``), and deliberately does not touch a leading ``.`` beyond that
    prefix so ``.mcp.json`` survives.
    """
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def find_marker_scopes(
    text: str, marker_aliases: frozenset[str], known_paths: frozenset[str]
) -> MarkerScopes:
    """Return the :class:`MarkerScopes` the markers in *text* establish.

    A marker line is **scoped** when the first token after its colon is a path
    in *known_paths*, and **unscoped** otherwise. That split is what keeps the
    historical ``BREAKING-CHANGE: reworked the error taxonomy`` approving
    everything while ``BREAKING-CHANGE: src/mangomas/errors.py - agreed`` binds
    to one file.

    The discriminator is deliberately "the first token is a *known* protected
    path" rather than "looks path-ish". The looser test has a bad failure
    mode in the safe direction and a worse one in the unsafe direction: a
    mis-typed path would be recognised as a scope, silently approving nothing
    and failing the gate confusingly, while this way a typo reads as prose and
    approves broadly — visibly, in a line a reviewer can see. Only an exact
    protected path narrows an approval, so narrowing is always deliberate.
    """
    scoped: set[str] = set()
    has_unscoped = False
    for marker in marker_aliases:
        pattern = re.compile(
            rf"^\+?[ \t]*{re.escape(marker)}[ \t]*(?::(?P<detail>.*))?$", re.MULTILINE
        )
        for match in pattern.finditer(text):
            detail = (match.group("detail") or "").strip()
            first_token = detail.split(maxsplit=1)[0].rstrip(",;") if detail else ""
            candidate = normalize_repo_path(first_token)
            if candidate in known_paths:
                scoped.add(candidate)
            else:
                has_unscoped = True
    return MarkerScopes(paths=frozenset(scoped), has_unscoped=has_unscoped)
