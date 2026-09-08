# Spec-0030: Cognitive/execution boundary plugin

- **Status:** In progress
- **Linked ADR:** ADR-0029
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

Mango-Mas V2 is a cognitive/orchestration plane (planner, reviewer, chat,
summarize, tool). The sibling
[Mango Code Agent Harness](https://github.com/ianshank/Mango_Code_Agent-Harness)
is the governed execution and verification plane (ExecutionBroker,
`command_actions` allowlist, INV-16). Integrating them by merging
orchestrators, sharing internals, or letting model confidence select tools
would leak authority into cognition.

The harness already ships a dataclass `CognitiveSignal` **1.0.0**. This spec
adopts a **Pydantic v2 envelope 1.1.0** as a standalone shared package so
neither repo imports the other's internals.

## Requirements

- Standalone package `mango-integration-contracts` (`mango_contracts`) with
  `extra="forbid"`, frozen models, schema `1.1.0`.
- Envelope is identity + schema + policy binding + content + evidence.
  It MUST NOT contain `allowed_tools`, `granted_capabilities`,
  `execution_approved`, `policy_override`, `release_approved`, `gate_passed`,
  `retry_limit`, `execution_priority`, shell/argv, filesystem handles, or
  secrets — at the top level **or nested in payload/metadata**.
- `confidence`, `severity`, `recommendation`, and `payload` MUST NOT be PDP,
  broker, retry, completion, or release inputs.
- `ProposedAction` is a separate model. Signals may cite opaque
  `proposed_action_refs` only.
- Unknown review roles and unknown Mango-Mas agent names RAISE; they never
  default to `implementer`.
- Payload registry validates known `signal_type`s; unknown payload schemas
  are archived, never executed.
- Must remain **additive & default-OFF** in `mangomas` runtime: this change
  must not import `mango_contracts` from `src/mangomas/` yet (emission is a
  later PR). Byte-identical orchestrator behaviour.

## Rejected readings (architecture set 2026-09-08)

Recorded so they cannot land in a follow-up PR by diagram inertia. Full
review: `docs/analysis/20260908-governed-coding-platform-architecture-review.md`.

- This repo is **not** Hugging Face MangoMAS (no MoE-7M, no MemoryCell).
  Cognitive producers are `planner` / `reviewer` / `chat` / `summarize` /
  `tool`. `mangomas.harness` is Claude Code + OTel, not ExecutionBroker.
- Do not copy pack ADR-001…007 into `docs/adr/` (number collision).
- Do not ship `ROLE_MAP["developer"] = "implementer"`. Unknown roles raise;
  known cognitive labels must not select write-capable harness roles.
- Do not add gRPC, OPA, Observation (broker fact), or `CognitiveRequest` in
  this change. Observation emitted by cognition would forge L8 evidence.
- Do not add `run_command` / `write_file` / `apply_patch` until the harness
  isolation backend exists (pack Change 05 hard gate).
- Side-effecting egress (GitHub, FS, argv) originates at the harness. LLM
  HTTP is allowed; that is the cognitive substrate.

## Scenarios (WHEN/THEN)

- WHEN an envelope carries a top-level `allowed_tools` field THEN validation
  fails AND WHEN the field is absent THEN a valid review finding parses.
- WHEN two envelopes differ only in `confidence` THEN
  `policy_input_from_signal` is byte-identical AND the stand-in disposition
  is unchanged (INV-16).
- WHEN `evidence.status=sufficient` and `refs=[]` THEN validation fails AND
  WHEN a ref is present THEN it passes.
- WHEN `producer_id_for_agent("quality agent")` is called THEN it raises AND
  WHEN `"planner"` is passed THEN it returns `mangomas.planner.v2`.
- WHEN a routing payload names `recommended_cognitive_role="implementer"`
  THEN ingest rejects the envelope AND WHEN it names `defect_analysis`
  THEN it parses.
- WHEN `src/mangomas/agents` or `core` gains `subprocess` THEN the bypass
  inventory test fails AND WHEN none is present THEN it passes.

## Config / env additions

None in this change. Emission settings (`MANGOMAS_SIGNAL__*`) land with the
producer PR so `.env.example` / CLAUDE.md stay aligned with live Settings.

## Protocol / contract impact

- New/changed protocols: none in `src/mangomas/`
- New error types: none in `mangomas.errors` (contracts use `ValueError`
  subclasses so the package stays mangomas-free)
- Registry additions: payload registry inside `mango_contracts` only

## Backwards-compatibility

- `src/mangomas/` is untouched at runtime; flag-off behaviour is the current
  tree (this PR adds no flag).
- Wire break vs harness `CognitiveSignal` 1.0.0 (string ids, optional
  `producer_version`, no TTL/evidence bundle). Companion harness bump to
  1.1.0 is required before cross-repo ingest. Do not silently coerce 1.0.0.

## Test plan

- Unit: `tests/mango_contracts/` (own constants; no `mangomas` import)
- Isolated coverage: `make contracts-coverage` floor 100%
- Bypass inventory: `tests/test_execution_bypass_inventory.py`
- Gates: both directions on extra=forbid, sufficient-evidence, role map
- Coverage: mangomas 95% unchanged; contracts 100% isolated

## Acceptance criteria

- [x] Feature off by default → no `mangomas` import of contracts (test proves it).
- [x] `extra="forbid"` rejects authority-shaped extras (test proves it).
- [x] INV-16 noninterference test (test proves it).
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint`,
  `make contracts-coverage` all clean.
- [x] CHANGELOG updated; ADR-0029 added.
