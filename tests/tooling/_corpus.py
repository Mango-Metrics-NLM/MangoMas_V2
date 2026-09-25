"""Shared scanning helpers for the doc-governance suites.

Three suites walk the same prose corpus for different reasons — the live-path
ledger, the corpus contract, and (from PR 3) the per-directory documents. Each
had grown its own private walker, and the duplication had already cost
something: ``test_live_path_ledger``'s walker omitted ``docs/workflow/``, which
is precisely where a stale ``composition.py`` citation survived every gate.

Everything here derives its answer from the declared constants in
``tests.constants.corpus`` rather than restating them, so widening the
denominator is a one-line policy change in one place.

Deliberately stdlib-only and side-effect-free: these helpers run inside pytest,
where the heavy imports are already paid for, but nothing here needs them.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tests.constants.corpus import (
    DATED_RECORD_DIRS,
    DATED_RECORD_FILES,
    LIVE_DOC_GLOBS,
    LIVE_DOC_ROOT_RELPATHS,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT: Path = Path(__file__).resolve().parents[2]

# One backticked span, no newlines inside. Markdown inline code cannot span a
# line break, so refusing to cross one keeps a stray backtick from swallowing a
# paragraph and reporting it as a single enormous "token".
_BACKTICKED = re.compile(r"`([^`\n]+)`")


@dataclass(frozen=True)
class Citation:
    """One backticked token, located precisely enough to fix without searching."""

    relpath: str
    line: int
    token: str

    def __str__(self) -> str:
        return f"{self.relpath}:{self.line} `{self.token}`"


def is_dated_record(relpath: str) -> bool:
    """Is this a record that is correct as of its date and must not be rewritten?

    CHANGELOG entries, ADRs, plans, analyses and specs describe the state at the
    time they were written. Editing one to satisfy a linter falsifies the
    record, so they are excluded from every liveness check.
    """
    if relpath in DATED_RECORD_FILES:
        return True
    return any(relpath.startswith(f"{directory}/") for directory in DATED_RECORD_DIRS)


@functools.cache
def live_doc_paths() -> tuple[Path, ...]:
    """Every doc read as current truth, sorted and de-duplicated.

    Cached because three suites call it and two of them parametrize over the
    result; without the cache the same globs are re-walked once per test case.
    """
    found: set[Path] = {REPO_ROOT / relpath for relpath in LIVE_DOC_ROOT_RELPATHS}
    for glob in LIVE_DOC_GLOBS:
        found.update(
            path
            for path in REPO_ROOT.glob(glob)
            if not is_dated_record(path.relative_to(REPO_ROOT).as_posix())
        )
    return tuple(sorted(path for path in found if path.is_file()))


def iter_backticked_tokens(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, token)`` for every inline-code span in ``text``.

    Line numbers are 1-based so they match what an editor shows.
    """
    for match in _BACKTICKED.finditer(text):
        yield text.count("\n", 0, match.start()) + 1, match.group(1)


def cites_basename(token: str, basename: str) -> bool:
    """Does one backticked ``token`` name the file ``basename``?

    True for the bare name and for any path ending in it, so ``config.py`` and
    ``src/mangomas/config.py`` both count — they cite the same module. A
    ``::symbol`` suffix is stripped first, so ``api/errors.py::_ERROR_STATUS``
    is recognised as a citation of ``errors.py``. Matching on a whole path
    segment is what keeps ``myconfig.py`` out.
    """
    candidate = token.split("::", 1)[0].strip()
    return candidate == basename or candidate.endswith(f"/{basename}")


def find_basename_citations(basename: str) -> list[Citation]:
    """Locate every live-doc backtick span naming ``basename``."""
    citations: list[Citation] = []
    for path in live_doc_paths():
        relpath = path.relative_to(REPO_ROOT).as_posix()
        for line, token in iter_backticked_tokens(path.read_text(encoding="utf-8")):
            if cites_basename(token, basename):
                citations.append(Citation(relpath=relpath, line=line, token=token))
    return citations
