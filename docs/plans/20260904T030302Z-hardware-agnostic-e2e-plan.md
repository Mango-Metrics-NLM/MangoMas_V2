# Hardware-agnostic end-to-end suites — delivery plan

- **Branch:** `claude/e2e-tests-gpu-cpu-agnostic-vyp5z3`
- **Date:** 2026-09-04
- **Target release:** rolling (`[Unreleased]`, per landed PR)
- **Status:** Draft — plan only; no test code on this branch yet
- **Specs:** spec-0029 (this plan's WHAT); exercises specs 0025, 0026, 0027,
  0028, 0012, 0013 and the shipped `plan-execute-review` graph
- **ADRs:** none — no boundary change

## Executive summary

Four PRs, in dependency order, give every Phase-1 delivery an end-to-end test
in the tier where it can actually execute, and put the two hardware-sensitive
tiers (live LM Studio, local sentence-transformers) under one written and
linted contract. **PR A** is small and lands first because everything else
builds on it: it fixes a latent CPU-vs-GPU defect already in `tests/lmstudio/`
(a 60 s httpx client budget under a 240 s adapter budget), makes the budgets
env-driven, adds `FakeLLM.delay_seconds`, and ships the hardware-contract lint
so PRs C and D are checked by it rather than by review memory. **PR B** grows
`tests/integration/` — the only E2E tier CI runs on every push — from one test
to seven flows over the real composition root. **PR C** adds LM Studio
scenarios 7–12. **PR D** gives the RAG tier a device contract, the one
additive config field (`MANGOMAS_EMBEDDINGS__DEVICE`), a nightly CPU home, and
the gated-suite parity meta-test that closes roadmap item 2.1. The constraint
that shaped the order: **a tier-2/tier-3 test written before the lint exists
is a test nobody re-checks for hardware coupling once the author moves on.**

Where "GPU/CPU agnostic" applies is narrower than it sounds, and the plan says
so: tier 1 has no hardware exposure by construction; tier 2's exposure is
latency (budgets) and model capability (one scenario, L10); tier 3's exposure
is the torch device and float drift. The rules in spec-0029 R2 target exactly
those three surfaces and nothing else.

## PR A — Contract, budgets, fakes (spec-0029 R2, R8, R9 + the lint half of R7)

### Milestone A0 — Env-driven live budgets; kill the client/adapter mismatch

- **Failing test first:** a new test in `tests/tooling/test_e2e_hardware_contract.py`
  (A2) is what would go red today — `tests/lmstudio/test_chat_invoke.py`
  passes a client `timeout=` smaller than the adapter budget. Until A2 exists,
  the proof is the grep in spec-0029's acceptance box: `HTTPX_REQUEST_TIMEOUT_SECONDS`
  is used in five `tests/lmstudio/` files today and in zero afterwards.
- **Depends on:** nothing — parallel-safe.
- `tests/constants.py`: add `LMSTUDIO_E2E_TIMEOUT_ENV = "LMSTUDIO_E2E_TIMEOUT_SECONDS"`,
  `DEFAULT_LMSTUDIO_E2E_TIMEOUT_SECONDS: float = 240.0`, and
  `LIVE_STEP_TIMEOUT_SECONDS: float = 0.001` (spec-0029 R2.5, with the
  "below one network round trip" rationale in the comment).
- `tests/lmstudio/conftest.py`: `LMSTUDIO_E2E_TIMEOUT_SECONDS` is resolved
  from the env with the constant as fallback (a fixture, `lmstudio_timeout`);
  `make_lmstudio_settings` takes it as its default; a new
  `lmstudio_client_timeout` fixture returns an `httpx.Timeout` derived from it
  (never smaller). Every scenario 1–6 switches its `timeout=` to the fixture.
  `tests/vertex/conftest.py` mirrors the same derivation for parity.
- `docs/testing/lmstudio-e2e.md`: env-var table gains the row.

### Milestone A1 — `FakeLLM.delay_seconds`

- **Failing test first:** `tests/test_fakes.py` (or the nearest existing
  fake-contract test) — `FakeLLM(delay_seconds=SLOW_AGENT_DELAY_SECONDS)`
  under `asyncio.timeout(TINY_STEP_TIMEOUT_SECONDS)` raises `TimeoutError`;
  `FakeLLM()` completes with no `sleep` call (assert via a monkeypatched
  `asyncio.sleep` spy that records zero calls — the default must stay
  byte-identical).
- **Depends on:** nothing — parallel-safe. Owner: `mango-fake-builder`.
- `tests/fakes.py`: additive dataclass field `delay_seconds: float = 0.0`,
  awaited at the top of `complete` and before the first chunk of
  `_fake_stream`, only when `> 0`.

### Milestone A2 — Hardware-contract lint (`tests/tooling/test_e2e_hardware_contract.py`)

- **Failing test first:** the lint's own subprocess proof — plant a tier-2
  file in a tmp tree containing `assert elapsed < 5` and a `"cuda"` literal;
  the lint run over that tree must fail naming both. Then run it over the real
  `tests/lmstudio/` and `tests/rag/test_end_to_end.py`: it must pass **after
  A0** (before A0 it flags the numeric `timeout=` in the vertex/lmstudio
  files, which is the point).
- **Depends on:** A0 (the real tree must be clean for the lint to land green).
- Rules, each a small regex over the file text, each with a positive and a
  negative fixture: (1) no `assert` line mentioning `perf_counter`, `monotonic`
  or `elapsed`; (2) no `"cuda"` / `"mps"` / `"cpu"` string literal outside
  `tests/constants.py`; (3) no `==` whose both sides call `embed` /
  `embed_batch`; (4) every `timeout=` keyword's value is a name, not a numeric
  literal. The module docstring states plainly that this is a text lint over
  the patterns this repo has actually written, not a proof.
- Scope table (the files it scans) lives in `tests/constants.py` as
  `HARDWARE_CONTRACT_SCOPE: tuple[str, ...]` so adding a tier-3 sibling in
  PR D is one line.
- `GATED_RUNTIME_SKIP_REASON_PREFIXES` gains `"LMSTUDIO_"`;
  `tests/tooling/test_collection_gate.py` gains the LM Studio-prefixed
  runtime-skip case (sanctioned) — needed by C4, cheap to land here.

### Milestone A3 — Docs + CHANGELOG for PR A

- **Depends on:** A0–A2.
- CHANGELOG `[Unreleased]` › `Fixed`: the client/adapter budget mismatch, with
  the GPU-green/CPU-red explanation. `Added`: the lint, `delay_seconds`.
- `.claude/agents/mango-integration-runner.md`: its "Diagnosing failures" item
  2 ("bump the per-test timeout in the test, not the fixture") is now wrong —
  budgets come from the env/constant. Update the line.

## PR B — Tier 1: in-process E2E over the real composition root (spec-0029 R3)

### Milestone B0 — `tests/integration/conftest.py`: the `composed_app` fixture

- **Failing test first:** `test_invoke_flow_persists_turn` rewritten to use
  the fixture: set `MANGOMAS_DB__URL=sqlite:///:memory:` via `monkeypatch`,
  build with `build_orchestrator(Settings())`, inject into `create_app`,
  `aclose` on teardown. It fails before the fixture exists (import error) and
  passes after — trivial, but it pins the fixture's shape for B1–B6.
- **Depends on:** nothing in PR A except A1 (B2 needs `delay_seconds`).
- The fixture yields `(app, orchestrator)`; the LLM comes from
  `llm_registry.scoped("lmstudio", <factory returning the test's FakeLLM>)`
  so the *composition* path (`cfg.llm.provider` → registry → client) is the
  one exercised, not a hand-built `AgentContext`. Telemetry is not configured
  (no lifespan), so `caplog` keeps working.
- Also here: a `history(client, tenant=None)` helper that `GET /history`s and
  returns the list, since five flows read it.

### Milestone B1 — I1 stream persistence → history (spec-0025)

- **Failing test first:** drain `/agents/chat/stream` fully, then
  `GET /history` → exactly one `chat` turn. Mutation: comment out the
  persistence call in `_stream_agent` → red.
- **Depends on:** B0.
- Second test: `FakeLLM(raise_on_stream=..., raise_after_chunks=1)` → the
  stream ends with the error frame and `GET /history` is empty. Third: the
  `metadata` SSE event is present in a fully drained stream. Stream
  *abandonment* (client disconnect mid-stream) is **not** tested here — over
  `httpx.ASGITransport` the disconnect is not delivered reliably enough for a
  deterministic oracle; it stays a unit-level contract in
  `tests/test_streaming.py`.

### Milestone B2 — I2 + I3 `LoopSettings` over HTTP (spec-0026)

- **Failing test first:** `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS=TINY_STEP_TIMEOUT_SECONDS`
  + `FakeLLM(delay_seconds=SLOW_AGENT_DELAY_SECONDS)` → `POST /agents/chat/invoke`
  is 504 with `error == "step_timeout"`, `GET /history` empty. Mutation:
  remove the `asyncio.timeout` wrap (the spec-0026 mutation, re-run through
  HTTP) → red.
- **Depends on:** A1, B0.
- Sibling: same fake with the default budget → 200, one turn.
- I3: `MANGOMAS_LOOP__MAX_STEPS=3` → the invoke response's
  `metadata["loop"]` shows the env tier; a request carrying an explicit
  `max_steps` shows the request tier instead. Verify the exact field names
  against `core/orchestrator.py`'s `"loop"` block before writing the oracle —
  the plan does not restate them.

### Milestone B3 — I4 the shipped graph over `POST /workflows/run` with validation on

- **Failing test first:** `MANGOMAS_WORKFLOW__ENABLED=true`,
  `MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT=true`,
  `MANGOMAS_AGENTS__REVIEWER__VALIDATE_OUTPUT=true`; `FakeLLM(replies=[plan
  JSON, tool prose, review JSON])` (reuse the fixtures in
  `tests/test_plan_execute_review.py`); post the example graph inline →
  200 and the content validates as `ReviewResult`. Negative: first reply is
  prose → the `llm_bad_response` envelope. Mutation: turn validation off in
  the negative test → it must go green (proves the flag, not the fake, is
  what rejects).
- **Depends on:** B0.
- This is the "advertised product" flow — planner → tool → reviewer — through
  the HTTP boundary for the first time. It reuses the graph file, not a copy,
  so the example cannot rot silently.

### Milestone B4 — I5 `branch` + composite `fan_out` over HTTP (specs 0012 / 0013)

- **Failing test first:** a `branch` graph whose predicate matches the fake's
  reply routes to `summarize` (assert `agent` in the response); a composite
  `fan_out` (one `agent` branch, one nested `sequence`) joins in roster order.
- **Depends on:** B0.

### Milestone B5 — I6 `MODEL_OVERRIDE` end to end (spec-0028)

- **Failing test first:** `MANGOMAS_AGENTS__CHAT__MODEL_OVERRIDE=<other-id>`;
  the scoped factory records `(model_id, client)` per construction → after
  invoke, the client built with the override id has one call and the default
  has none. Mutation: make `resolve_llm` ignore `extras` → red.
- **Depends on:** B0.
- Teardown assertion: after the fixture's `aclose`, **both** recorded clients
  are closed (the `_AgentLLMOverrideCloseMixin` path, through HTTP-built
  state for the first time).

### Milestone B6 — I7 tenancy across invoke + stream → history (specs 0007 + 0025)

- **Failing test first:** `MANGOMAS_TENANCY__ENABLED=true`; tenant `a`
  invokes, tenant `b` streams; `GET /history` with each header returns only
  that tenant's turn — the streamed one included. Mutation: drop the tenant
  filter → red.
- **Depends on:** B0, B1.

### Milestone B7 — Docs + CHANGELOG for PR B

- `docs/testing/regression.md` suite inventory: `tests/integration/` row
  rewritten from "ASGI transport" to the seven flows.
- CHANGELOG `[Unreleased]` › `Added`.

## PR C — Tier 2: LM Studio scenarios 7–12 (spec-0029 R4)

Every scenario here is run and recorded twice before merge — once on a GPU
box, once with LM Studio on CPU — and the plan's status line records model id,
hardware and date for each. That recorded pair is the acceptance evidence
for "agnostic"; the lint from A2 is the mechanism that keeps it so.

### Milestone C0 — L7 stream persists (spec-0025)

- **Failing test first:** `tests/lmstudio/test_stream_persists.py` — drain
  the live stream, then `repo.list_turns(limit=5)` holds one `chat` turn.
- **Depends on:** A0 (budget fixture).

### Milestone C1 — L8 live `StepTimeout` (spec-0026, R2.5)

- **Failing test first:** `make_lmstudio_settings(..., step_timeout_seconds=LIVE_STEP_TIMEOUT_SECONDS)`
  (extend the helper with a `LoopSettings` passthrough) → 504 `step_timeout`.
  Sibling with the CPU-sized budget → 200. Mutation: the spec-0026 wrap
  removal → red on the live path too.
- **Depends on:** A0.
- The 1 ms budget is what makes this hardware-independent: no GPU returns a
  completion inside one network round trip, so the assertion never depends on
  how fast the model is.

### Milestone C2 — L9 pipeline acceptance loop through a real model (spec-0027)

- **Failing test first:** `orch.dispatch_pipeline(["chat", "chat"], request,
  acceptance_fn=lambda r: True)` → `metadata["loop"]["steps_taken"] == 1`,
  `accepted` truthy; `acceptance_fn=lambda r: False, max_steps=2` →
  `MaxStepsExceeded` with `steps == 2`. Both outcomes are decided by the
  acceptance function, so the model's text never enters the oracle.
- **Depends on:** A0.

### Milestone C3 — L10 validated `plan-execute-review` at `temperature=0`

- **Failing test first:** `AgentSettings(validate_output=True, temperature=0.0)`
  for planner + reviewer via `Settings(agents=...)`; execute the shipped graph;
  the response content validates as `ReviewResult`. Failure mode is
  `LLMBadResponse`, reported as-is.
- **Depends on:** A0.
- Recorded honestly: this is the one scenario whose green depends on **model
  capability**. A model that cannot emit the `ExecutionPlan` JSON fails it on
  every device. The plan records which model id passed; it does not soften
  the oracle to make a weak model pass.

### Milestone C4 — L11 live `MODEL_OVERRIDE` (spec-0028)

- **Failing test first:** with `LMSTUDIO_OVERRIDE_MODEL` set, the same
  recording-factory oracle as B5 over the real `LMStudioClient`; unset →
  `pytest.skip("set LMSTUDIO_OVERRIDE_MODEL to run LM Studio override tests")`
  — sanctioned by the A2 prefix change.
- **Depends on:** A2, B5 (share the recording factory via
  `tests/integration/conftest.py` or `tests/fakes.py` — decide when writing
  B5; do not copy it).

### Milestone C5 — L12 `branch` + composite `fan_out` graph over `/workflows/run`

- **Failing test first:** `tests/lmstudio/test_workflow_run.py` — the graph
  from `scripts/run_workflow_e2e.py` plus a `branch` node, posted inline with
  `MANGOMAS_WORKFLOW__ENABLED=true` → 200, non-empty content, roster-ordered
  join. Replaces the script as the *test*; the script remains the demo and
  keeps `tests/test_run_workflow_e2e.py`.
- **Depends on:** A0, B4.

### Milestone C6 — Docs + CHANGELOG for PR C

- `docs/testing/lmstudio-e2e.md`: scenarios 7–12 in the existing format, plus
  a "Hardware" section pointing at spec-0029 R2 and the recorded GPU/CPU runs.
- `.claude/agents/mango-integration-runner.md` surface list gains the six
  files.
- CHANGELOG `[Unreleased]` › `Added`.

## PR D — Tier 3: device contract, `DEVICE` knob, nightly CPU home, parity meta-test (spec-0029 R5–R7)

### Milestone D0 — `MANGOMAS_EMBEDDINGS__DEVICE` (additive, default unset)

- **Failing test first:** `tests/adapters/embeddings/test_sentence_transformers.py`
  — inject a recording loader in place of `_lazy_load_model`; `device=None`
  records no `device` kwarg, `device="cpu"` records `{"device": "cpu"}`.
  `tests/test_config.py`: env round-trip into `Settings().embeddings.device`.
  `tests/deploy/test_env_example_contract.py` goes red until `.env.example`
  and the CLAUDE.md table carry the row — that test is the failing-first for
  the docs half.
- **Depends on:** nothing — parallel-safe with PRs B and C.
- `config/rag.py::EmbeddingSettings`: `DEFAULT_EMBEDDINGS_DEVICE: str | None
  = None`, field `device`.
  `adapters/embeddings/sentence_transformers.py`: `_lazy_load_model(model_name,
  device)` passes `device=` only when set. `composition/embeddings.py`
  forwards it. Owner: `mango-rag-dev`; config via the `mango-config` skill.

### Milestone D1 — R1 + R2 device contract in `tests/rag/test_end_to_end.py`

- **Failing test first:** R1 (finite; `embed` vs `embed_batch` dimension;
  self-cosine `1 ± EMBEDDING_ATOL`; batch order preserved) and R2 (top-1
  ranking identical under `device="cpu"` and auto). Add `EMBEDDING_ATOL`,
  the fixed corpus and query to `tests/constants.py`; add the file to
  `HARDWARE_CONTRACT_SCOPE`. Mutation for R2: swap the two corpus documents'
  ids in the forced-CPU store only → red.
- **Depends on:** D0 (R2 needs the device passthrough), A2 (scope table).

### Milestone D2 — R3 `ToolAgent` + `RetrievalTool` over real embeddings + Chroma

- **Failing test first:** `tests/rag/test_end_to_end_tool_agent.py` — the
  real-retrieval twin of `test_tool_agent_retrieval.py`: scripted `FakeLLM`
  tool call, real `Retriever`, assert the ingested chunk text appears in the
  re-injected `tool` message. Model text never enters the oracle.
- **Depends on:** D0.

### Milestone D3 — R4 `mangomas rag ingest` / `query` through the CLI with the real provider

- **Failing test first:** `tests/test_cli_rag_local.py` (gated
  `embeddings_local` + `rag`): env sets provider `sentence_transformers`,
  `MANGOMAS_VECTOR__PERSIST_DIR=<tmp_path>`, `DEVICE` from the env if the
  runner sets it; `ingest` a tmp corpus, `query` returns the expected top-1
  source. This is the only tier-3 test that goes through `build_orchestrator`,
  so it is the one that proves D0's composition wiring.
- **Depends on:** D0.

### Milestone D4 — Nightly `embeddings-local` job (CPU)

- **Failing test first:** D5's parity meta-test lists `RUN_EMBEDDINGS_LOCAL`
  as homeless until this job exists — write D5 first, watch it name this
  suite, then add the job.
- **Depends on:** D5 (for the failing-first), D1–D3 (something to run).
- `.github/workflows/nightly.yml`: job `embeddings-local` — `pip install
  torch --index-url https://download.pytorch.org/whl/cpu` **before**
  `pip install -e ".[dev,embeddings-local,rag]"` (the default Linux wheel
  bundles CUDA and is several GB; on a CPU runner that is pure cost);
  `actions/cache` on `~/.cache/huggingface` keyed on the model id constant;
  `make embeddings-local`. `notify.needs` gains the job. Pin the new action
  by SHA like the others; `tests/deploy/test_workflow_hardening.py` will
  enforce whatever it already enforces on the new job.
- `MANGOMAS_EMBEDDINGS__DEVICE` is **not** set in the job: the runner has no
  GPU, so auto-detect resolves to CPU and R2's parity assertion runs
  trivially-equal. Forcing it would hide a broken auto-detect.

### Milestone D5 — Gated-suite parity meta-test (`tests/deploy/test_gated_suite_homes.py`)

- **Failing test first:** before D4, it fails naming `RUN_EMBEDDINGS_LOCAL`;
  after D4 it passes. Both directions in a tmp tree: (a) a workflow that stops
  invoking `make postgres` → red; (b) `HOSTED_RUNNER_INFEASIBLE` listing a
  suite some workflow *does* invoke → red ("stale infeasibility claim").
- **Depends on:** nothing — parallel-safe; lands before D4 by design.
- `tests/constants.py`: `HOSTED_RUNNER_INFEASIBLE: dict[str, str]` — the
  reasons, recorded rather than implied: `RUN_LMSTUDIO` (needs a local LM
  Studio process), `RUN_VERTEX` / `RUN_GCP_SECRETS` / `RUN_GCP_TRACE`
  (credentials + project — roadmap decision D2), `RUN_LANGFUSE` (extra +
  credentials), `RUN_GITLEAKS` (already has a home — must **not** be listed;
  that is the stale-claim proof). The `RUN_*` → `make` target mapping is
  parsed from the Makefile's `RUN_X=1 $(PYTHON) -m pytest` lines, not
  restated.

### Milestone D6 — Docs + CHANGELOG for PR D

- CLAUDE.md config table + `.env.example` (`DEVICE`), `docs/testing/regression.md`
  (tier-3 rows + the nightly home), `README.md`'s embeddings table (the
  `device` column), `NEXT_STEPS.md` Phase 2 bullet marked ✅ for the
  executing-home + parity item.
- CHANGELOG `[Unreleased]` › `Added` / `Changed`.

## Deferred / out of scope

- **`dispatch_fan_out_settled` E2E.** It has no public consumer: the workflow
  `fan_out` node delegates to fail-fast `dispatch_fan_out`, and no HTTP route
  exposes the settled variant. An end-to-end test would have to invent a
  surface. Re-open when a graph-level `join` mode or route consumes it
  (spec-0027 records the sibling as additive; ADR-0027).
- **Stream-abandonment E2E.** Not deterministic over `httpx.ASGITransport`
  (see B1); stays a unit contract in `tests/test_streaming.py` /
  `tests/test_orchestrator.py` (spec-0025 / ADR-0025).
- **A CI home for LM Studio, Vertex, GCP, Langfuse.** Hosted runners cannot
  host LM Studio; the cloud suites wait on roadmap decision D2 (a GCP
  project). D5 records each reason so the gap is declared, not implied.
- **A GPU CI leg.** No hosted GPU runner is provisioned; the "agnostic"
  evidence for tier 2/3 is the recorded GPU + CPU run pair per PR (C, D), not
  a pipeline. Re-open if a self-hosted GPU runner appears.
- **Latency / throughput smoke ("perf smoke", roadmap Phase 2).** Explicitly
  excluded by spec-0029 R2.1 — a wall-clock assertion is the thing this plan
  forbids in the E2E tiers. A perf smoke needs its own suite, its own
  hardware baseline table, and its own gate semantics.
- **Forcing `DEVICE` for LM Studio.** LM Studio owns its own device
  placement; nothing in this repo can or should set it. The knob is for the
  in-process backend only.
- **`MANGOMAS_RAG__MIN_CHUNK_WORDS` inertness.** Unchanged; the tier-3 corpus
  uses `min_chunk_words=1` as today. Still the open retrieval-quality decision
  recorded in `NEXT_STEPS.md`.

## Verification

```bash
make gate                                   # full offline chain, CI's order
make gated-suites                           # tier 1 (RUN_INTEGRATION) + fakes-only rag — what CI runs
RUN_LMSTUDIO=1 LMSTUDIO_MODEL=<id> make lmstudio            # tier 2, run on a GPU box AND with LM Studio on CPU
RUN_LMSTUDIO=1 LMSTUDIO_MODEL=<id> LMSTUDIO_OVERRIDE_MODEL=<id2> make lmstudio   # + L11
pip install torch --index-url https://download.pytorch.org/whl/cpu && \
  pip install -e ".[dev,embeddings-local,rag]" && make embeddings-local       # tier 3 on CPU
MANGOMAS_EMBEDDINGS__DEVICE=cpu make embeddings-local        # tier 3 forced-CPU parity on a GPU box
python -m pytest tests/tooling/test_e2e_hardware_contract.py tests/deploy/test_gated_suite_homes.py -q   # the two meta-tests
```

Per-PR mutation proofs (run, reported in the PR body, not committed):

| PR | Mutation | Test that must go red |
|---|---|---|
| A | plant `assert elapsed < 5` in a tier-2 tmp file | hardware-contract lint |
| B | remove stream persistence in `_stream_agent` | I1 |
| B | remove the `asyncio.timeout` wrap | I2 (through HTTP) |
| B | `resolve_llm` ignores `extras` | I6 |
| B | drop the tenant row filter | I7 |
| C | remove the `asyncio.timeout` wrap | L8 (live) |
| D | swap corpus ids in the forced-CPU store only | R2 parity |
| D | delete `make postgres` from `nightly.yml` in a tmp copy | gated-suite parity |
| D | list `RUN_GITLEAKS` as infeasible | gated-suite parity (stale claim) |
