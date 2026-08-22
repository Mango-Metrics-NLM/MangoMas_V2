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
import subprocess
from pathlib import Path

import pytest

from tests._script_loader import load_script_module
from tests.constants import (
    AGENT_DESCRIPTION_MAX_CHARS,
    AGENT_SECTION_HEADINGS,
    AGENT_SKILL_OWNERS,
    AGENT_SLUG_PREFIX,
    AGENT_TRIGGER_PHRASE_PATTERN,
    CLAUDE_SKILLS_DIR_RELPATH,
    CORPUS_DOC_RELPATHS,
    EXPECTED_AGENT_SLUGS,
    EXPECTED_SKILL_SLUGS,
    HARNESS_SKILL_SLUG,
    MAX_ROUTER_DESCRIPTION_JACCARD,
    MIN_CORPUS_TRACEABILITY_REFS,
    PROCEDURE_SECTION_HEADING,
    PROTECTED_PATH_OWNER_SLUGS,
    RETIRED_AGENT_PREFIX,
    RETIRED_AGENTS_DIR_RELPATH,
    RETIRED_CHANGELOG_HEADING,
    RETIRED_SKILLS_DIR_RELPATH,
    RETIRED_STRAY_AGENT_FILENAME,
    ROUTER_AGENT_SLUGS,
    SKILL_UNMAPPED_AGENT_SLUGS,
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


@pytest.mark.parametrize(
    "retired", [RETIRED_SKILLS_DIR_RELPATH, RETIRED_AGENTS_DIR_RELPATH], ids=["skills", "agents"]
)
@pytest.mark.parametrize("relpath", CORPUS_DOC_RELPATHS)
def test_docs_do_not_reference_a_retired_corpus_path(relpath: str, retired: str) -> None:
    """`CLAUDE.md` auto-loads into every session, so a stale pointer there is
    worse than one anywhere else in the repo — it is read before any work
    starts. Historical records (CHANGELOG, dated plans) are excluded by
    construction; they describe the state at the time of writing."""
    path = _REPO_ROOT / relpath
    # Asserted rather than skipped: a typo'd or renamed relpath used to yield a
    # green *skip*, so the doc it was meant to police went unchecked and the
    # suite still reported success.
    assert path.is_file(), f"{relpath} is listed in CORPUS_DOC_RELPATHS but does not exist"
    assert retired not in path.read_text(encoding="utf-8")


# ── Agents (spec-0018 / ADR-0024) ─────────────────────────────────────────────
#
# Parametrised off the linter's own glob, so these survive the move to
# `.claude/agents/` without edits — the same reason B1's skill fixtures derive
# their roots from `SKILLS_GLOB` rather than a literal.

_linter = load_script_module("lint_agent_frontmatter.py")


# ── Full schema validation (spec-0018 / ADR-0024) ────────────────────────────
#
# Every test in this module checks one structural property at a time (a tool
# token, a heading, a description-length ceiling, ...). None of them run the
# actual Pydantic schema `scripts/lint_agent_frontmatter.py` enforces —
# `make frontmatter` does, but as a separate non-pytest step, and
# `tests/test_lint_agent_frontmatter.py` only points the schema at synthetic
# fixtures, never this repo's own `.claude/` tree. This module's own
# `_frontmatter_name`-style scans also tolerate broken YAML elsewhere in the
# same file, so a corrupted skill or agent frontmatter file could pass every
# test above while `make frontmatter` — run nowhere under plain
# `pytest`/`make test` — is the only thing that would have caught it.


def test_live_corpus_passes_schema_lint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the real schema-lint entry point against the real `.claude/` tree.

    `run_schema_lint()` is exactly what `make frontmatter` calls (via
    `main()`'s default mode); calling it here under pytest closes the gap
    between "the linter would have caught this" and "a pytest run did".
    """
    monkeypatch.chdir(_REPO_ROOT)
    result = _linter.run_schema_lint()
    assert result.failures == (), "\n".join(result.failures)


def _agent_paths() -> list[Path]:
    """Return the *tracked* agent files.

    Scoped to `git ls-files` rather than a bare glob because Claude Code's
    ``/agents`` command writes a personal agent straight into
    ``.claude/agents/``. A glob-based roster would then report it as an
    unexpected entry and red-light ``make gate`` for a contributor who did
    nothing wrong. Tracked-only keeps the roster a statement about the shared
    corpus, which is the only thing it can honestly assert.
    """
    # `:(glob)` magic is required: git's default pathspec matching does not
    # treat `**` as a recursive wildcard, so the bare glob silently matched
    # zero files. The non-empty guard below is what caught that.
    result = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--", f":(glob){_linter.AGENTS_GLOB}"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=True,
    )
    return sorted(_REPO_ROOT / rel for rel in result.stdout.split("\0") if rel)


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
    covers 16 of 23 and only fires when the set changes. Granting Edit or Write
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


def test_retired_agents_directory_is_gone() -> None:
    """Two live trees would drift, and the linter's glob would validate only
    one of them — the same failure the skills migration guards against."""
    assert not (_REPO_ROOT / RETIRED_AGENTS_DIR_RELPATH).exists()


@pytest.mark.parametrize("slug", sorted(EXPECTED_AGENT_SLUGS))
def test_tracked_agents_carry_the_shared_prefix(slug: str) -> None:
    """The prefix is what makes the tracked roster separable from a
    contributor's own agents in the same directory, and what keeps agent and
    skill names from colliding as both corpora grow."""
    assert slug.startswith(AGENT_SLUG_PREFIX)


# ── R7: skills own procedure, agents own a surface ────────────────────────────


def _agent_body(slug: str) -> str:
    """Return an agent file's body, excluding its frontmatter block."""
    path = next(p for p in _agent_paths() if _agent_frontmatter(p)["name"] == slug)
    text = path.read_text(encoding="utf-8")
    _, _, rest = text.partition("---")
    _, _, body = rest.partition("---")
    return body


@pytest.mark.parametrize("slug", sorted(AGENT_SKILL_OWNERS))
def test_mapped_agent_references_its_skill(slug: str) -> None:
    """An agent whose surface a skill documents must name that skill.

    Before this, the entire agent corpus contained seven skill references, and
    `mango-telemetry-exporter-dev` had copied ~26 lines of `mango-deploy` while
    citing nothing — the duplication and the missing pointer are the same
    defect seen from two sides.
    """
    body = _agent_body(slug)
    missing = [skill for skill in AGENT_SKILL_OWNERS[slug] if skill not in body]
    assert missing == [], f"{slug} does not reference {missing}"


def test_every_agent_is_mapped_or_recorded_unmapped() -> None:
    """`AGENT_SKILL_OWNERS` + `SKILL_UNMAPPED_AGENT_SLUGS` must partition the corpus.

    Without this, "does a skill document this agent's surface?" is answered
    only for agents someone remembered to answer it for. A new agent simply
    fell out of both halves: `mango-ci-dev` shipped citing `mango-deploy` and
    `mango-mutation-proof` in its body, but was in neither set, so neither
    `test_mapped_agent_references_its_skill` nor
    `test_mapped_agent_has_no_procedure_section` applied to it — the corpus's
    two skill-duplication guards were simply off for that agent, silently.

    Disjointness matters as much as coverage: a slug in both sets would claim
    both that a skill owns its procedure and that none does.
    """
    mapped = set(AGENT_SKILL_OWNERS)
    unmapped = set(SKILL_UNMAPPED_AGENT_SLUGS)

    overlap = sorted(mapped & unmapped)
    assert overlap == [], f"agent(s) both mapped and recorded unmapped: {overlap}"

    unclassified = sorted(set(EXPECTED_AGENT_SLUGS) - mapped - unmapped)
    assert unclassified == [], (
        f"agent(s) in neither set: {unclassified}. Add each to AGENT_SKILL_OWNERS "
        "with the skill(s) documenting its procedure, or to "
        "SKILL_UNMAPPED_AGENT_SLUGS with why none does."
    )

    stale = sorted((mapped | unmapped) - set(EXPECTED_AGENT_SLUGS))
    assert stale == [], f"set(s) name retired/renamed agent(s): {stale}"


def test_agent_skill_owners_resolve_to_a_real_skill() -> None:
    """Every `AGENT_SKILL_OWNERS` value must still be a real skill.

    `test_mapped_agent_references_its_skill` above only checks the skill name
    is a substring of the agent's body prose — a renamed or retired skill
    mentioned only in stale prose still passes that check. This checks the
    mapping's *target* against the live roster instead, so a rename that
    updates the constant's key but leaves a retired slug in its value fails by
    name here, rather than silently validating a skill that no longer exists.
    """
    referenced = {skill for skills in AGENT_SKILL_OWNERS.values() for skill in skills}
    unresolved = sorted(referenced - set(EXPECTED_SKILL_SLUGS))
    assert unresolved == [], f"AGENT_SKILL_OWNERS references retired/renamed skill(s): {unresolved}"


@pytest.mark.parametrize("slug", sorted(AGENT_SKILL_OWNERS))
def test_mapped_agent_has_no_procedure_section(slug: str) -> None:
    """A mapped agent's recipe belongs to its skill.

    Deliberately scoped to mapped agents. A blanket ban would delete good
    content: the routers' and auditors' numbered steps are their own operating
    loop, not a recipe any skill owns.
    """
    assert PROCEDURE_SECTION_HEADING not in _agent_body(slug)


@pytest.mark.parametrize("slug", sorted(EXPECTED_AGENT_SLUGS))
def test_agent_headings_use_the_canonical_vocabulary(slug: str) -> None:
    """56 distinct headings existed across 19 agents, including three spellings
    of "surface you own". Beyond being unscannable, ad-hoc headings let a
    duplicated section hide under a new name."""
    headings = [line for line in _agent_body(slug).splitlines() if line.startswith("## ")]
    unknown = [h for h in headings if h not in AGENT_SECTION_HEADINGS]
    assert unknown == [], f"{slug} uses non-canonical headings: {unknown}"


def test_protected_path_governance_is_single_sourced() -> None:
    """The advisory-hook caveat lives in `mango-harness` alone. It was
    byte-identical across four agents until that skill absorbed it."""
    carriers = [
        p.relative_to(_REPO_ROOT).as_posix()
        for p in sorted(_REPO_ROOT.glob(".claude/**/*.md"))
        if "advisory only" in p.read_text(encoding="utf-8")
    ]
    assert carriers == [f".claude/skills/{HARNESS_SKILL_SLUG}/SKILL.md"], carriers


@pytest.mark.parametrize("slug", sorted(PROTECTED_PATH_OWNER_SLUGS))
def test_protected_path_owners_point_at_the_governance_skill(slug: str) -> None:
    """Having moved the prose out, each owner must still lead a reader to it."""
    assert HARNESS_SKILL_SLUG in _agent_body(slug)


def test_no_corpus_file_prescribes_the_retired_changelog_heading() -> None:
    """`### Breaking Changes` was prescribed in four places and appears in
    CHANGELOG.md zero times — it is not a Keep a Changelog section, which is the
    format the CHANGELOG declares. The enforced mechanism is the commit
    trailer, so a corpus file naming the heading sends a contributor to a
    convention the repo does not use."""
    offenders = [
        p.relative_to(_REPO_ROOT).as_posix()
        for p in sorted(_REPO_ROOT.glob(".claude/**/*.md"))
        if RETIRED_CHANGELOG_HEADING in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_stray_agent_md_files_remain() -> None:
    """Five dormant `agent.md` files sat in the source tree, referenced by
    nothing and wrong in ways only a reader would discover — a fictional
    `TurnRepository.save()`, an SSE format a client could not parse. Two earned
    promotion to a nested `CLAUDE.md`; the rest were skill duplicates. A file
    nothing loads cannot be kept honest, so the convention stays retired."""
    strays = [
        p.relative_to(_REPO_ROOT).as_posix()
        for p in _REPO_ROOT.rglob(RETIRED_STRAY_AGENT_FILENAME)
        if ".git/" not in p.as_posix()
    ]
    assert strays == []
