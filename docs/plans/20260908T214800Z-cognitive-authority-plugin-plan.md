# Cognitive/execution contracts — delivery plan

- **Branch:** `cursor/cognitive-contracts-1036`
- **Date:** 2026-09-08
- **Target release:** rolling
- **Status:** In progress
- **Specs:** spec-0030
- **ADRs:** ADR-0029

## Executive summary

Ship the shared 1.1.0 Pydantic envelope as a standalone package in this
repo, with INV-16 tests and a process-spawn inventory, before any Mango-Mas
agent emits a signal or any write/command tool is added. The sequencing
constraint is INV-16 plus the harness isolation gap (ProcessBackend contains,
does not isolate): nothing new may execute until the companion isolation
backend exists.

## PR A — Contracts package + boundary tests (spec-0030)

### Milestone A0 — Envelope 1.1.0 ✅

- **Failing test first:** authority-shaped extras (`allowed_tools`, …) raise
  `ValidationError`; nested payload keys are walked.
- **Depends on:** nothing — parallel-safe.
- Package: `mango-integration-contracts/src/mango_contracts/`
  (`CognitiveSignal`, `ProposedAction`, evidence, payload registry, ingest).

### Milestone A1 — INV-16 noninterference + role map ✅

- **Failing test first:** two signals differing only in `confidence` produce
  identical `policy_input_from_signal` dicts; unknown roles raise.
- **Depends on:** A0.

### Milestone A2 — Isolated 100% coverage gate + spawn inventory ✅

- **Failing test first:** `make contracts-coverage` (mirrors bridge-coverage);
  `tests/test_execution_bypass_inventory.py` fails if `subprocess` appears
  under `agents/` or `core/`.
- **Depends on:** A0.

## PR B — Opt-in producer

### Milestone B0 — `MANGOMAS_SIGNAL__*` default off ✅

- **Failing test first:** flag off → identical `AgentResponse` and no sink
  writes; flag on → one JSONL line, dispatch unchanged (contained failures).
- **Depends on:** PR A. Planner/reviewer emit `planning.proposal` /
  `review.finding` only. Do not map `tool` → `implementer`.

## PR C — Read-only observations ✅

Keep `retrieve` local. No `run_command` / `write_file` / `apply_patch` from
this repo. JSONL is the sink; HTTP ingest is optional (`MANGOMAS_SIGNAL__HTTP_URL`)
and unused until the harness route exists.

## Deferred / out of scope

- Merging orchestrators or subclassing `_HarnessOrchestrator` for authority
  (ADR-0029).
- Hugging Face MoE, Memory/Learning cells, or `ROLE_MAP` to `implementer`
  (see `docs/analysis/20260908-governed-coding-platform-architecture-review.md`).
- OPA/Rego (defer until dynamic grants; harness-side).
- Command/patch execution until harness INV-13 isolation backend exists
  (architecture-set Change 05 hard gate).
- Silently accepting harness envelope 1.0.0.
- `CognitiveRequest` / `Observation` models (inbound request and
  broker-produced facts; cognition must not emit Observation).

## Architecture-set change map

Pack Changes 00–01 are this PR. 02 (trace ids in payload only), 04
(read-only ingest), and planner/reviewer emission default-OFF are later
PRs in *this* repo. 03 and 05–10 are harness work; 06+ are blocked here
until 05.

## Verification

```bash
make gate
make contracts-coverage
```
