# Spec-0032: Structured acceptance predicates (`json_field`)

- **Status:** In progress
- **Linked ADR:** ADR-0034
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`workflow/predicate.py` offers two predicate kinds, `contains` and `regex`, and
both compile to a match against `response.content` — the response **text**.
`PredicateSpec` backs `LoopNode.accept` and `BranchCase.when` alike, so every
declarative acceptance and every branch route is a text match.

The structured agents do not emit text. `ReviewerAgent` emits
`ReviewResult(passed: bool, score: float, feedback: str, suggestions: list[str])`
and `PlannerAgent` emits `ExecutionPlan`. A workflow that loops a reviewer until
it approves can therefore only text-match the reviewer's serialised self-report,
and that fails in both directions. Measured against the real classes:

| Needle | Response | Truth | Predicate | Verdict |
|---|---|---|---|---|
| `"passed": true` | `model_dump_json()` → `{"passed":true,…}` | accept | **reject** | false negative |
| `"passed":true` | pretty-printed (`indent=2`) | accept | **reject** | false negative |
| `passed:true` | rejecting review whose `feedback` mentions it | reject | **accept** | false positive |
| `passed:true` | rejecting review whose `suggestions` echo it | reject | **accept** | false positive |

The false negatives are the likely first encounter: a human writes the JSON
fragment with a space after the colon, `model_dump_json()` emits it without one,
and the loop runs to `max_steps` and raises `MaxStepsExceeded` even though the
reviewer approved. The false positives are what the obvious fix produces —
dropping the quotes to survive formatting drift makes the needle match prose
inside `feedback` and `suggestions`, accepting a rejecting review.

A needle **with** quotes cannot bleed into a string value, because JSON escapes
interior quotes. The defect is not that a substring match is naive; it is that
no spelling of a substring match is correct here, and the two wrong spellings
fail in opposite directions.

Separately, `score >= 0.8` is inexpressible at all: there is no numeric
comparison in the predicate vocabulary.

## Requirements

- **R1** — A third predicate kind, `json_field`, parses `response.content` as a
  JSON object and tests one named field, so acceptance binds to parsed
  structure rather than to serialised text.
- **R2** — Field addressing is a dotted path over mappings; `"passed"` is the
  one-segment case. A missing key, a non-mapping intermediate, or a path that
  runs off the end evaluates to "not accepted" — never an exception.
- **R3** — Exactly one comparison per spec: `equals` (JSON scalar),
  `at_least` or `at_most` (numeric). Zero or more than one is `ConfigError`
  at compile time, not per call.
- **R4** — Booleans are excluded from numeric comparison. `True >= 0.5` is
  otherwise silently true in Python, which would let a boolean field satisfy a
  score threshold.
- **R5** — Parsing is **strict** (whole-text, JSON object required), reusing
  `mangomas.core.structured.parse_llm_json_object`. Not `parse_or_recover`:
  `StructuredOutputAgent.parse` — the implementation behind
  `MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT` — is strict, and a recovering
  predicate would accept what `validate_output` rejects.
- **R6** — The compiled predicate is **total**: it catches `LLMBadResponse`
  from the parse and returns `False`. Non-convergence continues to surface as
  `MaxStepsExceeded`, so `errors.py` and the HTTP status table are untouched.
- **R7** — Must remain **additive**. Default: nothing changes for a graph that
  does not name `json_field`. `contains` and `regex` keep their exact current
  semantics, including `case_sensitive` and `flags` handling.
- **R8** — No hard-coded literals: the path separator, the comparison-field
  names and the per-kind not-applicable field sets are module constants, and
  error messages derive from them.

## Scenarios (WHEN/THEN)

Acceptance semantics:

- WHEN `{"kind":"json_field","field":"passed","equals":true}` evaluates a
  `ReviewResult` with `passed=true` THEN it accepts, **regardless of
  serialisation whitespace** (compact or pretty) — the two false negatives above.
- WHEN the same spec evaluates a `ReviewResult` with `passed=false` whose
  `feedback` or `suggestions` contain the text `passed:true` THEN it rejects —
  the two false positives above.
- WHEN `{"field":"score","at_least":0.8}` evaluates `score=0.9` THEN accept;
  `score=0.7` THEN reject.
- WHEN `{"field":"score","at_most":0.2}` evaluates `score=0.1` THEN accept.
- WHEN the addressed field is absent, the path traverses a non-mapping, or
  `content` is not a JSON object (invalid JSON, or a JSON array) THEN reject,
  and **no exception escapes** the predicate.
- WHEN `{"field":"passed","at_least":0.5}` evaluates `passed=true` THEN reject
  (R4) — a boolean is not a number for threshold purposes.

Config validation — both directions, per `specs/TEMPLATE.md`:

- WHEN a spec names `json_field` with no `field` THEN `ValidationError`; AND
  WHEN it names `field` plus exactly one comparison THEN it constructs.
- WHEN a spec names `json_field` with two comparisons THEN `ValidationError`;
  WHEN with zero THEN `ValidationError`.
- WHEN a spec names `json_field` together with `value`, `case_sensitive` or
  `flags` THEN `ValidationError` naming the not-applicable field.
- WHEN a `contains`/`regex` spec omits `value` or gives `""` THEN
  `ValidationError` — the guard the currently-required `value` field provides
  must survive its relaxation to optional.
- AND WHEN any predicate spelling valid before this change is re-validated
  THEN it still constructs and compiles to identical behaviour (R7).

## Config / env additions

_None._ A JSON grammar is a code-internal structure, not an operator tunable,
so nothing enters `Settings` and no `MANGOMAS_*` variable is added. The
constants required by R8 are module-level `_UPPER_CASE` names in
`workflow/predicate.py`, following the existing `_FLAG_BY_NAME` precedent.

## Protocol / contract impact

- New/changed protocols: _none_. `AcceptanceFn` stays
  `Callable[[AgentResponse], bool]` and the compiled closure stays synchronous.
- New error types: _none_. `ConfigError` (400) and `MaxStepsExceeded` (422) are
  reused; `errors.py` and `api/errors.py::_ERROR_STATUS` are untouched.
- Registry additions: _none_. `node_registry` is unchanged — `json_field` is a
  new value of an existing discriminated field, not a new node kind.
- Model change: `PredicateSpec` gains optional `field` / `equals` / `at_least` /
  `at_most`, and `value` relaxes from required to optional. `PredicateSpec` is
  reached through `LoopNode.accept` **and** `BranchCase.when`, so branch routing
  gains the same capability.

## Backwards-compatibility

- Every predicate spelling that validates today validates after this change and
  compiles to a byte-identical closure. `contains` keeps case-insensitive
  default matching; `regex` keeps its flag vocabulary and `search` semantics.
- Relaxing `value` to optional removes a schema-level guard, so a model
  validator restores it per kind: `contains`/`regex` require a non-empty
  `value`. Without this the change would silently widen what validates — the
  one real regression risk in this spec, and the reason the scenarios above
  assert the rejecting direction explicitly.
- `extra="forbid"` and `frozen=True` are retained.
- No deprecation: `contains` and `regex` remain fully supported. Matching text
  is correct for a text-producing agent; the docs steer structured agents to
  `json_field` rather than removing the alternative.

## Test plan

- Unit: `tests/test_workflow_predicate.py` — the four measured failure modes as
  regression tests, the R4 boolean trap, absent/non-mapping/non-object paths,
  every `ConfigError` and `ValidationError` arm, and the backwards-compat
  both-directions assertions.
- Graph level: `tests/test_workflow_graph.py` — a `json_field` spec round-trips
  through `LoopNode.accept` and `BranchCase.when`.
- Constants: new domain literals go in `tests/constants`, per the repo's
  no-magic-domain-values rule.
- Gated: _none_ — no external SDK, no network, no new extra.
- Gates: the four failure-mode tests are the two-sided proof. Each was observed
  failing against `contains` before `json_field` existed; each must pass after.
- Coverage: `workflow/**` holds its 95% floor and the global 95% gate holds.

## Acceptance criteria

- [ ] A graph that does not name `json_field` behaves identically (test proves it).
- [ ] `json_field` accepts/rejects per the scenarios above (tests prove both directions).
- [ ] No exception escapes a compiled predicate for any response content.
- [ ] `ruff`, `mypy --strict`, `pytest` (95% gate), `check_coverage.py`,
      `lint_agent_frontmatter.py` all clean.
- [ ] CHANGELOG updated; ADR-0034 added.
- [ ] `docs/workflow/graphs.md` and the `mango-workflow` skill document the kind
      and state why a substring match over a structured agent is unsound.
