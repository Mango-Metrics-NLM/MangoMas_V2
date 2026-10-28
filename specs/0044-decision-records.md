# Spec-0044: Decision records

- **Status:** Draft
- **Linked ADR:** ADR-0042 (decision records are a third stream, separate from turns and cognitive signals)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`
- **Delivery plan:** `docs/plans/20261027T220000Z-civ6-method-port-plan.md` (PR 2)

## Problem

MangoMas persists three things today: chat turns (`TurnRepository`), OTel
spans, and — flag-on — `CognitiveSignal` envelopes. None of them is a
record of *a decision and what came of it*: which configuration was chosen
for which kind of request, what score was predicted, what was observed.
That tuple is what AlphaZero's replay buffer stores (state, search output,
outcome) and it is the only input a future routing recommendation
(`routing.recommendation`, reserved in `mango_contracts`) could be learned
from. Without it, "learned routing in shadow mode" is a slogan. This spec
adds the record and its sink, nothing that reads it.

## Requirements

- New package `src/mangomas/decisions/` (pure; imports `mangomas.tenancy`,
  `mangomas.correlation`, Pydantic; **never** `mango_contracts`, never
  `cognitive/`, never `core/orchestrator/`):
  - `DecisionRecord` — Pydantic v2, frozen, `extra="forbid"`,
    `schema_version="1.0.0"`: `record_id`, `tenant_id`, `correlation_id`,
    `created_at`, `label: Literal["predicted","observed"]`,
    `state_digest: str` (sha256 over the canonical JSON of request
    messages + target/agent name + resolved options — **raw content is
    never stored**), `action: str` (agent / target / pipeline name),
    `context_kind: str` (`eval_row` | `dispatch` | …),
    `predicted: PredictedOutcome | None`, `observed: ObservedOutcome | None`,
    `refers_to: str | None` (an `observed` record cites the `predicted`
    record it settles; append-only, never an update),
    `evidence_refs: list[str]` (opaque ids, e.g. eval `row_id`).
    Validation: exactly one of `predicted` / `observed` is set and it
    matches `label`; `refers_to` is required when `label="observed"` and a
    predicted record exists in the same correlation (enforced by the sink,
    not the model — the model cannot see history).
  - `DecisionRecordSink` protocol (`async emit(record) -> None`,
    `async aclose()`), hung on `AgentContext.extras["decision_sink"]` —
    the spec-0030 pattern; `core/agent.py` is untouched.
  - `JsonlDecisionSink` writing `decisions.jsonl` under
    `MANGOMAS_DECISIONS__DIR`, one line per record, `asyncio.to_thread`
    for the write, failures contained and logged (a sink failure never
    fails a dispatch or an eval row).
- Authority boundary (INV-16 shape): a `DecisionRecord` MUST NOT be a PDP,
  broker, retry, routing, or gate input. The `cognitive/pdp.py` refuse-list
  gains the decision-record field names **as string literals in
  `cognitive/constants.py`** (so `cognitive` never imports `decisions`),
  and the bypass-inventory test gains `decisions/` (no `subprocess`, no
  writes outside the sink).
- Import direction, enforced by a new `[tool.importlinter]` `forbidden`
  contract in `pyproject.toml` (protected path; `BREAKING-CHANGE:
  pyproject.toml — import-linter contract for mangomas.decisions`):
  `mangomas.decisions` imports none of `eval`, `cognitive`, `workflow`,
  `rag`, `agents`, `api`, `core.orchestrator`. `eval` imports `decisions`
  only inside the flag-on branch (lazy), so the flag-off `sys.modules`
  scenario below holds and the existing independence contract is not
  widened.
- First emission point: `EvalRunner` emits one `observed` record per scored
  row when enabled (`context_kind="eval_row"`, `observed.score`,
  `observed.passed`, `evidence_refs=[row_id]`). The `predicted` emission at
  orchestrator dispatch is **milestone 2.2, deferred** until the
  2026-10-28 plan's B1 lands on the orchestrator client; this spec defines
  its shape so 2.2 is a wiring change, not a design change.
- Tenancy: `tenant_id` comes from the request-scoped `ContextVar`; a
  record whose `tenant_id` disagrees with the active tenant is refused by
  the sink (`PersistenceError`, contained). The JSONL sink partitions by
  tenant directory.
- Must remain **additive & default-OFF**: `MANGOMAS_DECISIONS__ENABLED=false`
  → no extras key, no `decisions` import on the dispatch or eval path,
  `report_payload` byte-identical.

## Scenarios (WHEN/THEN)

- WHEN the flag is off THEN `AgentContext.extras` has no `decision_sink`
  key and `sys.modules` has no `mangomas.decisions` after an eval run AND
  WHEN it is on THEN exactly one `observed` line per non-errored row is
  written.
- WHEN a record carries both `predicted` and `observed` THEN validation
  fails AND WHEN it carries exactly the one matching `label` THEN it
  parses.
- WHEN a record carries a top-level `allowed_tools`, `routing_override`,
  or `gate_passed` field THEN validation fails (authority-shaped extras,
  the spec-0030 list) AND WHEN those are absent THEN a valid record parses.
- WHEN the request content changes by one character THEN `state_digest`
  changes AND WHEN only `correlation_id` changes THEN it does not (digest
  covers state, not identity).
- WHEN the active tenant is `t1` and a record names `t2` THEN the sink
  refuses it and the eval row still completes AND WHEN they agree THEN the
  line lands under `<dir>/t1/decisions.jsonl`.
- WHEN the sink's directory is unwritable THEN the eval run completes with
  the same `EvalReport` as with the flag off and a logged, contained error
  AND WHEN it is writable THEN the report is identical *and* the file
  exists (the sink never changes the report).
- WHEN `cognitive/pdp.py` receives a dict containing `state_digest` or
  `observed` THEN it refuses (INV-16 adapter extended) AND WHEN the dict is
  clean THEN it passes through.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_DECISIONS__ENABLED` | `false` | Attach the decision sink and emit records |
| `MANGOMAS_DECISIONS__DIR` | `./data/decisions` | JSONL root; one subdirectory per tenant |
| `MANGOMAS_DECISIONS__SCHEMA_VERSION` | `1.0.0` | Record version; lower values rejected at Settings parse |
| `MANGOMAS_DECISIONS__DIGEST_ALGORITHM` | `sha256` | Digest for `state_digest`; allow-list `{sha256}` |

Constants: `DEFAULT_DECISIONS_ENABLED = False`, `DEFAULT_DECISIONS_DIR`,
`DEFAULT_DECISIONS_SCHEMA_VERSION`, `DEFAULT_DECISIONS_DIGEST_ALGORITHM`,
`DECISIONS_EXTRAS_KEY = "decision_sink"` (in `decisions/constants.py`,
importable flag-off without pulling Pydantic models, mirroring
`cognitive/constants.py`).

## Protocol / contract impact

- New protocol: `DecisionRecordSink` on `AgentContext.extras["decision_sink"]`
  (no new `AgentContext` field — `core/agent.py` stays protected;
  ADR-0042).
- New error types: _none_ — sink I/O is `PersistenceError`, contained.
- Registry additions: `decision_sink_registry` with provider key `jsonl`
  (Postgres sink deferred; it must not share the `turns` table that
  spec-0041 is about to give identity columns).

## Backwards-compatibility

- Flag-off: extras keys, `AgentResponse`, `EvalReport`, `report_payload`
  and the JSONL `signals.jsonl` of spec-0030 are byte-identical (snapshot +
  `sys.modules` tests).
- `DecisionRecord` 1.0.0 is a new wire shape; nothing reads it yet, so no
  consumer migration. A reader lands only with a spec of its own.

## Test plan

- Unit: `tests/decisions/` (model validation both directions, digest
  invariants, authority-shaped extras refusal, tenant refusal, contained
  sink failure), `tests/eval/test_runner_decision_records.py` (flag-off
  identity, one-line-per-row, report unchanged with sink failing).
  `tests/fakes.py` gains `FakeDecisionSink` (records in memory, optional
  `fail=True`).
- Hypothesis: digest is a pure function of canonical state; any
  authority-shaped key injected at top level or nested under `predicted` /
  `observed` metadata is refused.
- Bypass inventory: `tests/test_execution_bypass_inventory.py` extended to
  `src/mangomas/decisions`.
- Gates: both directions per scenario; mutation proofs named in PR body:
  (1) remove the one-of validator → both-set test red; (2) make the sink
  raise through → report-unchanged test red; (3) drop `decisions/` from
  the bypass inventory → the inventory's own self-check red.
- Coverage: new package floor 95 % (added to `scripts/check_coverage.py`
  and `coverage-floors` table); `eval` floor maintained.

## Acceptance criteria

- [ ] Feature off by default → no extras key, no import, no file (tests
  prove all three).
- [ ] Flag on → one `observed` JSONL line per scored eval row under the
  tenant directory; `EvalReport` unchanged (snapshot).
- [ ] One-of validation, authority-shaped refusal, tenant refusal, and
  contained sink failure each proved both directions and mutation-proved.
- [ ] INV-16 adapter refuses decision-record fields (test proves it).
- [ ] `ruff`, `mypy`, `pytest` (95 % gate), `frontmatter-lint`,
  bypass inventory all clean.
- [ ] CHANGELOG updated; ADR-0042 added; `AGENTS.md` config rows and
  architecture tree updated; `specs/README.md` row added.
