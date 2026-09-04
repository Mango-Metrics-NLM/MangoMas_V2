# Spec-0029: Hardware-agnostic end-to-end suites for the Phase-1 deliveries

- **Status:** Draft
- **Linked ADR:** _none — no boundary change_ (one additive config field; no
  protocol, error type or registry key changes)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added` (per landed milestone —
  see `docs/plans/20260904T030302Z-hardware-agnostic-e2e-plan.md`)

## Problem

The roadmap's Phase-1 deliveries — streaming turn persistence (spec-0025),
`LoopSettings` wiring + `StepTimeout` (spec-0026), whole-pipeline acceptance
loops + settled fan-out (spec-0027), per-agent `MODEL_OVERRIDE` (spec-0028),
structured-output validation and the shipped `plan-execute-review` graph — all
landed with unit tests and mutation proofs, but almost none of them has an
**end-to-end** test: a request entering at a public boundary (HTTP, CLI) and
leaving through a persisted turn or an error envelope, with the composition
root in between. Concretely, as of `728bd72`:

- `tests/integration/` — the suite CI actually runs on every push via
  `make gated-suites` — has **one** effective test (`test_invoke_flow_persists_turn`).
  The roadmap analysis already flagged that "the CI job's name overpromises".
- `tests/lmstudio/` ships scenarios 1–6 (ping, chat, stream, fallback,
  summarize, 502) and predates every spec above. No live-model test covers
  stream persistence, the per-step timeout, a pipeline acceptance loop, a
  validated planner, a model override, or a `branch` / composite `fan_out`
  graph. `scripts/run_workflow_e2e.py` is a demo, not a test.
- `tests/rag/test_end_to_end.py` is the only test that runs real local
  compute (sentence-transformers → Chroma). It has no device contract: nothing
  says what must hold identically on CUDA, MPS and CPU, and nothing forces CPU
  on a GPU box to find out.

The live suites also carry a **hardware coupling that is a latent defect
today**: `tests/lmstudio/conftest.py` gives the LLM adapter a 240 s budget
"because CPU runs can take well over 60 s", while every scenario passes
`timeout=HTTPX_REQUEST_TIMEOUT_SECONDS` (60 s) to the httpx *client*. On a
GPU box the completion finishes inside 60 s and the mismatch is invisible; on
a CPU box the client times out before the adapter's budget is reached, so the
same suite is green on one machine and red on another for a reason that has
nothing to do with the code under test. The budget is also a bare constant,
not env-overridable, contradicting the fixture docstring's own claim.

The intended outcome: every Phase-1 delivery has an end-to-end scenario in the
tier where it can actually execute; the live-model and local-compute tiers
follow one written, mechanically-linted hardware contract; and — closing
roadmap item 2.1 — every gated suite either has an executing home in CI or
carries a recorded infeasibility reason that a meta-test keeps honest.

## Requirements

- **R1 — Three tiers, by executing home.** Each scenario is placed by where it
  can run, never by what it tests:

  | Tier | Directory | Gate | Backing | Executing home | Hardware exposure |
  |---|---|---|---|---|---|
  | 1 — in-process | `tests/integration/` | `RUN_INTEGRATION=1` | fakes over ASGI, real composition root | every CI push (`make gated-suites`) | none by construction |
  | 2 — live model | `tests/lmstudio/` (`tests/vertex/` mirrors) | `RUN_LMSTUDIO=1` | real LLM over HTTP | manual (hosted runners cannot host LM Studio) | latency, model capability |
  | 3 — local compute | `tests/rag/test_end_to_end.py` + siblings | `RUN_EMBEDDINGS_LOCAL=1 RUN_RAG=1` | sentence-transformers + Chroma in-process | **new** nightly CPU job (R6) | device (CUDA / MPS / CPU), float drift |

- **R2 — The hardware-agnostic contract.** Tier-2 and tier-3 tests obey six
  rules, recorded here and linted by a meta-test (R7):
  1. **No wall-clock assertions.** A test never asserts on elapsed time. Budgets
     exist only to bound a hang; they are read from `tests/constants.py` or an
     env override, sized for the slowest supported hardware (CPU), and the
     client-side budget is never smaller than the adapter-side budget.
  2. **No exact numerics from a model.** Embedding vectors are compared by
     cosine similarity within a tolerance (`EMBEDDING_ATOL` in
     `tests/constants.py`), never by equality; retrieval assertions compare
     **rankings** (which chunk is top-1), never raw scores.
  3. **No exact text from a model.** Live-LLM assertions are structural:
     non-empty content, JSON that validates against the agent's schema, SSE
     frame kinds and order, the persisted turn, the error envelope's `code`.
     Where structure depends on sampling, the request pins `temperature=0`,
     but the test still never compares two completions for equality.
  4. **No device literals.** The strings `cuda`, `mps` and `cpu` appear in
     tests only via `tests/constants.py`; device selection is the library's
     auto-detect unless the operator sets `MANGOMAS_EMBEDDINGS__DEVICE` (R5).
     Forcing CPU is a parity check, never a precondition.
  5. **Timeouts that must fire are below any hardware's floor.** A live
     `StepTimeout` scenario uses a budget below one network round trip
     (`LIVE_STEP_TIMEOUT_SECONDS`, order of 1 ms), so it trips on a GPU
     exactly as it does on a CPU. Timeouts that must *not* fire use the
     CPU-sized budget from rule 1.
  6. **Deterministic teardown on every device.** Every adapter is closed
     (`aclose`) via the orchestrator's single teardown path or an explicit
     `finally`; Chroma persists only under `tmp_path`; no test leaves a model
     resident that the next test must share.

- **R3 — Tier-1 scenarios (fakes over ASGI, real composition root).** One
  file per delivery; all drive `build_orchestrator(Settings())` after env
  monkeypatching and inject that orchestrator into `create_app`, so the env →
  `Settings` → adapters → orchestrator path is exercised without the
  lifespan's `configure_telemetry` (which resets root logging and breaks
  `caplog`; the lifespan itself is already pinned by `tests/test_api.py`).

  | Id | Delivery | Boundary in → out | Oracle |
  |---|---|---|---|
  | I1 | spec-0025 | `POST /agents/chat/stream` → `GET /history` | fully drained stream appears in history with `agent == "chat"`; a stream whose LLM raises mid-way (`FakeLLM.raise_on_stream`) persists nothing; the `metadata` SSE event is emitted |
  | I2 | spec-0026 | `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS=<tiny>` → `POST /agents/chat/invoke` | 504 with `error == "step_timeout"`; no turn persisted. Needs `FakeLLM.delay_seconds` (R8) injected via `llm_registry.scoped` |
  | I3 | spec-0026 | `MANGOMAS_LOOP__MAX_STEPS=<n>` → invoke | `metadata["loop"]` reports the composition-wired budget tier (env beats field default; explicit `request.max_steps` beats env) |
  | I4 | spec-0027 + validation + shipped graph | `POST /workflows/run` with `examples/workflows/plan-execute-review.json`, `MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT=true` (and reviewer) | 200 whose content validates as `ReviewResult`; the negative direction — planner reply is not an `ExecutionPlan` — returns the `llm_bad_response` envelope |
  | I5 | spec-0012 / 0013 | `POST /workflows/run` with a `branch` graph and a composite `fan_out` graph | predicate routes to the expected agent; composite branch output is joined in roster order |
  | I6 | spec-0028 | `MANGOMAS_AGENTS__CHAT__MODEL_OVERRIDE=<other>` → invoke | a recording factory under `llm_registry.scoped` proves the override client (built with the override model id) received the call and `ctx.llm` did not; orchestrator `aclose` closes both clients |
  | I7 | spec-0007 + spec-0025 | two `X-Tenant-ID` values through invoke **and** stream → `GET /history` per tenant | each tenant sees only its own turns, streamed turns included |

  Deliberately **not** duplicated in tier 1: auth fail-closed, CORS, 413/503
  backpressure, `GET /history` limits — `tests/test_auth.py`,
  `tests/test_backpressure.py`, `tests/test_api_history_cors.py` already drive
  those through the full ASGI app; and the CLI `chat → history` round trip,
  already in `tests/test_cli.py`.

- **R4 — Tier-2 scenarios (live LM Studio).** Extends
  `docs/testing/lmstudio-e2e.md` with scenarios 7–12:

  | Id | Delivery | Oracle (structural only — R2.3) |
  |---|---|---|
  | L7 | spec-0025 | after a fully drained `/agents/chat/stream`, the repo holds one `chat` turn |
  | L8 | spec-0026 | `LoopSettings(step_timeout_seconds=LIVE_STEP_TIMEOUT_SECONDS)` → 504 `step_timeout` (R2.5); the sibling with the CPU-sized budget returns 200 |
  | L9 | spec-0027 | `dispatch_pipeline(["chat", "chat"], acceptance_fn=<always-accept>)` → `metadata["loop"]["steps_taken"] == 1`; `acceptance_fn=<never-accept>, max_steps=2` → `MaxStepsExceeded(steps=2)`. Both outcomes are fixed by the acceptance function, not by what the model says |
  | L10 | validation + shipped graph | the `plan-execute-review` graph with `validate_output=True` at `temperature=0` completes and the reviewer reply validates as `ReviewResult`. This is the one scenario whose green depends on **model capability** rather than hardware; the failure mode is a typed `LLMBadResponse`, which is itself the finding |
  | L11 | spec-0028 | with `LMSTUDIO_OVERRIDE_MODEL` set to a second loaded model, the override client receives the call (same recording-factory oracle as I6, over the real adapter). Runtime-skips with the sanctioned `set LMSTUDIO_OVERRIDE_MODEL to run …` reason when unset (R9) |
  | L12 | spec-0012 / 0013 | `POST /workflows/run` with a `branch` + composite-`fan_out` graph returns 200 with non-empty joined content; supersedes the demo in `scripts/run_workflow_e2e.py` as the *test* (the script stays as the demo) |

  Plus the conftest fix: `LMSTUDIO_E2E_TIMEOUT_SECONDS` becomes
  env-overridable, and every scenario's httpx client timeout is derived from
  it (never below it) rather than from the 60 s `HTTPX_REQUEST_TIMEOUT_SECONDS`.

- **R5 — Tier-3 device contract + one additive config field.**
  `MANGOMAS_EMBEDDINGS__DEVICE` (default unset) is forwarded as
  `SentenceTransformer(model_name, device=...)`; unset preserves the library's
  auto-detect exactly as today. It is both the operator knob (force CPU on a
  box whose GPU is serving LM Studio) and the only way one test run can
  compare forced-CPU against auto on the same machine. Scenarios:

  | Id | Oracle |
  |---|---|
  | R1 | vectors are finite; `embed` and `embed_batch` agree on dimension; self cosine similarity is `1 ± EMBEDDING_ATOL`; batch order is preserved (row *i* ≈ `embed(text_i)` within tolerance) |
  | R2 | top-1 ranking for the fixed corpus + query is identical under `device=cpu` and under auto-detect (trivially equal when auto resolves to CPU — the test still runs, it does not skip) |
  | R3 | `ToolAgent` + `RetrievalTool` over real embeddings + Chroma with a scripted `FakeLLM` tool call: the retrieved chunk text is present in the re-injected `tool` message (real-retrieval twin of `tests/rag/test_tool_agent_retrieval.py`) |
  | R4 | `mangomas rag ingest` then `mangomas rag query` through the CLI with `MANGOMAS_EMBEDDINGS__PROVIDER=sentence_transformers` and a `tmp_path` persist dir — the full composition path including `DEVICE` |

- **R6 — Executing homes.** A `embeddings-local` job in
  `.github/workflows/nightly.yml` runs `make embeddings-local` on a CPU
  runner, installing **CPU torch wheels first** (`--extra-index-url
  https://download.pytorch.org/whl/cpu`; the default Linux wheel bundles CUDA
  libraries and is several GB) and caching the Hugging Face model directory
  keyed on the model id. The `notify` job's `needs` list gains the new job.
- **R7 — Two meta-tests, both mutation-proven both ways** (Scenarios):
  1. `tests/tooling/test_e2e_hardware_contract.py` lints the tier-2/tier-3
     files for R2's mechanically checkable rules: no `assert` mentioning
     `perf_counter` / `monotonic` / `elapsed`; no `cuda` / `mps` / `cpu`
     literal outside `tests/constants.py`; no `==` between two calls of
     `embed`/`embed_batch`; every `timeout=` keyword references a name, not a
     number. It is a lint over source text, and says so: it catches the
     patterns this repo has actually written, not every possible violation.
  2. `tests/deploy/test_gated_suite_homes.py` asserts that every entry in
     `ENV_GATE_SUITES` maps to a `make` target that some workflow under
     `.github/workflows/` invokes, **or** appears in a new
     `HOSTED_RUNNER_INFEASIBLE: dict[str, str]` table in `tests/constants.py`
     with a reason — and that no suite appears in both (a stale infeasibility
     claim is the fail-open shape).
- **R8 — Fake growth (additive).** `FakeLLM` gains `delay_seconds: float =
  0.0`, awaited before `complete` / the first stream chunk. Default `0.0` is
  byte-identical to today; `mango-fake-builder` owns the edit.
- **R9 — Skip discipline.** `GATED_RUNTIME_SKIP_REASON_PREFIXES` gains
  `"LMSTUDIO_"` so L11's runtime skip is sanctioned by the zero-skip guard;
  the guard's subprocess proof (`tests/tooling/test_collection_gate.py`)
  gains the LM Studio-prefixed case. No other skip, and no `xfail`, anywhere
  in this spec.
- Must remain **additive & default-OFF**: `DEVICE` unset → unchanged
  auto-detect; `delay_seconds=0.0` → unchanged fake; every new test is behind
  an existing `RUN_*` gate; the default `pytest` run touches no network and
  no model.

## Scenarios (WHEN/THEN)

- WHEN `MANGOMAS_EMBEDDINGS__DEVICE` is unset THEN
  `SentenceTransformersEmbeddingClient` constructs the model without a
  `device` argument (pinned by the injected-fake constructor test — the
  recorded kwargs are `{}`); WHEN it is `cpu` THEN the recorded kwargs are
  `{"device": "cpu"}`.
- WHEN the hardware-contract lint sees a tier-2 file containing
  `assert elapsed < 5` THEN it fails naming the file and line; WHEN the same
  file reads the budget from `tests.constants` and asserts nothing about time
  THEN it passes. Proven in a subprocess over a tmp tree, the way
  `test_collection_gate.py` does, so the real lint runs against a planted
  violation.
- WHEN a workflow file stops invoking `make postgres` THEN
  `test_gated_suite_homes` fails for `RUN_POSTGRES` (guard can fire); WHEN
  `RUN_LMSTUDIO` is listed in `HOSTED_RUNNER_INFEASIBLE` with its reason THEN
  it passes; WHEN a suite is listed as infeasible **and** a workflow invokes
  its target THEN it fails (stale claim).
- WHEN the composition-wired step budget is `TINY_STEP_TIMEOUT_SECONDS` and
  the fake LLM delays longer THEN `POST /agents/chat/invoke` returns 504
  `step_timeout` and `GET /history` is empty; WHEN the budget is the default
  THEN the same request returns 200 and one turn is persisted.
- WHEN a planner reply fails `ExecutionPlan` validation with
  `VALIDATE_OUTPUT=true` THEN `POST /workflows/run` returns the
  `llm_bad_response` envelope; WHEN validation is off (default) THEN the same
  reply flows through unchanged (the back-compat direction).
- WHEN `LMSTUDIO_OVERRIDE_MODEL` is unset THEN L11 skips with exactly
  `set LMSTUDIO_OVERRIDE_MODEL to run LM Studio override tests` and the
  zero-skip guard stays green; WHEN a test skips with any other LM Studio
  reason THEN the guard fails the session (existing behaviour, re-proven with
  the new prefix).

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_EMBEDDINGS__DEVICE` | _(unset)_ | Torch device passed to `SentenceTransformer(device=...)`; unset = library auto-detect (CUDA → MPS → CPU). `sentence_transformers` provider only; ignored by `lmstudio` / `vertex` |

Test-scope env (no `MANGOMAS_` prefix, defined in `tests/constants.py`):

| Env var | Default | Purpose |
|---|---|---|
| `LMSTUDIO_E2E_TIMEOUT_SECONDS` | `240` | Adapter budget for every live scenario; the httpx client budget is derived from it |
| `LMSTUDIO_OVERRIDE_MODEL` | _(unset → L11 skips)_ | Second loaded model id for the `MODEL_OVERRIDE` scenario |

`DEVICE` is surfaced as `DEFAULT_EMBEDDINGS_DEVICE: str | None = None` on
`EmbeddingsSettings`; `.env.example` and the CLAUDE.md table gain the row so
`tests/deploy/test_env_example_contract.py` (names both directions + default
parity) stays green.

## Protocol / contract impact

- New/changed protocols: _none_. `EmbeddingClient` is untouched; the device
  is a constructor concern of one backend.
- New error types: _none_.
- Registry additions: _none_.
- Test doubles: `FakeLLM.delay_seconds` (additive, default `0.0`).

## Backwards-compatibility

- `MANGOMAS_EMBEDDINGS__DEVICE` unset → `_lazy_load_model` calls
  `SentenceTransformer(model_name)` with no `device` kwarg, byte-identical to
  today (regression-pinned by the recorded-kwargs test above).
- `FakeLLM()` with the default `delay_seconds=0.0` performs no `sleep`; every
  existing fake-backed test is unaffected.
- Existing LM Studio scenarios 1–6 keep their oracles; only their client
  timeout source changes (from a fixed 60 s to the derived budget), which can
  only make a currently-green run stay green.
- No new `RUN_*` gate. All new tests sit behind `RUN_INTEGRATION`,
  `RUN_LMSTUDIO`, or `RUN_EMBEDDINGS_LOCAL` + `RUN_RAG`, so the default unit
  run collects and skips them under the already-sanctioned reasons.

## Test plan

- Unit (measured by the 95 % gate): `tests/adapters/embeddings/test_sentence_transformers.py`
  (device kwarg both directions, via a recording loader injected in place of
  `_lazy_load_model`); `tests/test_config.py` (`DEVICE` env round-trip);
  `tests/test_fakes.py` or the nearest existing fake test (`delay_seconds`
  default no-op + delay honoured); `tests/tooling/test_e2e_hardware_contract.py`
  and `tests/deploy/test_gated_suite_homes.py` (both directions each, in a
  subprocess / tmp tree); `tests/tooling/test_collection_gate.py` (`LMSTUDIO_`
  prefix case).
- Gated tier 1 (`RUN_INTEGRATION=1`, runs in CI): `tests/integration/conftest.py`
  (`composed_app` fixture) + `test_stream_persistence_flow.py`,
  `test_loop_settings_flow.py`, `test_workflow_http_flow.py`,
  `test_model_override_flow.py`, `test_tenancy_history_flow.py`.
- Gated tier 2 (`RUN_LMSTUDIO=1`, manual): `tests/lmstudio/test_stream_persists.py`,
  `test_step_timeout.py`, `test_pipeline_acceptance.py`,
  `test_plan_execute_review.py`, `test_model_override.py`,
  `test_workflow_run.py`; conftest budget derivation.
- Gated tier 3 (`RUN_EMBEDDINGS_LOCAL=1 RUN_RAG=1`, nightly CPU):
  `tests/rag/test_end_to_end.py` gains R1/R2; `test_end_to_end_tool_agent.py`
  (R3); `tests/test_cli_rag_local.py` (R4).
- Gates: both meta-tests are mutation-proven both ways (Scenarios); the
  step-timeout scenarios are proven to fire by running them once with the
  timeout wrap removed (the spec-0026 mutation, re-run through HTTP).
- Coverage: E2E suites run `--no-cov` (Makefile targets) and move no floor;
  the unit-side additions keep `adapters ≥ 85 %`, `config ≥ 95 %`, and the
  global 95 %. `scripts/` is untouched.

## Acceptance criteria

- [ ] Feature off by default → no behaviour change (device kwarg absent;
      `FakeLLM` no-op delay; default `pytest` run collects the new tests under
      sanctioned skips only).
- [ ] Feature on via env → documented behaviour (`DEVICE` forwarded;
      `LMSTUDIO_E2E_TIMEOUT_SECONDS` honoured by adapter and client alike).
- [ ] Every Phase-1 delivery (specs 0025–0028, validation + shipped graph,
      `branch` / composite `fan_out`) has at least one tier-1 scenario green
      in CI and one tier-2 scenario green against a local LM Studio (recorded
      run: date, model id, hardware — GPU **and** CPU — in the plan).
- [ ] Tier-3 R1–R4 green on the nightly CPU job **and** on one GPU box, with
      the R2 parity assertion exercised on the GPU box.
- [ ] Both meta-tests land with their mutation proofs recorded in the plan.
- [ ] The 60 s-vs-240 s client/adapter budget mismatch is gone from
      `tests/lmstudio/` (grep-provable: no `HTTPX_REQUEST_TIMEOUT_SECONDS` use
      remains under `tests/lmstudio/`).
- [ ] `ruff`, `mypy`, `pytest` (95 % gate + floors), `frontmatter-lint`,
      `make gated-suites` all clean.
- [ ] CHANGELOG updated per milestone; `docs/testing/lmstudio-e2e.md`
      scenarios 7–12 and `docs/testing/regression.md` suite inventory updated;
      no ADR (no boundary changed).
