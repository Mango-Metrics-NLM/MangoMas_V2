# Hardware-agnostic end-to-end suites — delivery plan

- **Branch:** `claude/e2e-tests-gpu-cpu-agnostic-vyp5z3`
- **Date:** 2026-09-04
- **Target release:** rolling (`[Unreleased]`)
- **Status:** Delivered — groups A–D landed; tiers 2/3 written but not executed in this environment (see spec-0029 "Not executed here")
- **Specs:** spec-0029 (the WHAT); exercises specs 0025, 0026, 0027, 0028,
  0012, 0013 and the shipped `plan-execute-review` graph
- **ADRs:** none — no boundary change

## Executive summary

Four commit groups, in dependency order, on one branch. **A** is small and
lands first because everything else builds on it: it fixes a latent
CPU-vs-GPU defect already in `tests/lmstudio/` **and `tests/vertex/`** (a 60 s
httpx client budget under a 240 s adapter budget), makes both budgets
env-driven, adds `FakeLLM.delay_seconds`, and ships the hardware-contract lint
so later groups are checked by a mechanism rather than by review memory. **B**
grows `tests/integration/` — the only E2E tier CI runs on every push — from
one hand-built test to seven flows through the **real composition root**.
**C** adds LM Studio scenarios 7–12. **D** gives the RAG tier a device
contract, the one additive config field, a nightly CPU home, and the
gated-suite parity guard that closes roadmap item 2.1.

The ordering constraint: **a tier-2/tier-3 test written before the lint exists
is a test nobody re-checks for hardware coupling once the author moves on.**

Where "GPU/CPU agnostic" applies is narrower than the phrase suggests, and
this plan says so rather than implying more: tier 1 has no hardware exposure
by construction; tier 2's exposure is latency plus, for exactly one scenario
(L10), model capability; tier 3's is the torch device and float drift. Spec-0029
R2's six rules target those three surfaces and nothing else.

## Peer-review corrections folded in

The first draft of this plan was reviewed against the source before any code
was written. Eight claims were wrong or would have produced a test that could
not fail. Each correction is load-bearing:

| # | First draft said | Source says | Consequence |
|---|---|---|---|
| 1 | post an inline graph **dict** to `/workflows/run` | `WorkflowRunRequest.definition` is `str \| None` — inline JSON **or a path** (`api/models.py:24`) | I4/I5/L12 must send `json.dumps(graph)`; a dict is a 422 |
| 2 | I4/I5 need `MANGOMAS_WORKFLOW__ENABLED=true` | a per-request `definition` runs even when the feature is disabled (`api/routes/workflows.py:41`) | the env var is unnecessary; testing with it would hide that documented behaviour |
| 3 | `metadata["loop"]` "reports the budget tier" | the block is exactly `{"steps_taken", "accepted"}` (`core/orchestrator.py:310`) | I3's oracle becomes `steps_taken == n` **plus** the fake's call count — there is no budget field to read |
| 4 | I7 covers invoke **and** stream per tenant | `tests/test_tenancy.py:114` already covers invoke + `/history` for two tenants | I7 narrows to the **streamed** turn, which nothing covers |
| 5 | I1 asserts the SSE `metadata` frame | `tests/test_streaming.py:136` already asserts it | dropped; I1 keeps stream → `/history`, genuinely new because every streaming test runs `repo=None` (`tests/test_streaming.py:26`) |
| 6 | the budget mismatch is a `tests/lmstudio/` problem | `tests/vertex/conftest.py:55` duplicates the 240 s budget and its scenarios use the same 60 s client constant | group A fixes both suites; the acceptance grep covers both |
| 7 | "add a nightly job" | `tests/deploy/test_ci_make_parity.py:test_nightly_jobs_delegate_to_make` asserts the nightly jobs' **exact** command lists, and `test_workflow_hardening.py` requires every scheduled workflow to report its own failure | the job, that assertion, and `notify.needs` change in one commit or CI goes red |
| 8 | a new parity meta-test file with its own parsing | `tests/deploy/_workflows.py` + `_make_target_body` already parse workflows and Makefile targets | the guard reuses both; a third reader is the drift these helpers exist to prevent |

## Group A — Contract, budgets, fakes (spec-0029 R2, R7.1, R8, R9)

### A0 — Env-driven live budgets; kill the client/adapter mismatch ✅

- **Failing test first:** A2's lint, run over the real tree *before* this
  change, flags the numeric `timeout=` in six live-suite call sites. Also
  grep-provable: `HTTPX_REQUEST_TIMEOUT_SECONDS` has six users before and
  ceases to exist after.
- **Depends on:** nothing.
- `tests/constants.py`: `LMSTUDIO_E2E_TIMEOUT_ENV` / `VERTEX_E2E_TIMEOUT_ENV`
  names, `DEFAULT_LIVE_E2E_TIMEOUT_SECONDS = 240.0`,
  `LIVE_STEP_TIMEOUT_SECONDS`, and a shared
  `resolve_live_timeout(env_var)` helper so neither conftest hand-rolls the
  env read. Delete `HTTPX_REQUEST_TIMEOUT_SECONDS`.
- Both live conftests gain a `*_timeout` fixture (env → constant) and a
  `*_client_timeout` fixture returning an `httpx.Timeout` derived from it,
  never smaller. Every scenario switches to the fixture.
- `docs/testing/lmstudio-e2e.md`: env-var table gains the rows.

### A1 — `FakeLLM.delay_seconds` ✅

- **Failing test first:** `FakeLLM(delay_seconds=SLOW_AGENT_DELAY_SECONDS)`
  under `asyncio.timeout(TINY_STEP_TIMEOUT_SECONDS)` raises `TimeoutError`;
  `FakeLLM()` records **zero** `asyncio.sleep` calls via a spy — the default
  must stay byte-identical, and "no sleep at all" is the assertion that proves
  it.
- **Depends on:** nothing. Owner surface: `tests/fakes.py`
  (`mango-fake-builder`).

### A2 — Hardware-contract lint ✅

- **Failing test first:** its own subprocess proof — a planted tier-2 file
  containing `assert elapsed < 5`, a `"cuda"` literal, and a numeric
  `timeout=` must fail the lint naming each; the real tree must pass after A0.
- **Depends on:** A0 (the real tree must be clean for the lint to land green).
- Four rules, each with a positive and a negative fixture. The scope list
  lives in `tests/constants.py` so adding a tier-3 sibling in group D is one
  line. The module docstring states plainly what the lint cannot prove.
- `GATED_RUNTIME_SKIP_REASON_PREFIXES` gains `"LMSTUDIO_"`;
  `tests/tooling/test_collection_gate.py` gains the matching sanctioned-skip
  case.

## Group B — Tier 1: E2E through the real composition root (spec-0029 R3)

### B0 — `tests/integration/conftest.py`: `composed_app` ✅

- **Failing test first:** the existing `test_invoke_flow_persists_turn`,
  rewritten onto the fixture, fails on import until it exists.
- **Depends on:** A1 (B2 needs `delay_seconds`).
- Yields the app plus the recording state a flow needs. Builds via
  `build_orchestrator(Settings())` after `monkeypatch.setenv`, injects into
  `create_app`, swaps the LLM via `llm_registry.scoped` (the established
  idiom), and closes the orchestrator on teardown. Storage is in-memory
  SQLite so a developer's real DB is never touched.

### B1 — I1 stream persistence → `/history` ✅

- **Failing test first:** drain the stream, then `GET /history` → one `chat`
  turn. Mutation: drop the persistence call in `_stream_agent` → red.
- Second test: a mid-stream failure persists nothing.

### B2 — I2 + I3 `LoopSettings` over HTTP ✅

- **Failing test first:** tiny budget + delayed fake → 504, envelope code
  `step_timeout`, `/history` empty. Mutation: remove the `asyncio.timeout`
  wrap → red. Sibling: default budget → 200 + one turn.
- I3: `MANGOMAS_LOOP__MAX_STEPS=n` → `steps_taken == n` and `n` fake calls; a
  body-supplied `max_steps` beats the env.

### B3 — I4 the shipped graph with validation on ✅

- **Failing test first:** post the **shipped** `examples/workflows/plan-execute-review.json`
  as an inline JSON string with validation on for planner and reviewer; the
  content validates as `ReviewResult`. Negative: planner emits prose → the
  `llm_bad_response` envelope. Mutation: turn validation off in the negative
  test → it goes green, proving the flag and not the fake is what rejects.
- Reads the example file rather than a copy, so the shipped artefact cannot
  rot silently.

### B4 — I5 `branch` + composite `fan_out` over HTTP ✅
### B5 — I6 `MODEL_OVERRIDE` end to end ✅

- The recording factory keys on `cfg.model`, which works because
  `build_agent_llm_overrides` calls the **same** registry factory for base and
  overrides (`composition/llm.py`), swapping only `model`.
- Teardown assertion: after `aclose`, both recorded clients are closed.

### B6 — I7 tenancy across a **streamed** turn ✅
### B7 — Docs + CHANGELOG for group B ✅

## Group C — Tier 2: LM Studio scenarios 7–12 (spec-0029 R4)

Written and runnable; a recorded GPU + CPU run pair is the acceptance evidence
for "agnostic" and cannot be produced in this container (no LM Studio, no
GPU). The plan records that honestly rather than claiming a green run.

### C0 — L7 stream persists ✅
### C1 — L8 live `StepTimeout` at `LIVE_STEP_TIMEOUT_SECONDS` ✅

The sub-millisecond budget is what makes this hardware-independent: no GPU
returns a completion inside one network round trip, so the assertion never
depends on model speed.

### C2 — L9 pipeline acceptance loop ✅
### C3 — L10 validated `plan-execute-review` at `temperature=0` ✅

Recorded honestly: the one scenario whose green depends on model capability.
A model that cannot emit the `ExecutionPlan` JSON fails it on every device.
The oracle is not softened to make a weak model pass.

### C4 — L11 live `MODEL_OVERRIDE`, sanctioned runtime skip ✅
### C5 — L12 `branch` + composite `fan_out` over `/workflows/run` ✅
### C6 — Docs for group C ✅

## Group D — Tier 3: device contract, nightly home, parity guard (R5–R7)

### D0 — `MANGOMAS_EMBEDDINGS__DEVICE` ✅

- **Failing test first:** a recording loader injected in place of
  `_lazy_load_model` records no `device` when unset and `device="cpu"` when
  set — asserted **through the composition factory**, so both hops are one
  test. `tests/deploy/test_env_example_contract.py` goes red until
  `.env.example` and the CLAUDE.md table carry the row.
- `config/rag.py::EmbeddingSettings` (singular) gains `device: str | None`;
  `composition/embeddings.py` forwards it; the adapter passes it to the
  loader only when set.

### D1 — E1 + E2 device contract in `tests/rag/` ✅
### D2 — E3 `ToolAgent` + `RetrievalTool` over real embeddings ✅
### D3 — E4 `mangomas rag ingest|query` with the real provider ✅
### D4 — Nightly `embeddings-local` job (CPU) ✅

- **Failing test first:** D5's parity guard names `RUN_EMBEDDINGS_LOCAL` as
  homeless until this job exists — write D5 first, watch it name the suite,
  then add the job.
- CPU torch wheels installed **before** the extras; Hugging Face cache keyed
  on the model id. `notify.needs` and the exact-command assertion in
  `test_ci_make_parity.py` change in the same commit (correction 7).
- `DEVICE` is deliberately **not** set in the job: the runner has no GPU, so
  auto-detect resolves to CPU and E2 runs trivially-equal. Forcing it would
  hide a broken auto-detect.

### D5 — Gated-suite parity guard ✅

- Reuses `tests/deploy/_workflows.py` and `_make_target_body`. The
  `RUN_* → make target` mapping is parsed from the Makefile's recipe lines,
  not restated. `HOSTED_RUNNER_INFEASIBLE` records each reason:
  LM Studio needs a local process; Vertex / GCP need credentials and project
  (roadmap decision D2); Langfuse needs an extra plus credentials.
  `RUN_GITLEAKS` must **not** be listed — it has a home, and that is the
  stale-claim proof.

### D6 — Docs + CHANGELOG for group D ✅

## Deferred / out of scope

- **`dispatch_fan_out_settled` E2E.** No public consumer: the workflow
  `fan_out` node delegates to the fail-fast `dispatch_fan_out`, and no route
  exposes the settled variant. An E2E test would have to invent a surface.
  Re-open when a graph-level `join` mode or a route consumes it (spec-0027 /
  ADR-0027).
- **Stream-abandonment E2E.** Not deterministic over `httpx.ASGITransport`;
  stays a unit contract (spec-0025 / ADR-0025).
- **A CI home for LM Studio, Vertex, GCP, Langfuse.** Hosted runners cannot
  host LM Studio; the cloud suites wait on roadmap decision D2. D5 records
  each reason so the gap is declared, not implied.
- **A GPU CI leg.** No hosted GPU runner is provisioned. The agnosticism
  evidence for tiers 2/3 is a recorded GPU + CPU run pair, not a pipeline.
- **Perf smoke** (roadmap Phase 2). Excluded by R2.1 — a wall-clock assertion
  is exactly what this plan forbids in the E2E tiers. It needs its own suite,
  hardware baseline table and gate semantics.
- **Forcing `DEVICE` for LM Studio.** LM Studio owns its own device placement;
  nothing here can or should set it. The knob is for the in-process backend.
- **`MANGOMAS_RAG__MIN_CHUNK_WORDS` inertness.** Unchanged; still the open
  retrieval-quality decision recorded in `NEXT_STEPS.md`.

## Verification

```bash
make gate                                   # full offline chain, CI's order
make gated-suites                           # tier 1 + fakes-only rag — what CI runs
RUN_LMSTUDIO=1 LMSTUDIO_MODEL=<id> make lmstudio                 # tier 2, GPU box AND LM Studio on CPU
RUN_LMSTUDIO=1 LMSTUDIO_MODEL=<id> LMSTUDIO_OVERRIDE_MODEL=<id2> make lmstudio   # + L11
pip install torch --index-url https://download.pytorch.org/whl/cpu \
  && pip install -e ".[dev,embeddings-local,rag]" && make embeddings-local       # tier 3, CPU
MANGOMAS_EMBEDDINGS__DEVICE=cpu make embeddings-local            # tier 3 forced-CPU parity on a GPU box
```

Mutation proofs (run and reported, never committed):

| Group | Mutation | Test that must go red |
|---|---|---|
| A | plant `assert elapsed < 5` / `"cuda"` / numeric `timeout=` in a scoped tmp file | hardware-contract lint |
| A | delete the `delay_seconds` await | the delay test |
| B | remove stream persistence in `_stream_agent` | I1 |
| B | remove the `asyncio.timeout` wrap | I2 (through HTTP) |
| B | turn `validate_output` off in the negative case | I4 negative |
| B | make `resolve_llm` ignore `extras` | I6 |
| D | drop the `device` forward in the factory | D0 |
| D | delete `make postgres` from a tmp copy of `nightly.yml` | parity guard |
| D | list `RUN_GITLEAKS` as infeasible | parity guard (stale claim) |
