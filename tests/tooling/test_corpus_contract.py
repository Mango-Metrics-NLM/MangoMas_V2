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

import itertools
import re
from pathlib import Path

import pytest

from tests._script_loader import load_script_module
from tests.constants import (
    AGENT_DESCRIPTION_MAX_CHARS,
    AGENT_TRIGGER_PHRASE_PATTERN,
    CLAUDE_SKILLS_DIR_RELPATH,
    CORPUS_DOC_RELPATHS,
    EXPECTED_AGENT_SLUGS,
    EXPECTED_SKILL_SLUGS,
    MAX_ROUTER_DESCRIPTION_JACCARD,
    MIN_CORPUS_TRACEABILITY_REFS,
    PROTECTED_PATH_OWNER_SLUGS,
    RETIRED_AGENT_PREFIX,
    RETIRED_SKILLS_DIR_RELPATH,
    ROUTER_AGENT_SLUGS,
    WRITE_CAPABLE_AGENT_SLUGS,
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
    # Asserted rather than skipped: a typo'd or renamed relpath used to yield a
    # green *skip*, so the doc it was meant to police went unchecked and the
    # suite still reported success.
    assert path.is_file(), f"{relpath} is listed in CORPUS_DOC_RELPATHS but does not exist"
    assert RETIRED_SKILLS_DIR_RELPATH not in path.read_text(encoding="utf-8")


# ── Agents (spec-0018 / ADR-0024) ─────────────────────────────────────────────
#
# Parametrised off the linter's own glob, so these survive the move to
# `.claude/agents/` without edits — the same reason B1's skill fixtures derive
# their roots from `SKILLS_GLOB` rather than a literal.

_linter = load_script_module("lint_agent_frontmatter.py")


def _agent_paths() -> list[Path]:
    return sorted(_REPO_ROOT.glob(_linter.AGENTS_GLOB))


def _agent_frontmatter(path: Path) -> dict[str, object]:
    parsed: dict[str, object] = _linter._split_frontmatter(path.read_text(encoding="utf-8"))
    return parsed


def _agents() -> dict[str, dict[str, object]]:
    return {str(_agent_frontmatter(p)["name"]): _agent_frontmatter(p) for p in _agent_paths()}


def _tool_tokens(frontmatter: dict[str, object]) -> list[str]:
    tokens: list[str] | None = _linter._normalize_tools(frontmatter.get("tools"))
    assert tokens is not None, f"unparseable tools: {frontmatter.get('tools')!r}"
    return tokens


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def test_agent_corpus_is_non_empty() -> None:
    """Companion guard for every parametrised agent test below: an empty glob
    would make each of them vacuously true, which is the exact failure mode
    spec-0018 exists to remove."""
    assert _agent_paths()
    assert len(_agent_paths()) == len(EXPECTED_AGENT_SLUGS)


def test_agent_roster_matches_the_declared_set() -> None:
    on_disk = set(_agents())
    assert on_disk == set(EXPECTED_AGENT_SLUGS), (
        f"unexpected: {sorted(on_disk - set(EXPECTED_AGENT_SLUGS))}; "
        f"missing: {sorted(set(EXPECTED_AGENT_SLUGS) - on_disk)}"
    )


def test_agent_and_skill_namespaces_are_disjoint() -> None:
    """A slug shared between an agent and a skill makes "use mango-error"
    ambiguous in a sentence, and the two are invoked differently."""
    assert set(_agents()).isdisjoint(set(EXPECTED_SKILL_SLUGS))


# ── Permissions ───────────────────────────────────────────────────────────────


def test_write_capable_agents_match_the_reviewed_set() -> None:
    """A reviewed-change gate, deliberately not a deny-list substitute: it
    covers 12 of 19 and only fires when the set changes. Granting Edit or Write
    to another agent should be a decision someone made, not a diff someone
    skimmed."""
    write_capable = {
        slug for slug, fm in _agents().items() if {"Edit", "Write"} & set(_tool_tokens(fm))
    }
    assert write_capable == set(WRITE_CAPABLE_AGENT_SLUGS), (
        f"gained write access: {sorted(write_capable - set(WRITE_CAPABLE_AGENT_SLUGS))}; "
        f"lost it: {sorted(set(WRITE_CAPABLE_AGENT_SLUGS) - write_capable)}"
    )


@pytest.mark.parametrize("slug", sorted(ROUTER_AGENT_SLUGS))
def test_routers_cannot_write(slug: str) -> None:
    """Routers carry the broadest descriptions and take the most auto-delegated
    traffic. They read and advise; nothing about routing needs Edit, Write or
    Bash — and Bash alone is write-capable via `sed -i` while routing around the
    PreToolUse Edit|Write|NotebookEdit matcher entirely."""
    assert not {"Edit", "Write", "Bash"} & set(_tool_tokens(_agents()[slug]))


@pytest.mark.parametrize("slug", sorted(EXPECTED_AGENT_SLUGS))
def test_every_agent_declares_real_tools(slug: str) -> None:
    """Omitting `tools` inherits *every* tool, so an unrecognised or absent list
    is a fully-privileged agent rather than a harmless typo."""
    frontmatter = _agents()[slug]
    assert "tools" in frontmatter
    assert _linter._invalid_tool_tokens(_tool_tokens(frontmatter)) == []


@pytest.mark.parametrize("slug", sorted(EXPECTED_AGENT_SLUGS))
def test_no_agent_uses_the_ignored_delegation_scoping(slug: str) -> None:
    """`Agent(a, b)` is silently ignored inside a subagent definition — the
    agent would receive unrestricted delegation, not the named subset."""
    assert _linter._scoped_delegation_tokens(_tool_tokens(_agents()[slug])) == []


@pytest.mark.parametrize("slug", sorted(PROTECTED_PATH_OWNER_SLUGS))
def test_protected_path_owners_name_the_trailer(slug: str) -> None:
    """Before this, not one of the 19 mentioned `BREAKING-CHANGE`. An agent with
    Edit and Write on a protected path, unaware of the gate, fails CI on its
    first commit and cannot tell why."""
    path = next(p for p in _agent_paths() if _agent_frontmatter(p)["name"] == slug)
    assert _linter.BREAKING_CHANGE_MARKER in path.read_text(encoding="utf-8")


# ── Routing posture ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", sorted(EXPECTED_AGENT_SLUGS))
def test_description_is_within_the_cap(slug: str) -> None:
    """Descriptions are the whole routing surface and load at every session
    start, so their combined size is a standing cost."""
    description = str(_agents()[slug]["description"])
    assert len(description) <= AGENT_DESCRIPTION_MAX_CHARS, len(description)


@pytest.mark.parametrize("slug", sorted(EXPECTED_AGENT_SLUGS))
def test_retired_hierarchy_prefix_is_gone(slug: str) -> None:
    assert RETIRED_AGENT_PREFIX not in str(_agents()[slug]["description"])


@pytest.mark.parametrize("slug", sorted(EXPECTED_AGENT_SLUGS))
def test_only_routers_carry_trigger_conditions(slug: str) -> None:
    """The control that actually works. "Invoke explicitly when X" does not
    stop auto-delegation, because the router matches X and never reads the
    modal verb — so the conditions have to be absent, not re-worded."""
    has_trigger = re.search(AGENT_TRIGGER_PHRASE_PATTERN, str(_agents()[slug]["description"]), re.I)
    assert bool(has_trigger) == (slug in ROUTER_AGENT_SLUGS)


def test_router_descriptions_do_not_converge() -> None:
    """Routers are the only auto-delegated agents, so they are the only pair
    that can compete for the same request. A ratchet: the observed maximum is
    well under the ceiling, and this exists to stop that drifting."""
    descriptions = {s: str(_agents()[s]["description"]) for s in ROUTER_AGENT_SLUGS}
    for first, second in itertools.combinations(sorted(descriptions), 2):
        left, right = _words(descriptions[first]), _words(descriptions[second])
        overlap = len(left & right) / len(left | right)
        assert overlap < MAX_ROUTER_DESCRIPTION_JACCARD, f"{first} x {second}: {overlap:.3f}"


def test_corpus_carries_traceability_references() -> None:
    """Guards against a body sweep quietly severing the corpus from its ADRs.
    The pattern accepts `spec 0012` with a space — the corpus writes it that
    way, so a `spec-\\d{4}` regex would silently match one fewer."""
    body = "\n".join(p.read_text(encoding="utf-8") for p in _agent_paths())
    found = set(re.findall(r"ADR-\d{4}|spec[- ]\d{4}", body))
    assert len(found) >= MIN_CORPUS_TRACEABILITY_REFS, sorted(found)
