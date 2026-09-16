"""Uniqueness contract for the numbered decision records.

`CLAUDE.md` states the rule plainly — a new spec takes "the next free integer,
mirroring the `docs/adr/` numbering" — and nothing enforced it. That is the same
shape as every other finding in this audit: a control written as prose, with no
mechanism behind it (spec-0022 R15).

The failure it permits is quiet. Two branches allocate `0031` independently,
each writes `0031-<its-own-slug>.md`, and the filenames differ — so git reports
no conflict, both files merge cleanly, and the decision log ends up with two
records claiming the same number. Nothing goes red. The next contributor reading
"see ADR-0031" cannot tell which document is meant, and the reference is
ambiguous forever, because renumbering afterwards breaks every citation already
written against it.

This guard cannot prevent the collision — two open branches cannot see each
other — but it makes whichever merges *second* go red, so the number is
reallocated deliberately instead of silently duplicated.

Gaps are explicitly fine. `docs/adr/` already skips 0006, 0007 and 0022:
withdrawn records leave holes, and asserting contiguity would demand renumbering
live documents to close them. Uniqueness is the property that matters.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

from tests.constants import (
    DECISION_RECORD_DIRS,
    DECISION_RECORD_UNNUMBERED_STEMS,
)

# The floors belong only to the vacuity check; the two shape checks take the
# directory alone rather than an argument they would have to ignore.
_DIRECTORIES: tuple[str, ...] = tuple(directory for directory, _ in DECISION_RECORD_DIRS)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# `NNNN-kebab-slug` — the shape CLAUDE.md and `specs/README.md` both document.
# Anchored at both ends so `0031-Durable_Turn_Record` or `ADR-31-foo` fail as
# loudly as a duplicate does.
_NUMBERED_STEM_RE = re.compile(r"^(?P<number>\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*$")


def _records(directory: str) -> list[Path]:
    return sorted((_REPO_ROOT / directory).glob("*.md"))


@pytest.mark.parametrize("directory", _DIRECTORIES)
def test_every_record_number_is_unique(directory: str) -> None:
    """No two records in one directory may claim the same number."""
    by_number: dict[str, list[str]] = defaultdict(list)
    for path in _records(directory):
        match = _NUMBERED_STEM_RE.match(path.stem)
        if match is not None:
            by_number[match.group("number")].append(path.name)

    duplicates = {number: names for number, names in by_number.items() if len(names) > 1}

    assert not duplicates, (
        f"{directory} contains records sharing a number: {duplicates}. "
        "Two branches allocated it independently; renumber the later one to the "
        "next free integer and update its cross-references — a citation by "
        "number is otherwise ambiguous forever"
    )


@pytest.mark.parametrize("directory", _DIRECTORIES)
def test_every_record_is_numbered_or_a_known_exception(directory: str) -> None:
    """A file that matches neither shape is unreachable by number.

    Without this, ``0031_durable_turn_record.md`` or ``ADR-31.md`` would simply
    fall out of the uniqueness check above — a record invisible to the very
    guard that exists to keep the numbering honest.
    """
    unnumbered = [
        path.name
        for path in _records(directory)
        if _NUMBERED_STEM_RE.match(path.stem) is None
        and path.name not in DECISION_RECORD_UNNUMBERED_STEMS
    ]

    assert not unnumbered, (
        f"{directory} contains records that are neither NNNN-kebab-slug nor a "
        f"known unnumbered file: {unnumbered}"
    )


@pytest.mark.parametrize(("directory", "minimum"), DECISION_RECORD_DIRS)
def test_each_directory_holds_at_least_its_floor(directory: str, minimum: int) -> None:
    """A moved or emptied directory must fail, not pass vacuously.

    The two checks above are satisfied by zero files. ``scripts/
    lint_agent_frontmatter.py`` already carries this floor for exactly that
    reason: a glob matching nothing used to produce a green gate that validated
    nothing. The floor is a lower bound on records that exist today, not a
    target — it only ever rises.
    """
    found = [path.name for path in _records(directory) if _NUMBERED_STEM_RE.match(path.stem)]

    assert len(found) >= minimum, (
        f"{directory} holds {len(found)} numbered records, below the floor of "
        f"{minimum}; the directory moved and this contract is validating nothing"
    )
