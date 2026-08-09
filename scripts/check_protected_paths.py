"""CI gate: protected core contracts require a ``BREAKING-CHANGE`` commit.

The authoritative enforcement point for the protected-path rule (ADR-0021 /
spec-0017). Where the ``PreToolUse`` hook (``lint_agent_frontmatter.py --hook
pre-tool-use``) is only advisory — an in-session agent can create files, run
``Bash``, or use MCP filesystem tools, none of which it can gate completely —
this script reads committed history between a PR's base and head, which an
agent cannot rewrite because it cannot alter branch protection. Wire it as a
required status check.

Two independent checks, both computed from git history (never from working-tree
state, so a local edit that hasn't been committed cannot pass or fail this gate):

1. ``git diff --name-only <base>...<head>`` (merge-base diff) — did any file in
   the protected set change on this branch?
2. If so, ``git log --format=%B <base>..<head>`` (the commits unique to this
   branch) must contain a ``BREAKING-CHANGE`` marker (or its accepted legacy
   alias) *somewhere in a commit message* in that range. Checking commit
   messages rather than diff content closes the gap the old (broken)
   ``PreToolUse`` hook had even in its intended design: a diff-content check
   passes when the marker line is *deleted*, since a deletion still contains
   the string in the diff's ``-`` context. A commit message can't be edited
   after the fact without rewriting history, which this gate would then also
   see.

Both the protected-path set and the marker aliases live in ``pyproject.toml``
under ``[tool.mangomas.governance]`` — read via stdlib ``tomllib`` so neither
this script nor the ``PreToolUse`` hook needs ``mangomas`` installed.

Exit codes
----------
``EXIT_OK = 0``
    No protected path changed, or a qualifying commit message was found.
``EXIT_MISSING_MARKER = 1``
    A protected path changed and no commit in the range carries the marker.
``EXIT_GIT_ERROR = 2``
    A git command needed to compute the diff/log failed (e.g. the base ref is
    unresolvable — usually a shallow-clone or missing-fetch problem in CI).

Run::

    python scripts/check_protected_paths.py --base-ref origin/feat/initial-release
    python scripts/check_protected_paths.py --base-ref origin/feat/initial-release --head-ref HEAD
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

DEFAULT_PYPROJECT_PATH: Final[Path] = Path("pyproject.toml")
DEFAULT_HEAD_REF: Final[str] = "HEAD"

EXIT_OK: Final[int] = 0
EXIT_MISSING_MARKER: Final[int] = 1
EXIT_GIT_ERROR: Final[int] = 2


class GovernanceConfigError(Exception):
    """Raised when ``[tool.mangomas.governance]`` is missing or malformed."""


def _load_governance(pyproject_path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(protected_paths, marker_aliases)`` from *pyproject_path*.

    Raises :class:`GovernanceConfigError` on any malformed/missing config —
    this script must fail loudly on a config problem rather than silently
    gating nothing.
    """
    try:
        raw = pyproject_path.read_bytes()
    except OSError as exc:
        raise GovernanceConfigError(f"cannot read {pyproject_path}: {exc}") from exc
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise GovernanceConfigError(f"malformed TOML in {pyproject_path}: {exc}") from exc

    try:
        governance = doc["tool"]["mangomas"]["governance"]
        protected_paths = frozenset(governance["protected_paths"])
        marker_aliases = frozenset(governance["breaking_change_marker_aliases"])
    except KeyError as exc:
        raise GovernanceConfigError(
            f"[tool.mangomas.governance] missing required key {exc}"
        ) from exc
    if not protected_paths or not marker_aliases:
        raise GovernanceConfigError("[tool.mangomas.governance] tables must be non-empty")
    return protected_paths, marker_aliases


def _run_git(args: list[str]) -> str:
    """Return *args*' stdout. Raises ``GovernanceConfigError`` on failure.

    ``subprocess.run`` itself raises ``OSError`` (e.g. ``FileNotFoundError``)
    when the ``git`` executable can't be found at all, distinct from git
    running and exiting non-zero — both must land on ``EXIT_GIT_ERROR``
    rather than an uncaught traceback.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GovernanceConfigError(f"git {' '.join(args)} failed to start: {exc}") from exc
    if result.returncode != 0:
        raise GovernanceConfigError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or '(no stderr)'}"
        )
    return result.stdout


def _changed_files(base_ref: str, head_ref: str) -> list[str]:
    """Return paths changed on *head_ref* since it diverged from *base_ref*."""
    output = _run_git(["diff", "--name-only", f"{base_ref}...{head_ref}"])
    return [line for line in output.splitlines() if line]


def _commit_messages(base_ref: str, head_ref: str) -> str:
    """Return the concatenated commit messages unique to *head_ref* over *base_ref*."""
    return _run_git(["log", "--format=%B", f"{base_ref}..{head_ref}"])


def check(base_ref: str, head_ref: str, pyproject_path: Path) -> int:
    """Run the gate; return one of the module's ``EXIT_*`` codes."""
    try:
        protected_paths, marker_aliases = _load_governance(pyproject_path)
        changed = _changed_files(base_ref, head_ref)
        touched_protected = sorted(set(changed) & protected_paths)
        if not touched_protected:
            print("No protected core contracts changed. OK.")
            return EXIT_OK

        messages = _commit_messages(base_ref, head_ref)
    except GovernanceConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_GIT_ERROR

    matched_marker = next((marker for marker in marker_aliases if marker in messages), None)
    print("Protected core contracts changed on this branch:")
    for path in touched_protected:
        print(f"  - {path}")

    if matched_marker is not None:
        print(f"Found approval marker {matched_marker!r} in a commit message. OK.")
        return EXIT_OK

    print(
        "FAIL: no commit in this range carries a BREAKING-CHANGE marker.\n"
        "Add a commit whose message contains 'BREAKING-CHANGE' explaining the "
        "backwards-compatibility impact of the change above.",
        file=sys.stderr,
    )
    return EXIT_MISSING_MARKER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if a protected core contract changed without a BREAKING-CHANGE commit."
    )
    parser.add_argument(
        "--base-ref",
        required=True,
        help="Git ref to diff against (e.g. origin/feat/initial-release).",
    )
    parser.add_argument(
        "--head-ref",
        default=DEFAULT_HEAD_REF,
        help=f"Git ref being checked (default: {DEFAULT_HEAD_REF!r}).",
    )
    parser.add_argument(
        "--pyproject",
        default=str(DEFAULT_PYPROJECT_PATH),
        help=f"Path to pyproject.toml (default: {DEFAULT_PYPROJECT_PATH}).",
    )
    args = parser.parse_args(argv)
    return check(args.base_ref, args.head_ref, Path(args.pyproject))


if __name__ == "__main__":
    sys.exit(main())
