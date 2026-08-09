"""Contract tests for the live Claude Code corpus (spec-0018 / ADR-0024).

The corpus moved from `.github/` (a GitHub Copilot surface Claude Code never
reads) to `.claude/`, which both Claude Code and VS Code Copilot read. These
tests are the mechanical replacement for the only check the corpus had before:
a human running the linter and trusting that it found something.

The distinction that matters here is between the *floor* in
``scripts/lint_agent_frontmatter.py`` — structural, ">= 1", never rots — and
the *roster* asserted below by set equality. The floor catches a glob that
matches nothing. Only the roster catches a single file quietly disappearing,
and it does so by naming it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.constants import (
    CLAUDE_SKILLS_DIR_RELPATH,
    CORPUS_DOC_RELPATHS,
    EXPECTED_SKILL_SLUGS,
    RETIRED_SKILLS_DIR_RELPATH,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / CLAUDE_SKILLS_DIR_RELPATH
_SKILL_FILENAME = "SKILL.md"


def _skill_dirs() -> list[Path]:
    return sorted(p.parent for p in _SKILLS_DIR.glob(f"*/{_SKILL_FILENAME}"))


def _frontmatter_name(skill_md: Path) -> str | None:
    """Return the ``name:`` value from *skill_md*'s frontmatter block only.

    Scoped to the frontmatter deliberately: a naive whole-file search also
    matches ``name:`` inside the fenced YAML examples these skills are full of.
    """
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    _, _, rest = text.partition("---")
    frontmatter, _, _ = rest.partition("---")
    for line in frontmatter.splitlines():
        if line.startswith("name:"):
            return line.removeprefix("name:").strip()
    return None


# ── Inventory ─────────────────────────────────────────────────────────────────


def test_skill_corpus_is_non_empty() -> None:
    """Guards the helpers below — an empty directory would otherwise make every
    remaining assertion in this file vacuously true."""
    assert _SKILLS_DIR.is_dir()
    assert _skill_dirs()


def test_skill_roster_matches_the_declared_set() -> None:
    """Set equality, not a count: the assertion message names the skill that
    appeared or vanished, and updating the constant is the review record."""
    on_disk = {d.name for d in _skill_dirs()}
    assert on_disk == set(EXPECTED_SKILL_SLUGS), (
        f"unexpected: {sorted(on_disk - set(EXPECTED_SKILL_SLUGS))}; "
        f"missing: {sorted(set(EXPECTED_SKILL_SLUGS) - on_disk)}"
    )


def test_retired_skills_directory_is_gone() -> None:
    """Nobody resurrected the pre-migration tree. Two live copies would drift,
    and the linter would validate only one of them."""
    assert not (_REPO_ROOT / RETIRED_SKILLS_DIR_RELPATH).exists()


# ── Identity ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_skill_directory_name_matches_frontmatter_name(skill_dir: Path) -> None:
    """Both Claude Code and Copilot key a skill on its directory name; the
    ``name:`` field is what a reader sees. A mismatch makes documentation and
    invocation disagree."""
    assert _frontmatter_name(skill_dir / _SKILL_FILENAME) == skill_dir.name


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_every_skill_has_a_skill_md(skill_dir: Path) -> None:
    assert (skill_dir / _SKILL_FILENAME).is_file()


# ── Documentation pointers ────────────────────────────────────────────────────


@pytest.mark.parametrize("relpath", CORPUS_DOC_RELPATHS)
def test_docs_do_not_reference_the_retired_skills_path(relpath: str) -> None:
    """`CLAUDE.md` auto-loads into every session, so a stale pointer there is
    worse than one anywhere else in the repo — it is read before any work
    starts. Historical records (CHANGELOG, dated plans) are excluded by
    construction; they describe the state at the time of writing."""
    path = _REPO_ROOT / relpath
    if not path.is_file():
        pytest.skip(f"{relpath} not present")
    assert RETIRED_SKILLS_DIR_RELPATH not in path.read_text(encoding="utf-8")
