# Evaluation Harness

`mangomas.eval` is an offline harness that runs a JSONL dataset through any
registered agent, scores each prediction against an expected answer with a
pluggable `Scorer`, and emits a structured `EvalReport`.

The harness reuses the existing `Orchestrator` for dispatch — there is no
parallel execution path, no parallel LLM client, and no parallel control
loop. Adding eval to a deployment changes nothing about runtime behaviour.

## Quickstart

```bash
mangomas eval \
  --dataset path/to/dataset.jsonl \
  --scorer exact_match \
  --agent chat \
  --output-json report.json
```

All flags fall back to `MANGOMAS_EVAL__*` env vars; the dataset path is
required (either via the flag or the env var) and the CLI exits with
code 2 when neither is set.

## Settings

`EvalSettings` is wired into the top-level `Settings` model. All values are
env-driven via `MANGOMAS_EVAL__*`:

| Variable | Default | Purpose |
|---|---|---|
| `MANGOMAS_EVAL__DATASET_PATH` | (unset) | JSONL dataset path |
| `MANGOMAS_EVAL__SCORER` | `exact_match` | Scorer name |
| `MANGOMAS_EVAL__AGENT` | `chat` | Agent name (used by the `agent` target) |
| `MANGOMAS_EVAL__TARGET` | `agent` | Target name (`agent`/`pipeline`/`fan_out`/`echo`) |
| `MANGOMAS_EVAL__TARGET_OPTIONS` | `{}` | Per-target options keyed by target name |
| `MANGOMAS_EVAL__OUTPUT_DIR` | `eval-output` | Default report directory |
| `MANGOMAS_EVAL__PARALLELISM` | `1` | Max concurrent rows |
| `MANGOMAS_EVAL__FAIL_FAST` | `false` | Cancel after first non-pass |
| `MANGOMAS_EVAL__SCORER_OPTIONS` | `{}` | Free-form scorer options |
| `MANGOMAS_EVAL__GATE_ENABLED` | `false` | Engage the quality gate (exit 3 on fail) |
| `MANGOMAS_EVAL__MIN_MEAN_SCORE` | (unset) | Fail the gate if `mean_score` below this `[0,1]` |
| `MANGOMAS_EVAL__MIN_PASS_RATE` | (unset) | Fail the gate if `passed/size` below this `[0,1]` |
| `MANGOMAS_EVAL__FAIL_ON_ERROR` | `false` | Fail the gate if any row errored |
| `MANGOMAS_EVAL__MAX_MEAN_COST_USD` | (unset) | Fail the gate if `mean_cost_usd` exceeds this USD amount |
| `MANGOMAS_EVAL__BASELINE_PATH` | (unset) | Baseline report JSON to diff against (regression gating) |
| `MANGOMAS_EVAL__MAX_MEAN_SCORE_DROP` | (unset) | Max allowed `mean_score` drop vs baseline `[0,1]` |
| `MANGOMAS_EVAL__MAX_PASS_RATE_DROP` | (unset) | Max allowed `pass_rate` drop vs baseline `[0,1]` |
| `MANGOMAS_EVAL__ALLOW_NEW_FAILURES` | `true` | When `false`, fail on rows that passed in baseline but fail now |
| `MANGOMAS_EVAL__SINKS` | `["console"]` | Ordered list of result sinks |
| `MANGOMAS_EVAL__SINK_OPTIONS` | `{}` | Per-sink options keyed by sink name |
| `MANGOMAS_EVAL__SCHEMA_VERSION` | `1` | Forward-compatible config version marker |

## Dataset format

JSONL — one record per line:

```json
{
  "id": "row-1",
  "messages": [{"role": "user", "content": "What is 2 + 2?"}],
  "expected": "4",
  "metadata": {"category": "arithmetic"}
}
```

- `messages` is a non-empty list; entries match `mangomas.core.Message`.
- `expected` is a string used by every built-in scorer.
- `id` is auto-assigned as `row-<index>` when absent.
- `metadata` is optional and forwarded to the scorer through `ScorerContext.row_metadata`.

Empty lines are skipped; malformed JSON raises `DatasetError`.

## Scorer protocol

```python
from mangomas.eval import Scorer, ScorerContext, ScoreResult

class MyScorer:
    name = "mine"

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,
    ) -> ScoreResult:
        ...
```

`ScoreResult.score` is normalised to `[0.0, 1.0]`; `ScoreResult.passed` is
the scorer's own pass decision (so different scorers can apply different
thresholds without callers re-implementing the rule).

## Built-in scorers

| Scorer | Registry name | Behaviour |
|---|---|---|
| `ExactMatchScorer` | `exact_match` | Strict string match, with optional case-folding (`case_sensitive`) and whitespace normalisation (`strip_whitespace`). Default options: case-insensitive, whitespace-collapsing. |
| `LLMJudgeScorer` | `llm_judge` | Routes a structured prompt through the orchestrator's LLM client. Expects a one-line JSON response `{"score": <float>, "rationale": "..."}`. `passed = score >= threshold` (default `0.7`). Malformed responses raise `LLMBadResponse`. |
| `EmbeddingScorer` | `embedding` | Cosine similarity of embeddings of `prediction` and `expected`. **Requires an LLM with `.embed()`**. |
| `RegexMatchScorer` | `regex_match` | `expected` is a **regex** (per row); pass iff it matches `prediction`. Options: `flags` (`ignorecase`/`multiline`/`dotall`), `fullmatch`. |
| `ContainsScorer` | `contains` | `expected` is a **substring** (per row); pass iff contained in `prediction`. Option: `case_sensitive`. |
| `JsonKeysScorer` | `json_keys` | Parses `prediction` as a JSON object and grades by required-key coverage (great for `planner`/`reviewer` structured output). Keys come from `required_keys` option, else from `expected` parsed as a JSON object. Option: `strict` (extra keys → score 0). Malformed prediction → failing row (`score=0`), not an error. |
| `CostBudgetScorer` | `cost_budget` | Estimates USD per row (`metadata.cost_usd` explicit, else token counts, else `len(prediction)` × `usd_per_1k_output_chars`). Measure-only by default (`max_cost_usd` unset → always pass, `score=1.0`). The dollar figure is copied onto row metadata so `EvalReport.mean_cost_usd` can be gated via `MANGOMAS_EVAL__MAX_MEAN_COST_USD` (USD, default-off). |

> Note: `regex_match` and `contains` reinterpret the per-row `expected` field
> (as a pattern / needle) rather than a gold answer — keep their datasets
> separate from `exact_match` / `llm_judge` datasets.

## Targets

A **target** is what each row is dispatched against. It is resolved by name
through `target_registry` (default `agent`), so a run can evaluate a single
agent, a multi-agent topology, or a deterministic baseline. Select one with
`--target` / `MANGOMAS_EVAL__TARGET`; configure it via `target_options`. See
ADR-0004.

| Target | Registry name | Behaviour |
|---|---|---|
| `AgentTarget` | `agent` | Dispatch one registered agent (default). The agent name comes from `--agent` / `MANGOMAS_EVAL__AGENT`, else `target_options["agent"]["agent"]`. Preserves pre-target-indirection behaviour. |
| `PipelineTarget` | `pipeline` | Run a sequential pipeline (`orch.dispatch_pipeline`); grade the final agent's output. Option: `agents` (non-empty list). |
| `FanOutTarget` | `fan_out` | Dispatch all agents in parallel (`orch.dispatch_fan_out`). Option: `agents` (list) and `join` (`first` → first agent's content, default; `concat` → newline-joined). |
| `EchoTarget` | `echo` | Deterministic, no-LLM baseline. Returns a fixed `text` option if set, else the last user message. Useful for regression baselines and tests. |

`EvalRunner.run(dataset, agent_name=...)` still works (the agent name is wrapped
in the `agent` target); pass `target=` to use any other target. The report's
`agent_name` reflects the target name, and a new `target_name` field is added.
Third-party targets register via the `mangomas.eval.targets` entry-point group
(gated by `MANGOMAS_DISCOVERY_ENABLED`).

## Dataset sources

A **dataset source** is where rows come from, resolved by name through
`dataset_source_registry` (default `jsonl`). Select one with `--dataset-source` /
`MANGOMAS_EVAL__DATASET_SOURCE`; configure it via `dataset_source_options`. Every
source yields the same validated `DatasetRow` list (via the shared `_parse_row`).

| Source | Registry name | Behaviour |
|---|---|---|
| `JsonlSource` | `jsonl` | Read a local JSONL file (default). Path precedence: `--dataset` > `dataset_source_options["jsonl"]["path"]` > `MANGOMAS_EVAL__DATASET_PATH`. Wraps the existing `load_jsonl`. |
| `InlineSource` | `inline` | Rows supplied directly via `dataset_source_options["inline"]["rows"]` (list of raw row dicts). Validated through `_parse_row`. Handy for tests / small embedded datasets. |
| `LangfuseDatasetSource` | `langfuse` | Fetch a named Langfuse dataset (`dataset_source_options["langfuse"]["dataset"]`). Requires the `mangomas[langfuse]` extra (lazy-imported); creds via `LANGFUSE_*` env. Items map `input`→messages and `expected_output`→`expected`. |

Third-party sources register via the `mangomas.eval.dataset_sources` entry-point
group (gated by `MANGOMAS_DISCOVERY_ENABLED`).

## Quality gate (CI)

The gate turns an `EvalReport` into a pass/fail verdict. It is **off by default**
— configuring no thresholds keeps the historical exit codes (0 success,
1 runtime error, 2 config error). When engaged it adds **exit code 3** on
failure, raised only *after* every sink has emitted so CI artifacts always land.

```bash
# Fail CI (exit 3) if the mean score regresses below 0.8
mangomas eval -d data.jsonl -s exact_match --min-mean-score 0.8
```

`pass_rate = passed / dataset_size` (errored rows count against it), while
`mean_score` excludes errored rows. Use `--fail-on-error` to fail the gate when
any row errored regardless of thresholds. `--max-mean-cost-usd` is a **USD**
threshold on `EvalReport.mean_cost_usd` (populated by `cost_budget`); it is
not clamped to `[0, 1]`. Non-finite values (`NaN`, infinities) are rejected
at Settings, CLI, and scorer construction so a `NaN` cap cannot silently
pass. The quality gate stays default-off.

#### What the cost threshold measures — and what it cannot

`mean_cost_usd` is **declared, not measured**. `Target.run` returns a `str`
(`eval/target.py`), so the `AgentResponse` and its `metadata` are discarded at
that boundary, and `ScorerContext.row_metadata` carries the **dataset row's**
metadata instead. `cost_budget` therefore resolves its precedence — explicit
`cost_usd` → token counts → output-character rate — against what the JSONL file
declared, never against what the run consumed. No LLM adapter emits token usage
at all today.

Two consequences, both pinned by
`tests/eval/test_cost_measurement_basis.py`:

- With **no** declared counts, cost is `len(prediction)` × a rate. A
  `mean_cost_usd` gate then fires when the model becomes **wordier**, and is
  blind to a costlier model via `MODEL_OVERRIDE`, a price change per token, or
  extra tool steps.
- With declared counts — as in `tests/eval/fixtures/cost_controlled_v1.jsonl` —
  the figure is a **fixed budget cohort**, which is what makes it useful for
  comparing targets (`echo` / `agent` / `pipeline`) at equal declared cost. It is
  still a property of the dataset, not of the run.

So gate `mean_cost_usd` only over a declared-cost dataset, and say so wherever
the threshold is published. Making cost *measured* requires a channel for
response metadata through the `Target` seam plus adapter token telemetry; that is
an open decision (D15 in
`docs/analysis/20260919-council-rejection-and-replan.md`), not a present
capability. `estimate_cost_usd` already returns which tier it used (`explicit` /
`tokens` / `output_chars`) in the scorer's row metadata — read it when a figure
needs interpreting.

### Regression gating (baseline diff)

Beyond absolute thresholds, the gate can compare a run against a **baseline** — a
previously-saved `json_file` report. `diff_reports` produces a `ReportDiff`
(per-metric deltas + per-row regressed/new/dropped partition) and
`evaluate_regression_gate` fails (exit 3) when `mean_score` / `pass_rate` drops
beyond tolerance or new row failures appear. The threshold and regression
verdicts combine (logical AND, reasons concatenated) into one verdict, so a
single exit-3 path covers either. See ADR-0005.

```bash
# 1. Save a baseline report
mangomas eval -d data.jsonl -s exact_match -o baseline.json

# 2. Later: fail CI if mean_score drops > 0.02, or any baseline pass now fails
mangomas eval -d data.jsonl -s exact_match \
  --baseline baseline.json --max-mean-score-drop 0.02 --no-allow-new-failures
```

A drop is `baseline - current` (current run worse). A missing baseline file is
exit 2 (config); a malformed one surfaces during the run as exit 1. Row
regression keys on `row_id` stability — rows whose ids change show up as dropped
+ new rather than regressed.

## Result sinks

Sinks decouple producing a report from emitting it. The default is `["console"]`
(identical to the historical inline output). Sinks compose, and each emits under
fault isolation — one failing sink never costs the others their output.

| Sink | Registry name | Behaviour |
|---|---|---|
| `ConsoleSink` | `console` | Human-readable stdout summary (+ a `gate=` line when gating). |
| `JsonFileSink` | `json_file` | Pretty JSON report to `path` (+ a `gate` key when gating). |
| `SqliteResultsSink` | `sqlite_results` | Append the report + per-row results to two SQLite tables (`eval_reports`, `eval_rows`) at `db_path`. A queryable history; the gate verdict is stored as `gate_json`. |
| `WebhookSink` | `webhook` | POST the `json_file`-shaped payload to `url` (httpx; core dep, no extra). Option `timeout_seconds` (default 10). A non-2xx response fails the sink. |
| `LangfuseSink` | `langfuse` | Publishes a trace + `mean_score` to Langfuse. With option `per_row: true`, also emits one trace + `row_score` per row (default `false` = aggregate only). **Optional extra**: `pip install 'mangomas[langfuse]'`; credentials from `LANGFUSE_*` env/ADC. |

```bash
# Console + JSON file
MANGOMAS_EVAL__SINKS='["console","json_file"]' \
  mangomas eval -d data.jsonl -s exact_match -o report.json
```

The legacy `--output-json PATH` flag is preserved: it injects (or overrides the
path of) the `json_file` sink.

## Plugin discovery (entry points)

Third-party packages can register scorers/sinks without editing this repo by
declaring entry points under the `mangomas.eval.scorers` / `mangomas.eval.sinks`
groups, each pointing at a factory `Callable[[dict], Scorer|Sink]`. Discovery is
additive (built-ins still register in-process) and runs only when
`MANGOMAS_DISCOVERY_ENABLED=true`. A failing plugin is logged and skipped.

## Logging events

Every event carries `extra={"event": ..., ...}` for structured-log filters:

| Event | Level | Keys |
|---|---|---|
| `eval_dataset_load_start` | DEBUG | `path` |
| `eval_dataset_loaded` | INFO | `path`, `row_count` |
| `eval_start` | INFO | `dataset_size`, `scorer`, `agent_name`, `parallelism`, `fail_fast` |
| `eval_row` | DEBUG | `row_id`, `score`, `passed`, `duration_ms`, `cost_usd` |
| `eval_error` | ERROR | `row_id`, `error_type`, `duration_ms` |
| `eval_summary` | INFO | `dataset_size`, `passed`, `failed`, `errored`, `mean_score`, `mean_cost_usd`, `duration_ms` |
| `eval_cost_estimated` | DEBUG | `scorer`, `cost_usd`, `source`, `max_cost_usd`, `passed` |
| `eval_llm_judge_request` | DEBUG | `scorer`, `threshold` |
| `embedding_scorer_unavailable` | WARNING | `scorer`, `reason` |
| `eval_gate` | INFO | `passed`, `mean_score`, `pass_rate`, `mean_cost_usd`, `errored`, `reasons` |
| `eval_sink_json_file` | DEBUG | `path` |
| `eval_sink_langfuse` | INFO | `scorer` |
| `eval_plugin_load_failed` | WARNING | `group`, `plugin`, `error` |
| `eval_plugin_override` | INFO | `group`, `plugin` |

Correlation ids propagate automatically: the runner sets a fresh
`mangomas.correlation.correlation_id_var` per row, so every orchestrator /
adapter log line carries the row's id.

## Known gaps

- **`EmbeddingScorer`** raises `NotImplementedError` against every
  provider shipped today. None of the LLM adapters (LM Studio, Vertex)
  expose an `embed()` method yet. The scorer registers itself so external
  callers can monkeypatch their own embeddable client onto `AgentContext`,
  and a future adapter that lands an `embed()` method will activate the
  scorer with no harness changes.
- **`LLMJudgeScorer`** trusts the model to return strict JSON. Sloppy
  responses raise `LLMBadResponse`; consider lowering temperature
  (`MANGOMAS_LLM__TEMPERATURE`) when running the judge against a less
  deterministic model.

## Testing your own scorer

Scorers are pure async functions over strings — they can be tested with the
project's `FakeLLM` doubles in `tests/fakes.py`. See
`tests/eval/test_exact_match.py` for the minimal pattern.
