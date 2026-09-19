# Spec-0034: Structured-acceptance enforcement

- **Status:** Implemented
- **Linked ADR:** _none — no boundary change._ `load_workflow` grows a keyword-only
  parameter with a behaviour-preserving default; `errors.py`, `_ERROR_STATUS`, the
  HTTP DTOs and every protocol are untouched.
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — supply-chain and acceptance guards (analysis 2026-09-19)`

## Problem

Spec-0032 added the `json_field` predicate kind because no substring spelling over
a structured agent's output is correct in both directions, and documented
`json_field` as the supported way to bind acceptance to a parsed field. It shipped
no mechanism for either half of that guidance, so two defects stayed loadable:

1. A `loop` over `reviewer` may still accept on `{"kind": "contains", "value":
   "approved"}`, which matches `{"passed": false, "feedback": "the approved
   approach was rejected"}` and terminates the loop on a **rejecting** review.
2. A `json_field` path that cannot resolve — a typo like `pased`, or a dotted path
   through a non-object like `steps.0.action` — validates, compiles, and returns
   `False` for every response. The loop exhausts `max_steps` and raises
   `MaxStepsExceeded`, so a misspelling is indistinguishable from a model that
   never converges.

Adopting spec-0032's own recommendation therefore trades a silent-acceptance bug
for a silent-never-accepts bug. This spec makes both refusable at load time, which
is the earliest point at which the agent's schema is known.

Source: `docs/analysis/20260919-council-rejection-and-replan.md` §1.1 and §N4.
Plan: `docs/plans/20260919T000000Z-toolchain-and-acceptance-parity-plan.md` PR C.

## Requirements

- R1 — A `loop` whose `agent` is a **structured** agent must not carry a text
  acceptance predicate (`contains` / `regex`). `ConfigError`, naming `json_field`
  as the supported alternative.
- R2 — A `json_field` predicate's first path segment must be a declared field of
  that agent's output schema. `ConfigError`, listing the available fields.
- R3 — A `json_field` predicate whose path has more than one segment must have an
  **object** first segment, because `predicate._resolve` walks `Mapping` values
  only. `ConfigError`, naming the offending segment and the object-valued fields.
- R4 — Every problem in a graph is reported together, not just the first.
- R5 — The rules apply to `LoopNode.accept` **only**, never `BranchCase.when`
  (see Non-goals).
- R6 — `mangomas.workflow` must not import `mangomas.agents`. The schema
  information arrives as plain data.
- R7 — The structured-agent set is **derived**, never enumerated: adding a
  structured agent must not require editing the derivation, and one that omits its
  schema declaration must fail loudly rather than ship unguarded.
- R8 — Entry-point discovered agents (`MANGOMAS_DISCOVERY_ENABLED`) receive the
  same guards on the HTTP surface.
- Must remain **additive & default-OFF**: `load_workflow(source)` with no
  `structured_agents` argument behaves exactly as before, and every previously
  valid graph still loads.

## Scenarios (WHEN/THEN)

Text predicates (R1), both directions:

- WHEN a `loop` over `reviewer` accepts on `contains` or `regex` THEN
  `load_workflow` raises `ConfigError` mentioning the self-report problem.
- WHEN a `loop` over `chat` (unstructured) accepts on `contains` THEN it loads —
  substring matching over prose is correct and must stay legal.

Path resolution (R2, R3), both directions:

- WHEN the first segment is absent from the schema (`pased`) THEN `ConfigError`,
  listing the available fields.
- WHEN every declared field of every structured agent is addressed singly THEN
  each loads — the derivation must not be built from the wrong part of the JSON
  schema.
- WHEN a dotted path's first segment is not an object (`steps.0.action`,
  `goal.length`) THEN `ConfigError` naming that segment.
- WHEN a dotted path's first segment **is** an object THEN it loads, so the rule
  is schema-derived rather than an unconditional ban on dotted paths.
- WHEN a single-segment path names a non-object (`steps`) THEN it loads — the
  predicate tests that value, it does not walk into it.

Traversal (R4) and default-off:

- WHEN a bad `loop` is nested under `sequence` → `fan_out` → `fan_out`, or reached
  through a `branch`'s `default`, THEN it is still refused — a two-level walk
  would miss it (ADR-0018 composite branches).
- WHEN two loops are bad THEN both problems are reported.
- WHEN `structured_agents` is `None` or `{}` THEN every rule is skipped and all
  three specs above load, proving the parameter is opt-in.

Derivation (R7, R8), both directions:

- WHEN a `StructuredOutputAgent` subclass declares no `schema` THEN composition
  import raises `ConfigError` naming the attribute.
- WHEN one declares `schema` THEN it appears in the map with no edit to the
  derivation.
- WHEN a discovered plugin instance subclasses `StructuredOutputAgent` THEN the
  instance-derived map covers it.
- WHEN a plain agent instance is passed to that derivation THEN it is omitted —
  including it would make every text predicate over `chat` a load error, the
  guard's most damaging false positive.

## Config / env additions

_None._ The guards are engaged by the caller passing `structured_agents`, not by a
setting: the three in-repo call sites always pass it, and no deployment knob can
turn a correctness check off. `MANGOMAS_WORKFLOW__*` is unchanged.

## Protocol / contract impact

- New/changed protocols: _none_. `StructuredOutputAgent` gains a `schema`
  `ClassVar` (additive; the positional `schema` constructor parameter is kept, so
  any third-party subclass calling `super().__init__(MyModel, "x")` is unaffected).
- New error types: _none_. Reuses `ConfigError`, already mapped to 400, so the API
  answers 400 and the CLI exits 2 with no `_ERROR_STATUS` change.
- Registry additions: _none_. `composition/agents.py`'s five `register` calls
  become a table, registering the identical five factories under the identical
  names.
- New public names: `mangomas.workflow.graph.iter_nodes`,
  `mangomas.workflow.validation` (`StructuredAgentSchema`, `StructuredAgentFields`,
  `structured_acceptance_problems`, `validate_structured_acceptance`),
  `mangomas.workflow.predicate` (`TEXT_MATCH_KINDS`, `KIND_JSON_FIELD`,
  `FIELD_PATH_SEPARATOR`), and `composition.agents` (`DEFAULT_AGENTS`,
  `STRUCTURED_AGENT_FIELDS`, `STRUCTURED_AGENT_FIELDS_EXTRAS_KEY`,
  `describe_schema`, `structured_agent_schemas`,
  `structured_agent_schemas_for_instances`). The two `composition` names reach the
  ADR-0019 facade, pinned by `tests/test_import_compat.py`.

## Non-goals

- **`BranchCase.when` is out of scope, by decision.** `BranchNodeExecutor`
  evaluates it against the node's *input* content — the last threaded message,
  which may be the original request or any upstream step's reply — so there is no
  agent whose schema could be bound to it. Inferring the upstream producer would
  be wrong as often as right (a branch may be a sequence's first step, or nested
  in a `fan_out`), and a false refusal is worse than no guard. Pinned by
  `test_a_text_predicate_on_branch_when_is_not_refused` so a later reader does not
  "complete" the guard.
- **Path segments beyond the second are not validated.** Doing so needs the nested
  model's own schema, which `StructuredAgentSchema` deliberately does not carry. A
  wrong deeper segment still degrades to "not accepted" at run time, as before.
- **CLI plugin coverage.** Both `workflow` commands load the graph *before*
  building an orchestrator, so a malformed graph exits 2 without paying for
  storage and LLM wiring. Keeping that fail-fast ordering costs coverage of a
  discovered structured agent on the CLI path; the HTTP routes, which accept a
  caller-supplied graph and always have an orchestrator in `app.state`, use the
  full map. Recorded in `_load_workflow_or_exit`'s docstring.
- **Runtime behaviour of `compile_predicate`.** It stays total: no response content
  makes the closure raise, and non-convergence keeps surfacing as
  `MaxStepsExceeded`. Every refusal here is load-time only.

## Backwards-compatibility

- `load_workflow(source)` — no `structured_agents` — is byte-identical to before.
  Pinned by `test_every_previously_valid_graph_still_loads_without_the_keyword`,
  which loads the three specs the new rules refuse.
- `examples/workflows/plan-execute-review.json` is unchanged. Its all-`agent`
  shape is the `dispatch_pipeline` parity proof that `test_plan_execute_review.py`,
  `test_plan_execute_review_mast.py`, `tests/integration/test_workflow_http_flow.py`
  and `tests/lmstudio/` depend on. The acceptance pattern is demonstrated by a
  **new** sibling, `plan-review-until-passed.json`.
- No deprecations.

## Test plan

- Unit: `tests/test_workflow_validation.py` (the rules, the traversal, the
  branch-scope decision, `iter_nodes`), `tests/composition/test_agents.py` (the
  table, the derivation, the plugin case, the late-binding guard),
  `tests/test_plan_review_until_passed.py` (the shipped example, loaded under the
  guard and executed both ways).
- Fakes: `FakeLLM` reused; no additions to `tests/fakes.py`.
- Gated: none — no external SDK.
- Gates: every rule proven in both directions per the Scenarios above; the
  dotted-path rule additionally has a synthetic object-field schema so it cannot
  pass as an unconditional ban.
- Coverage: `workflow/validation.py`, `workflow/graph.py`,
  `composition/agents.py` and `agents/_structured.py` at 100 % line and branch;
  global 95 % gate and the `workflow` / `composition` / `agents` floors met.

## Acceptance criteria

- [x] Feature off by default → no behaviour change (test proves it).
- [x] Feature on → documented behaviour, both directions (tests prove it).
- [x] `ruff`, `mypy --strict`, `pytest` (95 % gate), `frontmatter-lint`,
      `lint-imports` all clean.
- [x] CHANGELOG updated; no ADR owed (no boundary changed).
- [x] Discovered plugins covered on the HTTP surface (R8); the CLI limit is
      recorded rather than implied.
