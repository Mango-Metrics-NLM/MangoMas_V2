# Analysis: peer review of the Governed Coding Platform architecture set

- **Date:** 2026-09-08
- **Scope:** the 13-section C4/design pack titled *MangoMAS_V2 ×
  Mango_Code_Agent-Harness — Architecture Design Set*, read against this
  checkout (`Mango-Metrics-NLM/MangoMas_V2` on `cursor/cognitive-contracts-1036`)
  and spec-0030 / ADR-0029.
- **Method:** mango-architect checklist (layering, contract direction,
  INV-16, composition-root discipline) plus a source walk of
  `src/mangomas/agents`, `core`, `harness`, and
  `mango-integration-contracts`.
- **Verdict:** **approve-with-changes**. Keep the authority split, the
  1.1.0 envelope, fail-closed classification, and the sandbox hard gate.
  Do **not** adopt the pack's product identity, role map, ADR numbers, or
  execution sequence as written.

Claims below are tagged **[Keep]**, **[Reject]**, or **[Defer]** (correct
idea, wrong change, or wrong repository).

---

## 1. Thesis

The pack is a strong **control-plane** design for
`ianshank/Mango_Code_Agent-Harness`. It is a weak description of **this**
repository. Treating Hugging Face MangoMAS (MoE-7M, MemoryCell,
LearningCell, `featurize64`) as if it were Mango-Mas V2 (FastAPI + LM
Studio, `planner` / `reviewer` / `chat` / `summarize` / `tool`) would
rebuild the orchestrator-merge architecture the pack itself warns against.

INV-16 survives only if three facts stay true at once:

1. This repo emits `CognitiveSignal` / `ProposedAction` and never
   classifies, authorizes, or spawns.
2. The harness is the only outbound edge to GitHub, shell, and writable
   FS.
3. No cognitive field — including a recommended role that maps to
   `implementer` — is a PDP input.

The 1.1.0 contract package in this change implements (1) and tests (3).
(2) is a harness obligation; this repo's agents already talk to LM Studio,
which is cognition's substrate, not a side effect.

---

## 2. What to keep

| Pack element | Why it is load-bearing |
|---|---|
| **[Keep]** Cognition proposes; harness disposes (INV-16, ADR-001 in the pack) | Matches ADR-0029. Byte-identical behaviour with cognition off is the acceptance test. |
| **[Keep]** Standalone `mango-integration-contracts`, neither repo imports the other (pack ADR-003) | Same decision as ADR-0029; eval_harness_bridge is the precedent (ADR-0003). |
| **[Keep]** `extra="forbid"`, nested authority-key rejection, TTL, `policy_snapshot_hash` match | Schema mismatch must not silently grow a control field. Implemented. |
| **[Keep]** Unclassified command → `destructive`; no ordinary role holds it | Harness PDP. Do not reimplement here. |
| **[Keep]** Broker contains; sandbox isolates (pack ADR-004, Change 05 hard gate) | INV-13 / DEC-010. Blocks patch/command tools in *this* repo until the harness isolation backend exists. |
| **[Keep]** Memory/Learning write-back off (pack ADR-006) | Those cells are not in this tree. Do not add them. |
| **[Keep]** Per-provider retrieval adapters, not one internet tool (pack ADR-007) | Aligns with later read-only observations; keep `retrieve` local. |
| **[Keep]** L1–L15 leak table | Direct test plan. This change covers L1, L2, L3 (no capability field), L4 (agents/core spawn inventory), L14, L15. |
| **[Keep]** Shadow evaluation before cell promotion (Change 08) | Do not skip to gated GitHub writes. |
| **[Keep]** `workflow.complete_recommended` is advisory; only the gate engine may enter Accepted | Envelope requires a recommendation record and still cannot transition state. |

---

## 3. Must-reject (evidence against this checkout)

### 3.1 Wrong cognitive-plane identity **[Reject]**

Pack §1–§4 draw MangoMAS_V2 as MoE-7M + cells (reasoning, peer review,
PII, causal, research, Memory, Learning) behind a proposal-only facade.

This checkout's cognitive plane is `src/mangomas/agents/` (`ChatAgent`,
`PlannerAgent`, `ReviewerAgent`, `SummarizeAgent`, `ToolAgent`) wired in
`composition.py`, talking to LM Studio or Vertex. There is no
`featurize64`, no expert gate, no MemoryCell.

`src/mangomas/harness/` is Claude Code governance plus an OTel wrap of
`dispatch` (ADR-0021). It is **not** ExecutionBroker. Overloading
`MANGOMAS_HARNESS__*` or subclassing `_HarnessOrchestrator` for authority
is an INV-16 regression.

**Correction:** diagrams that ship in *this* repo must name FastAPI agents
as the cognitive plane and the sibling GitHub harness as the authority
plane. Hugging Face MangoMAS, if used at all, is a *future optional
producer* of `routing.recommendation`, not the system in C1/C2 today.

### 3.2 §4 `ROLE_MAP` grants `implementer` **[Reject]**

```text
"developer": "implementer"
```

That is a model-chosen label selecting a write-capable harness role — the
same defect as router-supplied `allowed_tools`, whether or not a comment
says the map is fail-closed. Unknown keys raising is necessary and
insufficient.

**Correction in this package:** `HARNESS_REVIEW_ROLES` has no
`implementer`; `RoutingRecommendationPayload` rejects
`recommended_cognitive_role` in `{implementer, destructive, write_file,
shell}`. Any harness-side map from cognitive labels onto execution roles
belongs in the harness, is out of this repo, and must not target
`implementer` from a cognitive signal.

### 3.3 “MangoMAS has no outbound edge to any external system” **[Reject as stated]**

True for GitHub / shell / writable FS. False for this repo’s LLM and
optional embeddings HTTP. C1 correctly shows `mangomas → LM Studio`.
Rewrite the invariant as: **every side-effecting arrow (GitHub, FS, argv,
network mutation) originates at the harness broker.** LLM complete/stream
is the cognitive substrate.

### 3.4 ADR number collision **[Reject]**

Pack ADR-001…007 would collide with this repo’s `docs/adr/0001`–`0028`.
Binding decisions here are **ADR-0029** (envelope) and later ADRs from
**0030**. Do not copy the pack’s ADR files into `docs/adr/`.

Pack statuses “Accepted” while Change 00 says the repos were unread is
status inflation. ADR-0029 stays **Proposed** until this PR merges.

### 3.5 gRPC / in-process CognitiveRuntime / OPA in Change 02–03 **[Defer, not this repo]**

CI deployment in §8 shows harness ↔ MangoMAS over gRPC. This service is
FastAPI HTTP. Do not add a gRPC server to emit signals.

OPA/Rego (pack ADR-005) is harness PDP work. This repo must not grow a
second policy engine.

### 3.6 `Observation` and `CognitiveRequest` inside the contract package **[Defer]**

§2 puts `Observation` next to `CognitiveSignal`. Observations are
broker-produced facts (L8: no `producer_type="cognitive"`). Adding an
Observation model that cognition can emit would recreate forged
“tests passed” evidence. Keep Observation harness-owned.

`CognitiveRequest` is inbound harness → cognition. Out of this PR; when
it lands it must carry refs, not tokens, not writable handles, not
`allowed_tools`.

### 3.7 L5 “monkeypatch httpx to raise in MAS” **[Reject as stated]**

Agents here *should* call `httpx` via `LLMClient`. The control is “no
GitHub/web/shell tools on the cognitive facade,” not “no HTTP.” The
agents/core subprocess inventory is the right L4 test for this tree;
`mangomas.harness.governance` may use `subprocess.run` for git (outside
the scan roots).

### 3.8 Change 06–10 before Change 05 **[Reject]**

Pack already marks sandbox as a hard gate. This repo must not grow
`run_command` / `write_file` / `apply_patch` tools, nor GitHub write
adapters, until the harness isolation backend exists. Spec-0030 sequencing
stands.

---

## 4. Map pack Changes 00–10 onto this repository

| Pack change | This repo | Status |
|---|---|---|
| 00 readability + bypass inventory | `tests/test_execution_bypass_inventory.py` on `agents/` + `core/` | This PR |
| 01 contract 1.1.0 | `mango-integration-contracts/` + `make contracts-coverage` | This PR |
| 02 telemetry bridge | Later: trace ids in signal payload only; do not let cognition emit `execute_tool` | Not this PR |
| 03 OPA | Harness-only | Out of repo |
| 04 read-only brokered tools | Keep local `retrieve`; optional HTTP ingest | Later PR |
| 05 sandbox | Harness hard gate | Blocks 06+ here |
| 06–07 patch/command | Forbidden in this repo until 05 | — |
| 08 shadow eval | Harness + optional `mangomas eval` datasets | Later |
| 09 “CausalCell only” | N/A — no cells. Enable *planner/reviewer emission* default-OFF instead | Later PR |
| 10 GitHub writes | Harness-only; never from `src/mangomas` | — |

---

## 5. L-table vs tests in this change

| Leak | Covered here? |
|---|---|
| L1 confidence → PDP | `test_cognitive_signal_noninterference.py` |
| L2 severity → PDP | same, parametrized cognitive fields |
| L3 router `allowed_tools` | `extra="forbid"` + nested walker + routing payload denylist |
| L4 subprocess in cognition | bypass inventory (`agents`, `core`) |
| L5 raw sockets/GitHub from facade | not yet (no new tools); layering test: `src/mangomas` does not import contracts |
| L6 writable FS handle on envelope | forbidden keys `fs_handle` / `file_handle` |
| L7 prompt injection | harness context compiler; `is_prompt_eligible` is not a permission API |
| L8 forged execution evidence | no Observation type in the package |
| L9 idempotent PR create | `compute_idempotency_key` on `ProposedAction` only; no GitHub client |
| L10–L13 path denylist, bash -c, HF remote code, Context7 pin | harness canonicalization |
| L14 expired signal | ingest `expired_signal_archived` |
| L15 policy snapshot mismatch | ingest `policy_snapshot_mismatch` |

---

## 6. Pack ADRs, restated for this repo only

Do not file pack ADR-001…007 here. Local restatement:

- Pack ADR-001 ≡ already INV-16 / this ADR-0029.
- Pack ADR-002 (MoE recommends roles, not tools) — adopt the *constraint*,
  not the MoE. This tree has no MoE; `routing.recommendation` is an
  optional payload schema for a future producer.
- Pack ADR-003 ≡ ADR-0029 standalone package.
- Pack ADR-004 sandbox — referenced as an external hard gate, not
  implemented here.
- Pack ADR-005 OPA — deferred, harness-side.
- Pack ADR-006 Memory/Learning off — vacuously true (absent).
- Pack ADR-007 per-provider adapters — later read-only PR.

---

## 7. Remaining honesty gaps in the pack (not blocking this PR)

1. Change 00 claimed neither repo was readable; this checkout is readable
   and contradicts several class names in the pack. Future harness-side
   diagrams must be redrawn from that repo’s tree, not from placeholders.
2. The 0.39% blocked-precision prior is useful as a *reason not to let
   cognition veto*, not as a SLO.
3. Mermaid C4 is experimental; this repo’s C1/C2 stay as they are, with
   `integration_contracts` → `code_agent_harness` on C2 only. Do not
   replace C1 with the pack’s “Governed Coding Platform” boundary (it
   would mark LM Studio as a regression).
4. GenAI semconv pinning is a harness config concern.

---

## 8. Acceptance test (unchanged, now CI-shaped)

With no `mango_contracts` import from `src/mangomas/`:

- orchestrator behaviour is the current tree (this PR adds no runtime flag);
- no cognitive cell reaches a side-effecting tool;
- `policy_input_from_signal` contains only
  `run_id`, `task_id`, `policy_id`, `policy_version`, `policy_snapshot_hash`;
- `workflow.complete_recommended` cannot mark a run complete.
