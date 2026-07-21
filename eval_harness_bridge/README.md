# `eval_harness_bridge` — evaluate Mango-Mas with the `ianshank/Agents` harness

This directory lets the standalone [`ianshank/Agents`](https://github.com/ianshank/Agents)
eval harness (the `eval-harness` CLI) drive **Mango-Mas as a black-box system
under test**, using its own richer scorer / multi-provider-judge / sink /
threshold-gate stack. Nothing in `src/mangomas/` is touched — the bridge speaks
only HTTP.

## Why this shape

`Agents` and Mango-Mas's built-in `mangomas.eval` are near mirror-image designs.
The cleanest way to compose them is **Agents-outer, Mango-Mas-inner over HTTP**:

- Agents' `callable` target dynamic-imports a Python function and calls it
  **synchronously** as `fn(item.inputs)` (with `pass_item: false`), storing the
  raw return value as the graded prediction.
- Mango-Mas exposes `POST /agents/{name}/invoke` → `{content, agent, metadata}`,
  where `content` is exactly the string a scorer grades.

So the entire bridge is one sync function ([`mango_bridge.predict`](mango_bridge.py))
that POSTs to a running Mango-Mas API.

**Why HTTP and not in-process:** a naïve in-process bridge
(`build_orchestrator()` once + `asyncio.run(orch.dispatch(...))` per row) fails
on the *second* row with `RuntimeError: Event loop is closed` — Mango-Mas's LM
Studio client binds its async `httpx` pool to the first event loop, which
`asyncio.run` then destroys. HTTP avoids the loop entirely and evaluates the
real deployed surface. (An in-process bridge that awaits all rows in a *single*
loop also works — that's what Mango-Mas's own `EvalRunner` does — but it is not
what Agents' sync `callable` target gives you.)

## Files

| Path | Purpose |
|------|---------|
| `mango_bridge.py` | Sync `callable` target: `predict(inputs) -> str`, plus the `inputs`→`messages` schema bridge. |
| `convert_dataset.py` | Convert Mango-Mas JSONL → Agents `inputs`-shaped JSONL. |
| `config/mangomas.eval.yaml` | Example `EvalConfig`: `callable` target, `exact_match` scorer, threshold gate. |
| `datasets/mango_suite.source.jsonl` | Sample Mango-shaped suite (illustrative `expected` values). |
| `datasets/mango_suite.jsonl` | Generated Agents-shaped suite (`convert_dataset.py` output). |

Offline unit tests live in `tests/eval_harness_bridge/` and run in the normal
`pytest` CI — no model or server required.

## The schema bridge (the one real gap)

Agents rows are `{id, inputs: {...}, expected, metadata}` (a flat `inputs`
dict, no roles); Mango-Mas wants `messages: [{role, content}]`. Raw Mango-Mas
JSONL will **not** load meaningfully — the Agents loader leaves `inputs` empty
for a `{"messages": ...}` row — so conversion is a hard prerequisite.

`convert_dataset.py` nests the messages under `inputs.messages`, and
`mango_bridge.to_messages` passes them through verbatim (multi-turn rows
round-trip losslessly). For flat rows it synthesises an optional `system`
message plus a `user` message from `question` / `prompt` / `input`. Agent
selection travels as `inputs.agent` per row (Agents' `callable` params only
carry `path` + `pass_item`), falling back to `$MANGO_AGENT`.

Prediction extraction is lossless: `AgentResponse.content` == `resp.json()["content"]`
== the graded prediction.

## Quickstart

```bash
# 0. install both harnesses (pin the Agents ref to a SHA for real gating)
pip install -e ".[dev]"
pip install "git+https://github.com/ianshank/Agents@<PINNED_SHA>"

# 1. offline logic tests — no server, no model
python -m pytest tests/eval_harness_bridge -q

# 2. convert a dataset to the Agents inputs shape
python eval_harness_bridge/convert_dataset.py \
  eval_harness_bridge/datasets/mango_suite.source.jsonl \
  eval_harness_bridge/datasets/mango_suite.jsonl --agent chat

# 3. start Mango-Mas as the SUT (needs a reachable LLM, e.g. LM Studio)
uvicorn mangomas.api.app:create_app --factory --port 8000 &

# 4. run the gate through the Agents harness
PYTHONPATH=eval_harness_bridge DATA_ROOT=eval_harness_bridge/datasets \
  MANGO_BASE_URL=http://localhost:8000 \
  eval-harness run --config eval_harness_bridge/config/mangomas.eval.yaml
```

The harness exits non-zero when the threshold gate fails, which the
[`eval-gate.yml`](../.github/workflows/eval-gate.yml) workflow uses as a CI gate.

## Environment

| Var | Default | Purpose |
|-----|---------|---------|
| `MANGO_BASE_URL` | `http://localhost:8000` | Mango-Mas API base URL |
| `MANGO_AGENT` | `chat` | Default agent when a row omits `inputs.agent` |
| `MANGO_TIMEOUT` | `60` | Per-request timeout (s) |
| `DATA_ROOT` | — | Directory the harness confines dataset paths to |
| `MANGOMAS_LLM__BASE_URL` / `MANGOMAS_LLM__MODEL` | see project config | Point the SUT's LLM at your endpoint |

## Notes, gotchas, and next steps

- **Confirm type strings.** `jsonl` / `callable` / `exact_match` / `console` /
  `json_file` follow the harness's built-ins; run `eval-harness list-plugins`
  for your pinned revision before trusting the gate.
- **`exact_match` is illustrative.** The sample `expected` values assume
  tightly-constrained prompts. For open-ended suites, curate `expected` and
  switch to `llm_judge` (uncomment the `judge:` block and point
  `$JUDGE_BASE_URL` at an OpenAI-compatible endpoint such as LM Studio for a
  keyless local judge).
- **Errored rows.** `predict` raises on non-2xx so a down sidecar is recorded as
  *errored* (not a silent zero). The `pass_rate` gate already fails in that case;
  assert `errored == 0` explicitly if you want a distinct signal.
- **The gate is fail-closed.** A `GateRule.score` that matches no emitted
  aggregate name produces a loud gate failure (exit 1), not a silent pass.
- **Regression gating is intentionally not duplicated here.** Both projects
  already ship it — Mango-Mas via `mangomas.eval.baseline` +
  `evaluate_regression_gate` (over its own `EvalReport`), and Agents via
  `scripts/regression_gate.py` (net-new *code* failures). Pick one system of
  record; Agents `RunResult` and Mango-Mas `EvalReport` schemas do not overlap,
  so never diff across them.
- **Layer 2 (optional).** To keep Mango-Mas's native `EvalRunner` as the driver
  but borrow Agents' HTML sink or local judge, wrap them as Mango-Mas
  `Scorer` / `Sink` adapters and register under the existing
  `mangomas.eval.scorers` / `.sinks` entry-point groups with
  `MANGOMAS_DISCOVERY_ENABLED=true`. (The judge shim is safe; a sink shim must
  construct Agents' `RunResult(items=..., started_at=..., finished_at=...)`
  dataclass with exact field names.)
