# ADR-0034: Structured acceptance predicates

## Status

Proposed

## Context

`PredicateSpec` (ADR-0011, ADR-0016) offers `contains` and `regex`; both compile to
a closure over `response.content`, matching serialised **text**. `ReviewerAgent` and
`PlannerAgent` emit structured JSON, so a loop iterating a reviewer until it approves
can only text-match its serialised self-report. Probes against the real classes fail
both ways: `'"passed": true'` never matches `model_dump_json()`'s compact
`'"passed":true'` — raising `MaxStepsExceeded` on an *approving* review — while the
quoteless `'passed:true'` reached for next also matches inside
`feedback`/`suggestions`, accepting a *rejecting* one. `score >= 0.8` is
inexpressible. The trap: the obvious fix for the false negative manufactures the
false positive.

## Decision

Add a third kind, `json_field`: parse the response as a JSON object and test a
named field, binding acceptance to parsed structure rather than serialised text.

1. **Strict parsing** — reuse `core.structured.parse_llm_json_object`, not
   `parse_or_recover`. `StructuredOutputAgent.parse`, behind `VALIDATE_OUTPUT`, uses
   the strict `model_validate_json`; a recovering predicate would accept
   fence-wrapped JSON that `validate_output` rejects, so the two guards could not
   agree by construction.
2. **Total function** — catch `LLMBadResponse`, return `False`. An unparseable
   intermediate means "not accepted yet", not "abort with a 502"; exhaustion still
   raises `MaxStepsExceeded` (422), so protected `errors.py` is untouched.
3. **Dotted-path addressing now** — every schema is flat today, but adding paths
   later would make `"a.b"` ambiguous between a literal key and a path. Resolution
   walks mappings only; a missing key yields `False`, never an exception.
4. **Exactly one comparison** — `equals`, `at_least` or `at_most`; zero or several
   is `ConfigError` at compile time, as `_resolve_flags` already rejects a bad flag
   before first call. Booleans are excluded from numeric comparison (`True >= 0.5`
   is otherwise silently true).

## Consequences

### Positive

- Structured agents become loopable and routable with no needle tuned to a
  serialiser's whitespace.
- The predicate and `VALIDATE_OUTPUT` agree by construction — both strict.
- `BranchCase.when` is typed `PredicateSpec` exactly as `LoopNode.accept` is, so
  branch routing gains the kind unchanged.

### Negative / Trade-offs

- `PredicateSpec` stops being one flat shape; per-kind validation widens its
  compile-time error surface.
- Every loop step re-parses, and prose-wrapped JSON never matches.

### Neutral

- `value` relaxes to optional so existing graphs still validate. That removes a
  guard, so a per-kind validator restores it: `contains`/`regex` require a
  non-empty `value`; `json_field` requires `field` plus one comparison and rejects
  `value`/`case_sensitive`/`flags`. `frozen=True` and `extra="forbid"` unchanged.
- No `Settings` addition — a JSON grammar is not an operator tunable.

## Alternatives Considered

- **JSONPath / JMESPath** — rejected: a runtime dependency for a one-level need.
- **Raising on malformed JSON** — rejected per decision 2.
- **Documenting "don't loop on structured agents"** — rejected: the failure is
  silent both ways, and prose cannot make a substring match correct.

## References

- Code: `src/mangomas/workflow/predicate.py`, `src/mangomas/workflow/graph.py`,
  `src/mangomas/core/structured.py`, `src/mangomas/agents/_structured.py`
- Spec: `specs/0032-structured-acceptance-predicates.md`
- Related ADRs: ADR-0011 (workflow graphs), ADR-0016 (branch node), ADR-0027
  (loop semantics, `MaxStepsExceeded` as the non-convergence signal)
