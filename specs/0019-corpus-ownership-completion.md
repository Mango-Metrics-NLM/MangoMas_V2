# Spec-0019: Corpus ownership completion (the deferred "B5" agents)

- **Status:** In progress
- **Linked ADR:** _none — no boundary change._ ADR-0024 already settled the
  corpus's surface and permission posture; this spec applies that posture to
  four surfaces it left unowned, and adds no new mechanism.
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`docs/plans/20260809T133844Z-harness-corpus-decomposition-plan.md` defines a
deferred item **B5**: *"Owner agents for the unowned surfaces: `eval/`, `rag/`
+ `adapters/embeddings/` + `adapters/vector/`, `config.py`,
`src/mangomas/agents/`, `secrets/`, `cli/`, `harness/`. Four mature skills
already document these with no agent to reach for them."* It was never started
— `.claude/agents/` still holds exactly the 19 agents ADR-0024 made live, and
`AGENT_SKILL_OWNERS` maps nothing to `mango-rag`, `mango-eval` or
`mango-config`.

The consequence is asymmetric coverage rather than a missing feature. `eval/`
(38 files, four plugin registries) and the RAG stack (14 files across `rag/`
and two adapter packages) are among the largest subsystems in the tree and have
mature skills, but no agent whose stated surface includes them — so
`mango-backend`, whose own description lists RAG as in scope, routes RAG work
to no one. A separate audit found the same gap costs correctness: `tenancy.py`
is a complete ADR-0017 subsystem with a 100% coverage floor and **zero mentions
across all 19 agents and 13 skills**.

The intended outcome is that every substantial source surface has exactly one
named owner, so corpus maintenance can be delegated by name instead of done ad
hoc.

## Requirements

- **R1** — Four new specialist agents: `mango-rag-dev`, `mango-eval-dev`,
  `mango-secrets-dev`, `mango-agent-impl-dev`.
- **R2** — Each maps into `AGENT_SKILL_OWNERS`, names its skill in its body,
  and carries **no `## Workflow` section** (spec-0018 R7: skills own procedure,
  agents own a surface).
- **R3** — `mango-secrets-dev` maps to **both** `mango-adapter` and
  `mango-config`. `mango-adapter` alone is insufficient and partly wrong for
  this surface: its registration rule says "register the factory", but
  `secrets/registry.py` stores **instances**; its template is entirely
  `OpenAICompatHTTPClient`-shaped; and its "all public methods are `async def`"
  rule is contradicted by `secrets/provider.py`, which is sync-only by design.
  Mapping to `mango-adapter` alone would be a citation that misleads.
- **R4** — No two write-capable agents may claim the same file.
  `agents/_streaming.py` stays with `mango-sse-streamer`, which already claims
  it in both its description and its Surface table;
  `planner.py::ExecutionPlan` / `reviewer.py::ReviewResult` stay with
  `mango-schema-evolution`. `mango-agent-impl-dev` names those owners rather
  than claiming the files.
- **R5** — Every invariant a new agent states must be verifiable against
  current source. No invariant is written that could not be cited.
- **R6** — Two existing agents gain the missing `tenancy` surface:
  `mango-storage-adapter-dev` (the tenant row filter it already implements) and
  `mango-api-dev` (`TenancyMiddleware`, which its middleware list omits).
  `mango-backend`'s routing list gains the RAG and eval specialists it
  currently lacks.
- **R7** — New agents cite settings by **dotted import path**
  (`mangomas.config`), never by bare filename. Spec-0015 will turn `config.py`
  into a package; the dotted path survives that by construction, so this
  content never needs a follow-up edit.
- Additive by construction: the corpus is documentation. No source module,
  protocol, or runtime behaviour changes.

## Config / env additions

None — this spec adds no tunable.

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_
- Registry additions: _none_

The only contract touched is the **corpus contract** in
`tests/tooling/test_corpus_contract.py`, whose constants move:
`EXPECTED_AGENT_SLUGS` 19 → 23, `WRITE_CAPABLE_AGENT_SLUGS` 12 → 16,
`AGENT_SKILL_OWNERS` + 4 entries. `ROUTER_AGENT_SLUGS` and
`PROTECTED_PATH_OWNER_SLUGS` are unchanged — none of the four is a router, and
none owns a protected path.

## Backwards-compatibility

- No runtime behaviour changes at all; `src/` is untouched except for the
  unrelated surfaces already covered by their own commits.
- Adding an agent cannot break an existing one: Claude Code resolves an agent
  by its `name` field, and the four new slugs are disjoint from both the
  existing agent roster and the skill roster
  (`test_agent_and_skill_namespaces_are_disjoint`).
- Auto-delegation is unaffected: the four carry **no trigger conditions**, so
  they are reachable only by explicit name, exactly as the other 15 specialists
  are (`test_only_routers_carry_trigger_conditions`).

## Test plan

- Unit: the existing `tests/tooling/test_corpus_contract.py` suite covers all
  of it — roster set-equality, write-capability review gate, tool-token
  validity, description cap, trigger-phrase placement, canonical heading
  vocabulary, skill-reference requirement, and the no-`## Workflow` rule for
  mapped agents. Each is parametrised per slug, so the four new agents are
  covered the moment they land in `EXPECTED_AGENT_SLUGS`.
- `scripts/lint_agent_frontmatter.py` (via `make frontmatter`) validates
  frontmatter schema and tool tokens.
- Gated: not applicable — no external SDK.
- Coverage: no `src/` change, so no floor moves.

## Acceptance criteria

- [ ] Four agent files exist, each naming its mapped skill and carrying no
      `## Workflow` section.
- [ ] `tests/constants.py` updated in the same commit as each agent file — the
      roster count guard fails immediately otherwise.
- [ ] No file is claimed by two write-capable agents (R4).
- [ ] Every invariant in every new body resolves against current source (R5).
- [ ] `mango-storage-adapter-dev`, `mango-api-dev` and `mango-backend` updated
      for tenancy and routing (R6).
- [ ] `make gate` green: `ruff`, `mypy --strict`, `frontmatter`, `pytest`
      (95% gate + per-package floors).
- [ ] `CLAUDE.md` agent counts and specialist table updated; the
      "covers 12 of 19" docstring in `test_corpus_contract.py` updated.
- [ ] CHANGELOG updated under `[Unreleased]` › `Added`.
