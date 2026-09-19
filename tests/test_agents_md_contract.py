"""The root instruction pair: one document, split across two filenames.

``AGENTS.md`` is the vendor-neutral half, in the format the Agentic AI
Foundation stewards and 30+ coding agents read. ``CLAUDE.md`` keeps only what
is Claude Code's own and pulls the rest in with a first-line ``@AGENTS.md``.

The failure this guards is a split whose halves never rejoin. A first line that
*mentions* ``AGENTS.md`` while the file is missing, empty, or renamed leaves
every session reading a third of its instructions and nothing saying so — and
``tests/deploy/test_env_example_contract.py`` would go on enforcing a ~110-row
configuration contract against a document no session actually receives.

Measured layering note (ADR-0035): because a root ``CLAUDE.md`` exists at all,
Claude Code does **not** read nested ``AGENTS.md`` files anywhere in the tree.
Per-directory instruction files here are therefore named ``CLAUDE.md``, and
``test_no_nested_agents_md_files`` keeps that from being re-learned the hard
way.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.constants.corpus import AGENTS_MD_IMPORT_LINE, ROOT_INSTRUCTION_RELPATHS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_MD = _REPO_ROOT / "AGENTS.md"
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"

# A section whose title names Claude Code itself belongs to the Claude half.
# This is the rule the split was performed with, asserted rather than restated
# as a list of headings that would drift the next time one is added.
_CLAUDE_SECTION_PREFIX = "## Claude Code"
_SECTION_RE = re.compile(r"^(## .*)$", re.MULTILINE)


def _sections(path: Path) -> list[str]:
    return [line.strip() for line in _SECTION_RE.findall(path.read_text(encoding="utf-8"))]


def test_both_instruction_docs_exist() -> None:
    """Non-vacuity: every check below reads these two files."""
    missing = [rel for rel in ROOT_INSTRUCTION_RELPATHS if not (_REPO_ROOT / rel).is_file()]
    assert missing == [], f"root instruction doc(s) missing: {missing}"


def test_claude_md_first_line_imports_agents_md() -> None:
    """The import must be the literal first line, not merely present.

    Claude Code resolves ``@path`` imports from the top of the file. A mention
    further down is prose, not an import, and reads identically to a reader.
    """
    first_line = _CLAUDE_MD.read_text(encoding="utf-8").splitlines()[0].strip()
    assert first_line == AGENTS_MD_IMPORT_LINE, (
        f"CLAUDE.md's first line must be exactly {AGENTS_MD_IMPORT_LINE!r} so the "
        f"vendor-neutral half is imported into every session; found {first_line!r}."
    )


def test_the_agents_md_import_resolves() -> None:
    """The half that PR F never asserted: the import must point at something.

    A dangling import is silent — Claude Code reads the file it can find and
    says nothing about the one it cannot.
    """
    assert _AGENTS_MD.is_file(), "CLAUDE.md imports AGENTS.md, which does not exist"
    sections = _sections(_AGENTS_MD)
    assert sections, (
        "AGENTS.md has no '## ' sections — the import resolves to an empty "
        "document, so every session reads only the Claude-specific half."
    )


def test_the_split_puts_each_section_on_the_right_side() -> None:
    """Claude-specific sections stay; everything else is vendor-neutral.

    Asserted as the rule rather than a heading list, so a new ``## Claude Code
    …`` section is placed correctly by construction instead of by memory.
    """
    misplaced_in_agents = [s for s in _sections(_AGENTS_MD) if s.startswith(_CLAUDE_SECTION_PREFIX)]
    assert misplaced_in_agents == [], (
        f"Claude-Code-specific sections belong in CLAUDE.md, not the "
        f"vendor-neutral AGENTS.md: {misplaced_in_agents}"
    )
    stranded_in_claude = [
        s for s in _sections(_CLAUDE_MD) if not s.startswith(_CLAUDE_SECTION_PREFIX)
    ]
    assert stranded_in_claude == [], (
        f"these sections are not Claude-Code-specific and belong in AGENTS.md, "
        f"where every other coding agent can read them: {stranded_in_claude}"
    )


def test_no_section_heading_is_duplicated_across_the_pair() -> None:
    """The pair is concatenated, so a repeated section is read twice.

    Duplication is also how a split silently becomes two diverging copies.
    """
    overlap = sorted(set(_sections(_AGENTS_MD)) & set(_sections(_CLAUDE_MD)))
    assert overlap == [], f"these sections appear in both halves and will be read twice: {overlap}"


def test_no_nested_agents_md_files() -> None:
    """A nested AGENTS.md would be dead weight, measurably.

    Probe matrix in ADR-0035: while a root ``CLAUDE.md`` exists, Claude Code
    ignores every ``AGENTS.md`` below the root — so a per-directory file under
    that name is never loaded. That is precisely the defect that retired this
    repository's ``agent.md`` corpus ("a file nothing loads cannot be kept
    honest"), and it would be invisible rather than noisy.
    """
    nested = sorted(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in _REPO_ROOT.rglob("AGENTS.md")
        if ".git/" not in path.as_posix() and ".venv/" not in path.as_posix() and path != _AGENTS_MD
    )
    assert nested == [], (
        f"nested AGENTS.md file(s) found: {nested}. Claude Code does not read "
        "these while a root CLAUDE.md exists — name per-directory instruction "
        "files CLAUDE.md instead (ADR-0035)."
    )
