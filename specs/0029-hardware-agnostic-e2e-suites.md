# Spec-0029: Hardware-agnostic end-to-end suites for the Phase-1 deliveries

- **Status:** Implemented
- **Linked ADR:** _none — no boundary change_ (one additive `EmbeddingSettings`
  field; no protocol, error type or registry key changes)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added` / `Fixed`

## Problem

The roadmap's Phase-1 deliveries — streaming turn persistence (spec-0025),
`LoopSettings` wiring + `StepTimeout` (spec-0026), whole-pipeline acceptance
loops + settled fan-out (spec-0027), per-agent `MODEL_OVERRIDE` (spec-0028),
structured-output validation and the shipped `plan-execute-review` graph — all
landed with unit tests and mutation proofs, but none of them has an
**end-to-end** test: a request entering at a public boundary and leaving
through a persisted turn or an error envelope, with the **composition root**
(env → `Settings` → adapters → orchestrator) in between. Concretely, at
`728bd72`:

- `tests/integration/` — the suite CI runs on every push via
  `make gated-suites` — has **one** effective test
  (`test_invoke_flow_persists_turn`), and it hand-builds an `Orchestrator`
  rather than going through `build_orchestrator`. The roadmap analysis
  already flagged that "the CI job's name overpromises".
- Nothing anywhere proves an `MANGOMAS_*` env var reaches a running HTTP
  request. `tests/test_control_loop.py` proves `LoopSettings` works when
  passed to `Orchestrator(...)` directly; nothing proves
  `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS` becomes a 504 envelope.
- `tests/test_streaming.py` builds its orchestrator with `repo=None`
  (`tests/test_streaming.py:26`), so spec-0025's central claim — a streamed
  turn is persisted and visible in `GET /history` — has **no test at the HTTP
  boundary at all**.
- `tests/lmstudio/` ships scenarios 1–6 and predates every spec above. No
  live-model test covers stream persistence, the per-step timeout, a pipeline
  acceptance loop, a validated planner, a model override, or a `branch` /
  composite `fan_out` graph. `scripts/run_workflow_e2e.py` is a demo, not a
  test.
- `tests/rag/test_end_to_end.py` is the only test that runs real local
  compute. It has no device contract: nothing states what must hold
  identically on CUDA, MPS and CPU, and nothing forces CPU on a GPU box to
  find out.

The live suites also carry a **hardware coupling that is a latent defect
today**. `tests/lmstudio/conftest.py:68` gives the adapter a 240 s budget
"because CPU runs can take well over 60 s", while six scenarios across
`tests/lmstudio/` **and `tests/vertex/`** pass `timeout=HTTPX_REQUEST_TIMEOUT_SECONDS`
(60 s, `tests/constants.py:83`) to the httpx *client*. On a GPU box the
completion finishes inside 60 s and the mismatch is invisible; on a CPU box
the client times out before the adapter's budget is reached, so the same
suite is green on one machine and red on another for a reason that has
nothing to do with the code under test. `tests/vertex/conftest.py:55`
duplicates the same 240 s budget and inherits the same defect. Neither
budget is env-overridable, contradicting both fixture docstrings' claim that
"every value can be overridden via env vars".

Intended outcome: every Phase-1 delivery has an end-to-end scenario in the
tier where it can execute; the live-model and local-compute tiers follow one
written, mechanically-linted hardware contract; and — closing roadmap item
2.1 — every gated suite either has an executing home in CI or carries a
recorded infeasibility reason that a meta-test keeps honest.

## Requirements

- **R1 — Three tiers, placed by executing home.** A scenario is placed by
  where it can run, never by what it tests:

  | Tier | Directory | Gate | Backing | Executing home | Hardware exposure |
  |---|---|---|---|---|---|
  | 1 — in-process | `tests/integration/` | `RUN_INTEGRATION=1` | fakes over ASGI, **real composition root** | every CI push (`make gated-suites`) | none by construction |
  | 2 — live model | `tests/lmstudio/`, `tests/vertex/` | `RUN_LMSTUDIO=1` / `RUN_VERTEX=1` | real LLM over HTTP | manual (no hosted runner can host LM Studio; Vertex needs credentials) | latency; model capability |
  | 3 — local compute | `tests/rag/` | `RUN_EMBEDDINGS_LOCAL=1` + `RUN_RAG=1` | sentence-transformers + Chroma in-process | **new** nightly CPU job (R6) | torch device, float drift |

  Tier 1 is where a delivery is *proven*; tiers 2 and 3 are where it is
  *confirmed against reality*. A scenario that can be written in tier 1 is
  never written only in tier 2.

- **R2 — The hardware-agnostic contract.** Tier-2 and tier-3 tests obey six
  rules, recorded here and linted by R7's meta-test:
  1. **No wall-clock assertions.** A test never asserts on elapsed time.
     Budgets exist only to bound a hang; they come from `tests/constants.py`
     or an env override, are sized for the slowest supported hardware (CPU),
     and **the client-side budget is never smaller than the adapter-side
     budget**.
  2. **No exact numerics from a model.** Embedding vectors are compared by
     cosine similarity within `EMBEDDING_COSINE_ATOL`, never by equality;
     retrieval assertions compare **rankings** (which chunk is top-1), never
     raw scores.
  3. **No exact text from a model.** Live-LLM assertions are structural:
     non-empty content, JSON that validates against the agent's schema, SSE
     frame kinds and order, the persisted turn, the error envelope's `code`.
     Two completions are never compared for equality.
  4. **No device literals.** `cuda` / `mps` / `cpu` appear in tests only via
     `tests/constants.py`. Device selection is the library's auto-detect
     unless the operator sets `MANGOMAS_EMBEDDINGS__DEVICE` (R5). Forcing CPU
     is a parity check, never a precondition.
  5. **Timeouts that must fire are below any hardware's floor.** A live
     `StepTimeout` scenario uses `LIVE_STEP_TIMEOUT_SECONDS` (order of 1 ms),
     below one network round trip, so it trips on a GPU exactly as on a CPU.
     Timeouts that must *not* fire use the CPU-sized budget from rule 1.
  6. **Deterministic teardown on every device.** Every adapter is closed via
     the orchestrator's single teardown path or an explicit `finally`; Chroma
     persists only under `tmp_path`; no test leaves a model resident that the
     next test must share.

- **R3 — Tier-1 scenarios.** A shared `composed_app` fixture builds the app
  through `build_orchestrator(Settings())` after env monkeypatching and
  injects it into `create_app(orchestrator=...)`, swapping the LLM via
  `llm_registry.scoped` — the idiom `tests/lmstudio/test_stream_fallback.py:86`
  and `tests/test_composition.py:277` already use. The lifespan is skipped
  deliberately: it calls `configure_telemetry`, whose
  `logging.basicConfig(force=True)` rips out pytest's `caplog` handlers. The
  lifespan itself is already pinned by `tests/test_api.py:129`.

  | Id | Delivery | Boundary in → out | Oracle |
  |---|---|---|---|
  | I1 | spec-0025 | `POST /agents/chat/stream` → `GET /history` | a fully drained stream persists exactly one `chat` turn; a stream whose LLM raises mid-way persists none. **New because** every existing streaming test runs with `repo=None` |
  | I2 | spec-0026 | `MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS` → `POST /agents/chat/invoke` | 504 with envelope `code == "step_timeout"`, `GET /history` empty; the default-budget sibling returns 200 with one turn. Needs `FakeLLM.delay_seconds` (R8) |
  | I3 | spec-0026 | `MANGOMAS_LOOP__MAX_STEPS=n` → invoke | `metadata["loop"]["steps_taken"] == n` and the fake recorded `n` calls; a body-supplied `max_steps` overrides the env (the non-default-request tier of `_effective_max_steps`) |
  | I4 | spec-0027 + validation + shipped graph | `POST /workflows/run` with the **shipped** `examples/workflows/plan-execute-review.json`, `MANGOMAS_AGENTS__{PLANNER,REVIEWER}__VALIDATE_OUTPUT=true` | 200 whose content validates as `ReviewResult`; the negative — planner emits prose — returns the `llm_bad_response` envelope; with validation off the same prose flows through (back-compat direction) |
  | I5 | specs 0012 / 0013 | `POST /workflows/run` with a `branch` graph and a composite `fan_out` graph | the predicate routes to the expected agent; the composite branch joins in roster order. **New because** `tests/test_workflow_api.py` covers agent/fan_out/loop only, and hand-builds its orchestrator |
  | I6 | spec-0028 | `MANGOMAS_AGENTS__CHAT__MODEL_OVERRIDE` → invoke | a recording factory under `llm_registry.scoped` proves the client built with the override model id received the call and the base client did not; `aclose` closes both |
  | I7 | specs 0007 + 0025 | two `X-Tenant-ID` values through **stream** → `GET /history` | each tenant sees only its own **streamed** turn. Scoped to streaming: `tests/test_tenancy.py:114` already covers the invoke path |

  Deliberately not duplicated: auth fail-closed, CORS, 413/503 backpressure,
  `/history` limits, the SSE `metadata` frame, and the CLI `chat → history`
  round trip — all already driven through the full ASGI app by
  `tests/test_auth.py`, `tests/test_backpressure.py`,
  `tests/test_api_history_cors.py`, `tests/test_streaming.py:136` and
  `tests/test_cli.py:74`.

- **R4 — Tier-2 scenarios (live LM Studio), scenarios 7–12.** Oracles are
  structural only (R2.3):

  | Id | Delivery | Oracle |
  |---|---|---|
  | L7 | spec-0025 | after a fully drained `/agents/chat/stream`, the repo holds one `chat` turn |
  | L8 | spec-0026 | `LIVE_STEP_TIMEOUT_SECONDS` → 504 `step_timeout`; the CPU-sized-budget sibling returns 200 |
  | L9 | spec-0027 | `dispatch_pipeline(acceptance_fn=<always>)` → `steps_taken == 1`; `<never>, max_steps=2` → `MaxStepsExceeded(steps=2)`. Both outcomes fixed by the acceptance function, never by model text |
  | L10 | validation + shipped graph | the shipped graph with `validate_output=True` at `temperature=0` completes and the reply validates as `ReviewResult`. The one scenario whose green depends on **model capability**; its failure mode is a typed `LLMBadResponse`, which is itself the finding |
  | L11 | spec-0028 | with `LMSTUDIO_OVERRIDE_MODEL` set, the same recording-factory oracle as I6 over the real adapter; unset → sanctioned runtime skip (R9) |
  | L12 | specs 0012 / 0013 | `POST /workflows/run` with a `branch` + composite-`fan_out` graph returns 200 with non-empty joined content. Supersedes `scripts/run_workflow_e2e.py` as the *test*; the script stays the demo |

  Plus the budget fix: `LMSTUDIO_E2E_TIMEOUT_SECONDS` / `VERTEX_E2E_TIMEOUT_SECONDS`
  become env-overridable, and **every** live scenario's httpx client budget is
  derived from the adapter budget (never below it), in `tests/vertex/` as well
  as `tests/lmstudio/`.

- **R5 — Tier-3 device contract + one additive config field.**
  `MANGOMAS_EMBEDDINGS__DEVICE` (default unset) is forwarded to
  `SentenceTransformer(model_name, device=...)`; unset preserves the library's
  auto-detect exactly as today. It is both the operator knob (force CPU on a
  box whose GPU is serving LM Studio) and the only way one run can compare
  forced-CPU against auto on the same machine. Scenarios:

  | Id | Oracle |
  |---|---|
  | E1 | vectors are finite; `embed` and `embed_batch` agree on dimension; self cosine similarity is `1 ± EMBEDDING_COSINE_ATOL`; batch order preserved (row *i* ≈ `embed(text_i)`) |
  | E2 | top-1 ranking for the fixed corpus + query is identical under `device=cpu` and under auto-detect (trivially equal when auto resolves to CPU — the test still runs, it does not skip) |
  | E3 | `ToolAgent` + `RetrievalTool` over **real** embeddings + Chroma with a scripted `FakeLLM` tool call: the retrieved chunk text reaches the re-injected `tool` message (real-retrieval twin of `tests/rag/test_tool_agent_retrieval.py`) |
  | E4 | `mangomas rag ingest` then `mangomas rag query` through the CLI with `MANGOMAS_EMBEDDINGS__PROVIDER=sentence_transformers` and a `tmp_path` persist dir — the only tier-3 scenario that goes through `build_orchestrator`, so it is what proves the `DEVICE` composition wiring |

- **R6 — Executing homes.** A nightly `embeddings-local` job runs
  `make embeddings-local` on a CPU runner, installing **CPU torch wheels
  first** (the default Linux wheel bundles CUDA and is several GB) and caching
  the Hugging Face model directory. The `notify` job's `needs` gains it, and
  `tests/deploy/test_ci_make_parity.py::test_nightly_jobs_delegate_to_make`
  — which asserts the nightly jobs' exact command lists — is extended in the
  same commit.
- **R7 — Two meta-tests, both mutation-proven in both directions:**
  1. `tests/tooling/test_e2e_hardware_contract.py` lints the tier-2/tier-3
     files for R2's mechanically checkable rules. It is a **text lint over the
     patterns this repo has actually written**, and says so — it catches
     regression to known-bad shapes, it does not prove agnosticism.
  2. A gated-suite parity guard asserting every `ENV_GATE_SUITES` entry maps
     to a `make` target some workflow invokes, **or** appears in
     `HOSTED_RUNNER_INFEASIBLE` with a reason — and never both (a stale
     infeasibility claim is the fail-open shape). It reuses
     `tests/deploy/_workflows.py` and the existing `_make_target_body`
     parser rather than adding a third workflow reader.
- **R8 — Fake growth (additive).** `FakeLLM` gains `delay_seconds: float = 0.0`,
  awaited before `complete` returns and before the first stream chunk. Default
  `0.0` performs no `sleep` at all, so every existing fake-backed test is
  byte-identical.
- **R9 — Skip discipline.** `GATED_RUNTIME_SKIP_REASON_PREFIXES` gains
  `"LMSTUDIO_"` so L11's runtime skip is sanctioned by the zero-skip guard,
  with the subprocess proof in `tests/tooling/test_collection_gate.py`
  extended to cover it. No other skip and no `xfail` anywhere in this spec.
- Must remain **additive & default-OFF**: `DEVICE` unset → unchanged
  auto-detect; `delay_seconds=0.0` → unchanged fake; every new test sits
  behind an existing `RUN_*` gate; the default `pytest` run touches no network
  and loads no model.

## Scenarios (WHEN/THEN)

- WHEN `MANGOMAS_EMBEDDINGS__DEVICE` is unset THEN the sentence-transformers
  loader is called with no `device` argument; WHEN it is `cpu` THEN the loader
  records `device="cpu"`. Both directions, through the composition factory.
- WHEN the hardware-contract lint sees a scoped file containing
  `assert elapsed < 5`, a bare `"cuda"` literal, or a numeric `timeout=`
  THEN it fails naming file and line; WHEN the real tree is scanned after the
  budget fix THEN it passes. Proven in a subprocess over a tmp tree, the way
  `tests/tooling/test_collection_gate.py` proves the collection gate.
- WHEN a workflow file stops invoking `make postgres` THEN the parity guard
  fails for `RUN_POSTGRES`; WHEN `RUN_LMSTUDIO` is listed in
  `HOSTED_RUNNER_INFEASIBLE` with its reason THEN it passes; WHEN a suite is
  both listed infeasible **and** invoked by a workflow THEN it fails.
- WHEN the composition-wired step budget is tiny and the fake LLM delays
  longer THEN `POST /agents/chat/invoke` returns 504 `step_timeout` and
  `GET /history` is empty; WHEN the budget is the default THEN the same
  request returns 200 and one turn is persisted.
- WHEN a planner reply fails `ExecutionPlan` validation with
  `VALIDATE_OUTPUT=true` THEN `POST /workflows/run` returns the
  `llm_bad_response` envelope; WHEN validation is off THEN the same reply
  flows through unchanged.
- WHEN `LMSTUDIO_OVERRIDE_MODEL` is unset THEN L11 skips with exactly
  `set LMSTUDIO_OVERRIDE_MODEL to run LM Studio override tests` and the
  zero-skip guard stays green; WHEN a test skips with any unsanctioned reason
  THEN the guard fails the session.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_EMBEDDINGS__DEVICE` | _(none)_ | Torch device for `SentenceTransformer(device=...)`; unset = library auto-detect (CUDA → MPS → CPU). `sentence_transformers` provider only |

Test-scope env (no `MANGOMAS_` prefix; names live in `tests/constants.py`):

| Env var | Default | Purpose |
|---|---|---|
| `LMSTUDIO_E2E_TIMEOUT_SECONDS` | `240` | Adapter budget for every live LM Studio scenario; the httpx client budget derives from it |
| `VERTEX_E2E_TIMEOUT_SECONDS` | `240` | Same, for `tests/vertex/` |
| `LMSTUDIO_OVERRIDE_MODEL` | _(unset → L11 skips)_ | Second loaded model id for the `MODEL_OVERRIDE` scenario |

`DEVICE` is surfaced as `DEFAULT_EMBEDDINGS_DEVICE: str | None = None` on
`EmbeddingSettings` (singular — `src/mangomas/config/rag.py:37`). `.env.example`
and the CLAUDE.md config table gain the row, which
`tests/deploy/test_env_example_contract.py` requires in both directions; a
`_(none)_` Default cell is the recorded convention for an unset optional and
is skipped by the defaults comparison.

## Protocol / contract impact

- New/changed protocols: _none_. `EmbeddingClient` is untouched; the device is
  a constructor concern of one backend.
- New error types: _none_. Registry additions: _none_.
- Test doubles: `FakeLLM.delay_seconds` (additive, default `0.0`).
- Removed: `HTTPX_REQUEST_TIMEOUT_SECONDS` from `tests/constants.py`, whose
  six call sites all move to the derived per-suite budget. Test-scoped, no
  runtime surface.

## Backwards-compatibility

- `MANGOMAS_EMBEDDINGS__DEVICE` unset → the loader is called exactly as today,
  regression-pinned by the recorded-kwargs test.
- `FakeLLM()` with `delay_seconds=0.0` performs no `sleep`; existing tests
  unaffected.
- LM Studio scenarios 1–6 and the Vertex scenarios keep their oracles; only
  their client timeout source changes, which can only make a currently-green
  run stay green.
- No new `RUN_*` gate. Every new test sits behind `RUN_INTEGRATION`,
  `RUN_LMSTUDIO`, or `RUN_EMBEDDINGS_LOCAL` + `RUN_RAG`, so the default unit
  run collects and skips them under already-sanctioned reasons.

## Test plan

- Unit (measured by the 95 % gate): sentence-transformers device kwarg both
  directions through the composition factory; `DEVICE` env round-trip into
  `Settings`; `FakeLLM.delay_seconds` default-no-op plus honoured delay; the
  two meta-tests, each mutation-proven in a subprocess / tmp tree; the
  `LMSTUDIO_` prefix case in `tests/tooling/test_collection_gate.py`.
- Gated tier 1 (`RUN_INTEGRATION=1`, runs in CI every push): the
  `composed_app` fixture plus I1–I7.
- Gated tier 2 (`RUN_LMSTUDIO=1`, manual): L7–L12 plus the budget derivation
  in both live conftests.
- Gated tier 3 (`RUN_EMBEDDINGS_LOCAL=1 RUN_RAG=1`, nightly CPU): E1–E4.
- Gates: both meta-tests prove both directions; the step-timeout scenarios are
  proven to fire by re-running the spec-0026 mutation through HTTP.
- Coverage: E2E suites run `--no-cov` and move no floor; unit-side additions
  keep `adapters ≥ 85 %`, `config ≥ 95 %` and the global 95 %.

## Acceptance criteria

- [x] Feature off by default → no behaviour change (device kwarg absent,
      pinned by `test_no_device_is_passed_when_unset`; `FakeLLM` no-op delay,
      pinned by `test_default_fake_llm_never_sleeps`; default `pytest` run
      collects every new test under a sanctioned skip only).
- [x] Feature on via env → documented behaviour (`DEVICE` forwarded through
      the composition factory; both live budgets env-resolved, with the client
      budget derived from the adapter budget).
- [x] Every Phase-1 delivery has a tier-1 scenario green in CI
      (`tests/integration/`, 21 tests) and a tier-2 scenario written and
      runnable against a local LM Studio (`tests/lmstudio/`, 18 collected).
- [x] Tier-3 E1–E4 written; the E2 parity assertion runs on any device and
      becomes the non-trivial comparison on a GPU box. **Not yet executed** —
      this container has no torch/CUDA and no GPU; the nightly CPU job is the
      first execution.
- [x] Both meta-tests landed with mutation proofs recorded (below).
- [x] The client/adapter budget mismatch is gone from **both** live suites —
      `HTTPX_REQUEST_TIMEOUT_SECONDS` no longer exists, and the lint rejects a
      numeric `timeout=` so it cannot return.
- [x] `ruff`, `mypy --strict`, `pytest` (95 % gate + per-package floors),
      `frontmatter-lint`, `make gated-suites` all clean.
- [x] CHANGELOG updated; `docs/testing/lmstudio-e2e.md` scenarios 7–12 and
      `docs/testing/regression.md` inventory updated; no ADR (no boundary
      changed).

## Mutation proofs (run, not committed)

Each was applied to the source, the named test observed failing, and the source
restored. The first is the strongest evidence for this spec's premise.

| Mutation | Result |
|---|---|
| Delete `save_turn` in `_stream_agent` | **All 10 existing streaming tests stayed green**; the new I1 flow failed. That gap is why this spec exists |
| Remove the `asyncio.timeout` wrap in `_handle_step` | I2 failed (200 instead of 504) |
| Make `resolve_llm` ignore `ctx.extras` | I6 failed |
| Neutralise the `WHERE tenant = ?` filter | I7 failed |
| Drop `device=cfg.device` in the embeddings factory | D0 forwarding test failed |
| Remove the `delay_seconds > 0` guard | `test_default_fake_llm_never_sleeps` failed |
| Plant a numeric `timeout=` in a live scenario | The hardware lint failed, naming file and line — i.e. it catches the exact pre-fix defect |
| Replace `make postgres` in `nightly.yml` | Parity guard failed, naming `RUN_POSTGRES` |
| List `RUN_GITLEAKS` as infeasible | Parity guard failed as a stale claim |

## Not executed here

Honest scope of what this container could verify:

- **Tier 2 (LM Studio)** — written and collected (18 scenarios, all skipping
  under the sanctioned gate); never run, because no LM Studio server exists
  here. The GPU + CPU recorded-run pair remains outstanding, and
  `docs/testing/lmstudio-e2e.md` carries the empty table for it.
- **Tier 3 (local compute)** — written; never run, because `torch` and
  `chromadb` are not installed here and the container has no GPU. The nightly
  CPU job is its first execution.

Both are gated suites by design; the meta-test in
`tests/deploy/test_gated_suite_homes.py` is what stops "written but never run"
from becoming permanent and invisible.
