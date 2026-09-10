"""Claude Code corpus roster, frontmatter fixtures, and MCP/hook constants."""

from __future__ import annotations

import json

# ── Harness frontmatter linter fixtures ───────────────────────────────────────
VALID_AGENT_FRONTMATTER: str = """\
---
name: Example
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
tools: [read, search]
model: Claude Sonnet 4.5 (copilot)
argument-hint: "Pass an example argument"
---

Body content.
"""

# Claude Code agent format (spec-0018 / ADR-0024). Kept beside the legacy
# Copilot fixture above rather than replacing it: the migration needs both, so
# tests can assert the new format passes *and* that the old one is rejected with
# a message naming the specific field rather than a generic "extra inputs".
VALID_CLAUDE_AGENT_FRONTMATTER: str = """\
---
name: example-agent
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
tools: Read, Grep, Glob, Skill
model: inherit
---
"""

# Field values that must be rejected, each standing for a real defect in the
# migrating corpus. Kept as data so a new rejection rule adds a row, not a test.
INVALID_AGENT_MODEL_VALUES: tuple[str, ...] = (
    "Claude Sonnet 4.5 (copilot)",  # every one of the 19 agents carried this
    "Opus",
    "claude opus 5",
)
# Copilot tool aliases: valid in that tool, meaningless to Claude Code — and
# because omitting `tools` inherits everything, an unrecognised list is the
# dangerous kind of wrong rather than a harmless one.
INVALID_AGENT_TOOL_TOKENS: tuple[str, ...] = ("read", "edit", "search", "execute")
VALID_AGENT_TOOL_TOKENS: tuple[str, ...] = ("Read", "Grep", "Glob", "Skill", "Edit", "Write")
# An MCP tool name cannot be enumerated ahead of time — it depends on the
# caller's .mcp.json — so the validator accepts the shape.
VALID_MCP_TOOL_NAME: str = "mcp__github__pull_request_read"
# Delegation scoping that Claude Code ignores inside a subagent definition: the
# agent gets unrestricted delegation, not the named subset.
SCOPED_DELEGATION_TOOL_SPEC: str = "Agent(protocol-auditor, layering-auditor)"
# Fields Claude Code accepts and this project declines, vs fields carried over
# from the Copilot format. The two get different messages on purpose.
POLICY_REJECTED_AGENT_FIELDS: tuple[str, ...] = ("permissionMode", "hooks")
LEGACY_AGENT_FIELDS: tuple[str, ...] = ("argument-hint", "sub_agents")

VALID_SKILL_FRONTMATTER: str = """\
---
name: example-skill
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
argument-hint: "Describe what to do"
---

Body content.
"""

MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS: str = """\
---
name: bad-example
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
model: inherit
---

Body content.
"""
# Filename stem the fixtures above must be written under: Claude Code resolves
# an agent by its `name` field, so the lint requires the two to agree.
VALID_CLAUDE_AGENT_SLUG: str = "example-agent"
MISSING_TOOLS_AGENT_SLUG: str = "bad-example"

MALFORMED_SKILL_FRONTMATTER_SHORT_DESCRIPTION: str = """\
---
name: bad-skill
description: tooshort
argument-hint: "Pass an example argument"
---

Body content.
"""

# ── Claude Code ecosystem tooling (spec 0016 / ADR-0020) ─────────────────────
# Repo-relative paths to the two shared Claude Code config files.
CLAUDE_SETTINGS_RELPATH: str = ".claude/settings.json"
CLAUDE_SETTINGS_LOCAL_EXAMPLE_RELPATH: str = ".claude/settings.local.json.example"
MCP_CONFIG_RELPATH: str = ".mcp.json"

# ADR-0021 / spec-0017: the ConfigChange hook's opt-out env var, referenced
# by both the settings.local.json.example contract test and its
# documentation-pointer assertion.
HARNESS_CONFIG_AUDIT_MODE_ENV: str = "MANGOMAS_HARNESS__CONFIG_AUDIT_MODE"

# CognitiveSignal emission (spec-0030 / ADR-0029). Distinct from HARNESS__*.
SIGNAL_ENABLED_ENV: str = "MANGOMAS_SIGNAL__ENABLED"
SIGNAL_DIR_ENV: str = "MANGOMAS_SIGNAL__DIR"
SIGNAL_GENAI_SPANS_ENV: str = "MANGOMAS_SIGNAL__GENAI_SPANS"
SIGNAL_HTTP_URL_ENV: str = "MANGOMAS_SIGNAL__HTTP_URL"
SIGNAL_HTTP_TIMEOUT_ENV: str = "MANGOMAS_SIGNAL__HTTP_TIMEOUT_SECONDS"
SIGNAL_POLICY_ID_ENV: str = "MANGOMAS_SIGNAL__POLICY_ID"
SIGNAL_POLICY_VERSION_ENV: str = "MANGOMAS_SIGNAL__POLICY_VERSION"
SIGNAL_POLICY_SNAPSHOT_HASH_ENV: str = "MANGOMAS_SIGNAL__POLICY_SNAPSHOT_HASH"
SIGNAL_SCHEMA_VERSION_ENV: str = "MANGOMAS_SIGNAL__SCHEMA_VERSION"
SIGNAL_MOCK_INGEST_URL: str = "https://harness.example.test/ingest/cognitive"
SIGNAL_CUSTOM_POLICY_ID: str = "team.policy"
PLANNER_SIGNAL_GOAL: str = "ship it"
PLANNER_SIGNAL_STEPS: tuple[str, ...] = ("Build",)
PLANNER_SIGNAL_REPLY: str = json.dumps(
    {
        "goal": PLANNER_SIGNAL_GOAL,
        "steps": [{"step": 1, "description": PLANNER_SIGNAL_STEPS[0], "agent": None}],
    }
)
REVIEWER_SIGNAL_FEEDBACK: str = "Good job."
REVIEWER_SIGNAL_REMEDIATION: str = "nits"
REVIEWER_SIGNAL_REPLY: str = json.dumps(
    {
        "passed": True,
        "score": 0.8,
        "feedback": REVIEWER_SIGNAL_FEEDBACK,
        "suggestions": [REVIEWER_SIGNAL_REMEDIATION],
    }
)

# ── Live Claude Code corpus (spec-0018 / ADR-0024) ───────────────────────────
# Claude Code reads nothing from `.github/`; VS Code Copilot reads both roots,
# so `.claude/` is the single home that serves each tool.
CLAUDE_SKILLS_DIR_RELPATH: str = ".claude/skills"
RETIRED_SKILLS_DIR_RELPATH: str = ".github/skills"

# The roster, asserted by SET EQUALITY rather than by count: a count names
# nothing, whereas a set difference names the skill that appeared or vanished,
# and the one-line edit here is the review record for that change.
EXPECTED_SKILL_SLUGS: frozenset[str] = frozenset(
    {
        "mango-adapter",
        "mango-agent-add",
        "mango-cognitive",
        "mango-config",
        "mango-coverage-audit",
        "mango-decompose",
        "mango-deploy",
        "mango-error",
        "mango-eval",
        "mango-harness",
        "mango-mutation-proof",
        "mango-observability",
        "mango-rag",
        "mango-release",
        "mango-testing",
        "mango-topology",
        "mango-workflow",
    }
)

# Docs that describe the corpus and must not point at a retired path. Historical
# records are excluded: CHANGELOG entries and dated plan documents describe the
# state at the time they were written and are deliberately immutable.
CORPUS_DOC_RELPATHS: tuple[str, ...] = (
    "CLAUDE.md",
    "README.md",
    "NEXT_STEPS.md",
    ".github/copilot-instructions.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/architecture/c2-container.md",
    "docs/tooling/claude-code-ecosystem.md",
    # Nested CLAUDE.md files. Claude Code auto-loads one when work happens
    # in its directory, so a stale pointer here is read before any work
    # starts — the same argument that puts the root CLAUDE.md on this list.
    "tests/CLAUDE.md",
    "src/mangomas/core/CLAUDE.md",
)

# Hooks that predate the ecosystem-tooling integration, as
# ``(event, matcher, command)``. Every `.claude/settings.json` edit must be
# additive, so the contract test asserts each of these survives verbatim —
# listing them here (rather than inline) keeps the expected hook contract in
# one place and lets the test stay data-driven.
# One command string, two registrations: the same stdin-JSON mode is wired
# under both the Edit-family matcher and Bash (it discriminates by payload
# shape). Named so the two tuples below cannot drift apart.
PRE_TOOL_USE_HOOK_COMMAND: str = "python scripts/lint_agent_frontmatter.py --hook pre-tool-use"

PREEXISTING_HOOKS: tuple[tuple[str, str, str], ...] = (
    ("SessionStart", "*", "python scripts/harness_session_start.py"),
    (
        # ADR-0021 / spec-0017: replaced the dead `$CLAUDE_TOOL_INPUT_path`
        # interpolation (Claude Code delivers hook input as stdin JSON, never
        # as a per-field env var) with the real `--hook pre-tool-use` stdin
        # mode, and widened the matcher to cover NotebookEdit.
        "PreToolUse",
        "Edit|Write|NotebookEdit",
        PRE_TOOL_USE_HOOK_COMMAND,
    ),
    (
        # Same stdin-JSON fix applied to the ruff-autofix hook, which had the
        # identical defect.
        "PostToolUse",
        "Edit|Write",
        # spec-0020: `ruff format` runs beside `check --fix` on the same emitted
        # path. Formatting is the one thing a per-file autofix hook genuinely
        # could not do before, so drift surfaced only at `make gate`. `-I{}`
        # replaces the bare `xargs` so both commands see the same argument.
        "python scripts/lint_agent_frontmatter.py --hook post-tool-use --emit-path "
        '| xargs -r -I{} sh -c \'python -m ruff check --fix "{}" >/dev/null 2>&1; '
        'python -m ruff format "{}" >/dev/null 2>&1\' || true',
    ),
    (
        # spec-0023 R8: `|| exit 1` replaced `|| true`. The recorded rationale
        # for swallowing ("so a failure never strands a session") did not hold:
        # only exit code 2 blocks a Stop hook, so any other non-zero was
        # already a *visible, non-blocking* notice. What `|| true` actually did
        # was downgrade that notice to a transcript-only line — and once the
        # zero-skip guard landed (spec-0022 R8, which turns a green run red by
        # mutating session.exitstatus) it was swallowing precisely the signal
        # the guard exists to raise. `exit 1` rather than bare propagation
        # because pytest exits 2 on a collection error, and 2 *would* block.
        # This is also the compensating control for the PostToolUse matcher
        # being Edit|Write only: nothing can know which files a Bash command
        # wrote, so `format-check` at turn end is the net that catches them.
        #
        # spec-0020: `make typecheck format-check` added ahead of the suite.
        # Measured at 0.3s warm, and mypy catches cross-file type breakage that
        # neither the per-file ruff hook nor pytest sees.
        #
        # `--cov` was considered and rejected: it costs +14s per turn end
        # (20.6s -> 35.0s) and would not have caught any of the three coverage
        # defects this repo has hit. Two were glob problems visible only in the
        # config, and the exclusion bug made the percentage go *up*. Delegates
        # to `make` for the same reason CI does — one definition of each check.
        "Stop",
        "*",
        "make typecheck format-check || exit 1 ; python -m pytest -q --no-cov || exit 1",
    ),
    (
        # spec-0022 R11: the same stdin-JSON pre-tool-use mode, registered a
        # second time under the Bash matcher. The mode discriminates by
        # payload shape (`tool_input.command` vs `file_path`), so one command
        # serves both matchers; for Bash it emits a mention-level advisory
        # `ask` on protected paths — never `deny`, per ADR-0021.
        "PreToolUse",
        "Bash",
        PRE_TOOL_USE_HOOK_COMMAND,
    ),
)

# rtk (rtk-ai/rtk): Bash-output compaction, wired as a PreToolUse hook.
RTK_HOOK_EVENT: str = "PreToolUse"
RTK_HOOK_MATCHER: str = "Bash"
RTK_HOOK_COMMAND_FRAGMENT: str = "rtk hook claude"
# Presence guard. Without it the hook exits 127 ("rtk: not found") on every
# Bash tool call for contributors who haven't installed the binary — Claude
# Code treats that as non-blocking, so the call still runs, but it emits a
# hook-error notice each time. The guard makes the skip silent.
RTK_BINARY_GUARD_FRAGMENT: str = "command -v rtk"
# Claude Code has no per-hook disable (``disableAllHooks`` would also drop the
# PREEXISTING_HOOKS above), so an env-var gate is the only per-contributor
# opt-out. ``env`` values layer across settings files; hook arrays do not.
RTK_DISABLE_ENV: str = "MANGOMAS_DISABLE_RTK_HOOK"
RTK_TELEMETRY_DISABLED_ENV: str = "RTK_TELEMETRY_DISABLED"
# Shared truthy/falsey markers for the `env`-block flags above.
ENV_FLAG_ON: str = "1"
ENV_FLAG_OFF: str = "0"

# ── Agent corpus contract (spec-0018 / ADR-0024) ──────────────────────────────
# Shared prefix on every tracked agent. It is what separates the committed
# corpus from a contributor's own agents in the same flat directory, and what
# keeps agent and skill names from colliding as both corpora grow.
AGENT_SLUG_PREFIX: str = "mango-"
CLAUDE_AGENTS_DIR_RELPATH: str = ".claude/agents"
RETIRED_AGENTS_DIR_RELPATH: str = ".github/agents"
# Five dormant `agent.md` files sat in the source tree, all containing claims
# that were false rather than stale. Two earned promotion to a nested
# CLAUDE.md; the other three were deleted as skill duplicates. Nothing may
# reintroduce the convention — a file nothing loads cannot be kept honest.
RETIRED_STRAY_AGENT_FILENAME: str = "agent.md"

# The 25 agents, by slug. Set equality, so a change names what appeared or
# vanished and editing this tuple is the review record.
EXPECTED_AGENT_SLUGS: tuple[str, ...] = (
    "mango-adr-author",
    "mango-agent-impl-dev",
    "mango-api-dev",
    "mango-api-impl-dev",
    "mango-architect",
    "mango-backend",
    "mango-ci-dev",
    "mango-cli-dev",
    "mango-error-taxonomy-dev",
    "mango-eval-dev",
    "mango-fake-builder",
    "mango-harness-dev",
    "mango-hypothesis-fuzz",
    "mango-integration-runner",
    "mango-layering-auditor",
    "mango-llm-adapter-dev",
    "mango-orchestrator-dev",
    "mango-pr-watcher",
    "mango-protocol-auditor",
    "mango-rag-dev",
    "mango-schema-evolution",
    "mango-secrets-dev",
    "mango-sse-streamer",
    "mango-storage-adapter-dev",
    "mango-telemetry-exporter-dev",
    "mango-test-engineer",
    "mango-workflow-graph-dev",
)
# Routers are the only agents that carry trigger conditions, so they are the
# only descriptions auto-delegation can match. They cannot write.
ROUTER_AGENT_SLUGS: frozenset[str] = frozenset(
    {"mango-architect", "mango-backend", "mango-api-dev", "mango-test-engineer"}
)
# Agents holding Edit and/or Write. A reviewed-change gate, NOT a substitute
# for a deny rule: it covers 18 of 25 and only fails when the set changes.
WRITE_CAPABLE_AGENT_SLUGS: frozenset[str] = frozenset(
    {
        "mango-adr-author",
        "mango-agent-impl-dev",
        "mango-api-impl-dev",
        "mango-ci-dev",
        "mango-cli-dev",
        "mango-error-taxonomy-dev",
        "mango-eval-dev",
        "mango-fake-builder",
        "mango-harness-dev",
        "mango-hypothesis-fuzz",
        "mango-integration-runner",
        "mango-llm-adapter-dev",
        "mango-orchestrator-dev",
        "mango-rag-dev",
        "mango-schema-evolution",
        "mango-secrets-dev",
        "mango-sse-streamer",
        "mango-storage-adapter-dev",
        "mango-telemetry-exporter-dev",
        "mango-workflow-graph-dev",
    }
)
# Agents whose declared surface includes a protected core contract. Each must
# say so, because the CI trailer gate will otherwise fail their first commit.
PROTECTED_PATH_OWNER_SLUGS: frozenset[str] = frozenset(
    {
        "mango-error-taxonomy-dev",
        "mango-orchestrator-dev",
        "mango-schema-evolution",
        "mango-hypothesis-fuzz",
    }
)
# The description is the entire routing surface and loads at every session
# start. 320 binds on the corpus as written; 400 would bind on nothing.
AGENT_DESCRIPTION_MAX_CHARS: int = 320
# Ceiling on token overlap between two *router* descriptions. Routers are the
# only auto-delegated agents, so they are the only pair that can compete. A
# ratchet, stated honestly: the observed maximum is 0.208, so this binds on
# nothing today and exists to stop drift.
MAX_ROUTER_DESCRIPTION_JACCARD: float = 0.30
# Trigger-condition wording. Banned outside routers: "invoke explicitly when X"
# is not a control, because auto-delegation matches X and ignores the verb.
AGENT_TRIGGER_PHRASE_PATTERN: str = r"use when|when you|whenever"
# Wording retired with the parent/child hierarchy.
RETIRED_AGENT_PREFIX: str = "Sub-agent of"
# Minimum ADR/spec references across the corpus. Set from what the corpus
# actually contains (ADR-0013/0014/0016 and `spec 0012`) rather than an
# aspiration — note the space in `spec 0012`, which a `spec-\\d{4}` regex misses.
MIN_CORPUS_TRACEABILITY_REFS: int = 4

# ── R7: skills own procedure, agents own a surface (spec-0018) ────────────────
# Agents whose surface an existing skill already documents. Such an agent must
# name its skill and must not restate the recipe: before this mapping,
# mango-llm-adapter-dev duplicated ~45 of its 64 body lines from mango-adapter,
# and mango-telemetry-exporter-dev had copied ~26 lines of mango-deploy while
# citing no skill at all.
#
# Authored, not derived — a reviewer should check the pairings rather than
# trust them. Routers and auditors are deliberately absent: their numbered
# steps are their own operating loop, not a recipe a skill owns.
AGENT_SKILL_OWNERS: dict[str, tuple[str, ...]] = {
    "mango-adr-author": ("mango-release",),
    "mango-agent-impl-dev": ("mango-agent-add", "mango-cognitive"),
    "mango-api-impl-dev": ("mango-observability", "mango-config"),
    "mango-ci-dev": ("mango-deploy", "mango-mutation-proof"),
    "mango-error-taxonomy-dev": ("mango-error",),
    "mango-eval-dev": ("mango-eval",),
    "mango-fake-builder": ("mango-testing",),
    "mango-harness-dev": ("mango-harness",),
    "mango-hypothesis-fuzz": ("mango-testing",),
    "mango-integration-runner": ("mango-testing",),
    "mango-llm-adapter-dev": ("mango-adapter",),
    "mango-orchestrator-dev": ("mango-topology", "mango-observability"),
    "mango-pr-watcher": ("mango-release",),
    "mango-rag-dev": ("mango-rag",),
    "mango-schema-evolution": ("mango-agent-add",),
    # Two skills, deliberately: `mango-adapter` supplies the Protocol-first
    # contract and the fake pattern, but its "register the factory" rule and
    # its async-methods rule are both wrong for this surface (the secrets
    # registry stores instances, and the protocol is sync-only). `mango-config`
    # is what actually documents the SecretsProvider seam.
    "mango-secrets-dev": ("mango-adapter", "mango-config"),
    "mango-sse-streamer": ("mango-topology",),
    "mango-storage-adapter-dev": ("mango-adapter",),
    "mango-telemetry-exporter-dev": ("mango-observability", "mango-deploy"),
    "mango-workflow-graph-dev": ("mango-workflow", "mango-observability"),
}
# Agents deliberately outside `AGENT_SKILL_OWNERS`, so the mapping can be
# checked for totality: a new agent must land in one set or the other, never
# fall through both unnoticed. Two reasons appear here, and both are decisions
# rather than omissions:
#
#   * the four routers and the two auditors have no file surface at all — they
#     read and advise, and their numbered steps are their own operating loop,
#     not a recipe any skill owns (which is also why
#     `test_mapped_agent_has_no_procedure_section` is scoped to mapped agents);
#   * `mango-cli-dev` owns a real surface that no skill documents, so its
#     workflow is genuinely its own rather than a restated recipe.
#
# Adding a slug here is therefore a claim that no skill documents its
# procedure. `test_every_agent_is_mapped_or_recorded_unmapped` enforces the
# partition; `test_mapped_agent_references_its_skill` enforces the other half.
# Top-level entries under `src/mangomas/` that no write-capable agent claims in
# its `## Surface You Own` section. `registry.py` is deliberate rather than an
# omission: it is a protected path holding a generic `Registry[T]` with no
# project-specific logic, consumed equally by the agent, eval, workflow,
# secrets and node registries. Handing it to any one of those owners would be
# arbitrary, and a change to it is a cross-cutting contract change that needs a
# `BREAKING-CHANGE` trailer and an architecture review, not a surface owner.
#
# Everything else must be claimed. `test_every_source_surface_has_a_write_capable_owner`
# derives ownership from the agent bodies themselves rather than a second
# hand-maintained table, so the corpus cannot desync from its own claims.
UNOWNED_SOURCE_SURFACES: frozenset[str] = frozenset({"registry.py"})

# ── Corpus-count claims in prose ─────────────────────────────────────────────
#
# Docs that describe the corpus as it is *now*. A number in one of these is a
# claim about the live tree and must agree with it; the README said "13 skills,
# 23 agents (4 routers + 19 specialists)" while the tree held 15 and 27.
#
# `NEXT_STEPS.md` is deliberately absent. It is a dated delivery log whose
# per-milestone counts are correct *as of that milestone* and must not be
# rewritten — its own preamble says "counts below are as-of the harness
# branch". Rewriting them would falsify the record rather than fix drift.
# `docs/adr/` and `docs/plans/` are excluded for the same reason.
LIVE_CORPUS_COUNT_DOCS: tuple[str, ...] = (
    "CLAUDE.md",
    "README.md",
    "docs/tooling/claude-code-ecosystem.md",
)
# Numbers written as words, which the corpus docs use in prose.
#
# "one" is deliberately absent. In English it doubles as an article — "dispatch
# one agent", "one skill owns the procedure" — so treating it as a count claim
# produces false positives on ordinary prose, and no doc will ever truthfully
# claim this corpus holds a single agent. Every other word is unambiguous
# because it forces a plural noun.
SPELLED_NUMBERS: dict[str, int] = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
}
# A count claim whose noun is a corpus noun but whose subject is a *subset*.
# Keyed by a distinctive phrase on the line; the value names the roster the
# number must equal. Registered rather than exempted — a subset count is still
# a claim, and this keeps it pinned to something real.
SUBSET_COUNT_CLAIMS: dict[str, str] = {
    "own a **protected path**": "PROTECTED_PATH_OWNER_SLUGS",
}
SKILL_UNMAPPED_AGENT_SLUGS: frozenset[str] = frozenset(
    {
        "mango-api-dev",
        "mango-architect",
        "mango-backend",
        "mango-cli-dev",
        "mango-layering-auditor",
        "mango-protocol-auditor",
        "mango-test-engineer",
    }
)
# The four protected-path owners additionally reference the governance skill.
HARNESS_SKILL_SLUG: str = "mango-harness"
# Heading a mapped agent may not carry: its procedure belongs to its skill.
# Unmapped agents keep theirs.
PROCEDURE_SECTION_HEADING: str = "## Workflow"
# Canonical `##` vocabulary for agent bodies. 56 distinct headings existed
# before this, including three spellings of "surface you own", which made the
# corpus unscannable and let duplicated sections hide under new names.
# Nine, not seven: agents whose surface is a *process* rather than a file tree
# (the auditors, mango-pr-watcher) need `## Checklist` and `## Decision Table`
# to say what they actually do. Each of the nine has a distinct meaning, which
# is the property that matters — an ad-hoc name is where a duplicated section
# hides.
AGENT_SECTION_HEADINGS: frozenset[str] = frozenset(
    {
        "## Surface You Own",
        "## Protected path",
        "## Invariants",
        "## Constraints",
        "## Checklist",
        "## Decision Table",
        "## Diagnosing Failures",
        "## Output Format",
        "## Workflow",
    }
)
# `### Breaking Changes` was prescribed in four places and used in CHANGELOG.md
# zero times — it is not a Keep a Changelog section, which is the format the
# CHANGELOG declares. The enforced mechanism is the commit trailer.
RETIRED_CHANGELOG_HEADING: str = "### Breaking Changes"

# Claude Code permission rules in `.claude/settings.json`. Pinned by set
# equality: nothing else in the suite asserted anything about `permissions`, so
# a rule could be dropped, or added unreviewed, in total silence.
EXPECTED_DENY_RULES: frozenset[str] = frozenset(
    {
        "Bash(rm -rf:*)",
        "Bash(git push --force:*)",
        "Bash(git push -f:*)",
        # The two settings files, not `.claude/**`. The rationale for denying
        # anything here is that nothing legitimately needs Claude Code to
        # rewrite its own permissions mid-session — and that argues for these
        # two files, not the whole tree. A `.claude/**` rule would also block
        # every edit to `.claude/skills/`, which is ordinary authoring work and
        # is exactly what the next planned change does.
        "Edit(/.claude/settings.json)",
        # Gitignored, still loaded, and able to add `permissions.allow` entries
        # — so it is the more useful of the two to deny.
        "Edit(/.claude/settings.local.json)",
        # spec-0022 R4: MCP filesystem-write and git-mutation tools bypass both
        # the `Edit|Write|NotebookEdit` PreToolUse matcher and the `Edit(...)`
        # deny rules — the MCP half of the gap ADR-0021 explicitly concedes.
        # The permission layer is the only in-session-authoritative one, so the
        # deny lands here. File edits and git mutations keep their first-class,
        # harness-audited channels (native Edit/Write and Bash git).
        "mcp__filesystem__write_file",
        "mcp__filesystem__edit_file",
        "mcp__filesystem__move_file",
        "mcp__filesystem__create_directory",
        "mcp__git__git_commit",
        "mcp__git__git_add",
        "mcp__git__git_reset",
        "mcp__git__git_checkout",
        "mcp__git__git_create_branch",
        "mcp__git__git_init",
    }
)
# Every MCP-tool deny rule is `mcp__<server>__<tool>`; the server segment must
# name an adopted server or the rule is silently inert (a dead control that
# reads like a live one — the same defect class as the interior-`*` Bash rule).
MCP_DENY_RULE_PREFIX: str = "mcp__"
# Deny rules Claude Code consults for a *file write*. `Edit(...)` covers Edit,
# Write and NotebookEdit; nothing here stops a `Bash` heredoc or `>` redirect,
# so these rules are cheap and partial rather than airtight.
PATH_SCOPED_DENY_RULE_PREFIX: str = "Edit("
# A leading `/` anchors a rule at the settings file's directory (the project
# root). Without it the rule is cwd-relative and silently stops matching when
# a session starts from a subdirectory.
ANCHORED_RULE_PATH_PREFIX: str = "/"
# Claude Code honours exactly one wildcard in a Bash rule: a trailing `:*` after
# the command prefix. An interior `*` is matched as a literal character, so
# `Bash(python -m ruff *:*)` never matched `python -m ruff check --fix` and the
# call prompted on every run — a dead allow-rule that looks live.
BASH_RULE_PREFIX: str = "Bash("
BASH_RULE_WILDCARD_SUFFIX: str = ":*"
# Rule heads Claude Code accepts but never consults for a file write, emitting a
# startup warning instead. Only `Edit(...)` is matched, and it already covers
# Edit, Write and NotebookEdit — so a `Write(...)` rule is a non-control that
# reads like one.
INERT_FILE_RULE_PREFIXES: tuple[str, ...] = ("Write(", "NotebookEdit(")

# MCP servers adopted by ADR-0020. Upstream `memory` (redundant with
# claude-mem), `everything` (test/demo), and `time` (low value here) are
# deliberately excluded — the test asserts an exact set so an unreviewed
# addition fails.
ADOPTED_MCP_SERVERS: frozenset[str] = frozenset(
    {"filesystem", "git", "fetch", "sequential-thinking", "repomix", "github"}
)
# The only adopted server needing a credential, and the only one that is
# optional: without docker or a token it fails to start and the other five are
# unaffected. The npm `@modelcontextprotocol/server-github` package was NOT
# used — upstream deprecated it ("Package no longer supported"), so the npx
# form every other server here uses is not available for this one.
CREDENTIALED_MCP_SERVERS: frozenset[str] = frozenset({"github"})
# Any secret an MCP server needs must arrive as a `${VAR}` interpolation. A
# literal value in this shared, checked-in file would be a committed
# credential, and cloud sessions load it with no approval prompt.
ENV_INTERPOLATION_PREFIX: str = "${"
# Servers that take a path argument and must be pinned to the project root
# rather than granted unscoped filesystem/git reach.
PATH_SCOPED_MCP_SERVERS: tuple[str, ...] = ("filesystem", "git")
# `${VAR:-default}` form: without the default an unset variable is passed
# through as a literal string rather than failing, which is the dangerous case.
MCP_PROJECT_DIR_SCOPE: str = "${CLAUDE_PROJECT_DIR:-.}"

__all__ = [
    "ADOPTED_MCP_SERVERS",
    "AGENT_DESCRIPTION_MAX_CHARS",
    "AGENT_SECTION_HEADINGS",
    "AGENT_SKILL_OWNERS",
    "AGENT_SLUG_PREFIX",
    "AGENT_TRIGGER_PHRASE_PATTERN",
    "ANCHORED_RULE_PATH_PREFIX",
    "BASH_RULE_PREFIX",
    "BASH_RULE_WILDCARD_SUFFIX",
    "CLAUDE_AGENTS_DIR_RELPATH",
    "CLAUDE_SETTINGS_LOCAL_EXAMPLE_RELPATH",
    "CLAUDE_SETTINGS_RELPATH",
    "CLAUDE_SKILLS_DIR_RELPATH",
    "CORPUS_DOC_RELPATHS",
    "CREDENTIALED_MCP_SERVERS",
    "ENV_FLAG_OFF",
    "ENV_FLAG_ON",
    "ENV_INTERPOLATION_PREFIX",
    "EXPECTED_AGENT_SLUGS",
    "EXPECTED_DENY_RULES",
    "EXPECTED_SKILL_SLUGS",
    "HARNESS_CONFIG_AUDIT_MODE_ENV",
    "HARNESS_SKILL_SLUG",
    "INERT_FILE_RULE_PREFIXES",
    "INVALID_AGENT_MODEL_VALUES",
    "INVALID_AGENT_TOOL_TOKENS",
    "LEGACY_AGENT_FIELDS",
    "LIVE_CORPUS_COUNT_DOCS",
    "MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS",
    "MALFORMED_SKILL_FRONTMATTER_SHORT_DESCRIPTION",
    "MAX_ROUTER_DESCRIPTION_JACCARD",
    "MCP_CONFIG_RELPATH",
    "MCP_DENY_RULE_PREFIX",
    "MCP_PROJECT_DIR_SCOPE",
    "MIN_CORPUS_TRACEABILITY_REFS",
    "MISSING_TOOLS_AGENT_SLUG",
    "PATH_SCOPED_DENY_RULE_PREFIX",
    "PATH_SCOPED_MCP_SERVERS",
    "PLANNER_SIGNAL_GOAL",
    "PLANNER_SIGNAL_REPLY",
    "PLANNER_SIGNAL_STEPS",
    "POLICY_REJECTED_AGENT_FIELDS",
    "PREEXISTING_HOOKS",
    "PRE_TOOL_USE_HOOK_COMMAND",
    "PROCEDURE_SECTION_HEADING",
    "PROTECTED_PATH_OWNER_SLUGS",
    "RETIRED_AGENTS_DIR_RELPATH",
    "RETIRED_AGENT_PREFIX",
    "RETIRED_CHANGELOG_HEADING",
    "RETIRED_SKILLS_DIR_RELPATH",
    "RETIRED_STRAY_AGENT_FILENAME",
    "REVIEWER_SIGNAL_FEEDBACK",
    "REVIEWER_SIGNAL_REMEDIATION",
    "REVIEWER_SIGNAL_REPLY",
    "ROUTER_AGENT_SLUGS",
    "RTK_BINARY_GUARD_FRAGMENT",
    "RTK_DISABLE_ENV",
    "RTK_HOOK_COMMAND_FRAGMENT",
    "RTK_HOOK_EVENT",
    "RTK_HOOK_MATCHER",
    "RTK_TELEMETRY_DISABLED_ENV",
    "SCOPED_DELEGATION_TOOL_SPEC",
    "SIGNAL_CUSTOM_POLICY_ID",
    "SIGNAL_DIR_ENV",
    "SIGNAL_ENABLED_ENV",
    "SIGNAL_GENAI_SPANS_ENV",
    "SIGNAL_HTTP_TIMEOUT_ENV",
    "SIGNAL_HTTP_URL_ENV",
    "SIGNAL_MOCK_INGEST_URL",
    "SIGNAL_POLICY_ID_ENV",
    "SIGNAL_POLICY_SNAPSHOT_HASH_ENV",
    "SIGNAL_POLICY_VERSION_ENV",
    "SIGNAL_SCHEMA_VERSION_ENV",
    "SKILL_UNMAPPED_AGENT_SLUGS",
    "SPELLED_NUMBERS",
    "SUBSET_COUNT_CLAIMS",
    "UNOWNED_SOURCE_SURFACES",
    "VALID_AGENT_FRONTMATTER",
    "VALID_AGENT_TOOL_TOKENS",
    "VALID_CLAUDE_AGENT_FRONTMATTER",
    "VALID_CLAUDE_AGENT_SLUG",
    "VALID_MCP_TOOL_NAME",
    "VALID_SKILL_FRONTMATTER",
    "WRITE_CAPABLE_AGENT_SLUGS",
]
