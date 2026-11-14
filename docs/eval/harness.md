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
| `MANGOMAS_EVAL__SCORER` | `exact_match` | Scorer name (`-s` may be repeated on the CLI to build a `composite`) |
| `MANGOMAS_EVAL__AGENT` | `chat` | Agent name (used by the `agent` target) |
| `MANGOMAS_EVAL__TARGET` | `agent` | Target name (`agent`/`pipeline`/`fan_out`/`echo`) |
| `MANGOMAS_EVAL__TARGET_OPTIONS` | `{}` | Per-target options keyed by target name |
| `MANGOMAS_EVAL__OUTPUT_DIR` | `eval-output` | Default report directory |
| `MANGOMAS_EVAL__PARALLELISM` | `1` | Max concurrent rows |
| `MANGOMAS_EVAL__FAIL_FAST` | `false` | Cancel after first non-pass |
| `MANGOMAS_EVAL__TRIALS` | `1` | Attempts per row (`-k`); a row passes only when all attempts pass (pass^k) |
| `MANGOMAS_EVAL__SCORER_OPTIONS` | `{}` | Free-form scorer options |
| `MANGOMAS_EVAL__GATE_ENABLED` | `false` | Engage the quality gate (exit 3 on fail) |
| `MANGOMAS_EVAL__MIN_MEAN_SCORE` | (unset) | Fail the gate if `mean_score` below this `[0,1]` |
| `MANGOMAS_EVAL__MIN_PASS_RATE` | (unset) | Fail the gate if `passed/size` below this `[0,1]` |
| `MANGOMAS_EVAL__FAIL_ON_ERROR` | `false` | Fail the gate if any row errored |
| `MANGOMAS_EVAL__MAX_MEAN_COST_USD` | (unset) | Fail the gate if `mean_cost_usd` exceeds this USD amount |
| `MANGOMAS_EVAL__MIN_PASS_POW_K` | (unset) | Fail the gate if `pass_pow_k` below this `[0,1]`; fails closed when `TRIALS < 2` |
| `MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH` | (unset) | `llm_judge` calibration record; unset = judge reported, not gated |
| `MANGOMAS_EVAL__JUDGE_CALIBRATION_MAX_AGE_DAYS` | `180` | Record staleness limit |
| `MANGOMAS_EVAL__JUDGE_MIN_KAPPA` | `0.8` | Cohen's κ floor (κ or ρ suffices) |
| `MANGOMAS_EVAL__JUDGE_MIN_RHO` | `0.85` | Spearman ρ floor |
| `MANGOMAS_EVAL__JUDGE_MIN_SAMPLE_SIZE` | `200` | Minimum labelled rows behind a record |
| `MANGOMAS_EVAL__JUDGE_PROFILE` | (unset) | LLM profile the judge grades through (unset = the shared client) |
| `MANGOMAS_EVAL__BASELINE_PATH` | (unset) | Baseline report JSON to diff against (regression gating) |
| `MANGOMAS_EVAL__MAX_MEAN_SCORE_DROP` | (unset) | Max allowed `mean_score` drop vs baseline `[0,1]` |
| `MANGOMAS_EVAL__MAX_PASS_RATE_DROP` | (unset) | Max allowed `pass_rate` drop vs baseline `[0,1]` |
| `MANGOMAS_EVAL__ALLOW_NEW_FAILURES` | `true` | When `false`, fail on rows that passed in baseline but fail now |
| `MANGOMAS_EVAL__SINKS` | `["console"]` | Ordered list of result sinks |
| `MANGOMAS_EVAL__SINK_OPTIONS` | `{}` | Per-sink options keyed by sink name |
| `MANGOMAS_EVAL__SLICE_KEY` | `category` | Row-metadata key for `EvalReport.slices` (reporting only) |
| `MANGOMAS_EVAL__SCHEMA_VERSION` | `2` | Forward-compatible config version marker |

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
  Conventions the built-in scorers and lanes read: `metadata.category` (the
  default `MANGOMAS_EVAL__SLICE_KEY`), `metadata.expected_tools` (`tool_trajectory`),
  `metadata.json_field` (`json_field`), and `metadata.fixture` (the scripted
  transcript the deterministic lanes replay — see `evals/README.md`).
- `expected_error` (optional, spec-0045) names the exception class or
  `MangomasError.code` the target **must** raise. The runner passes the row on
  that refusal (`metadata.refused: true`), fails it on any answer without
  consulting the scorer, and keeps any other error an errored row.

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
| `LLMJudgeScorer` | `llm_judge` | Routes a structured prompt through the orchestrator's LLM client. Expects a one-line JSON response `{"score": <float>, "rationale": "..."}`. `passed = score >= threshold` (default `0.7`). Malformed responses raise `LLMBadResponse`. **Self-grading**: the judge is the system under test's own client, so its scores are *reported, not gated* until a calibration record covers its model — see "Judge calibration" below. |
| `EmbeddingScorer` | `embedding` | Cosine similarity of embeddings of `prediction` and `expected`. Resolves a configured provider via `ScorerContext.embeddings` (`EmbeddingClient`). |
| `RegexMatchScorer` | `regex_match` | `expected` is a **regex** (per row); pass iff it matches `prediction`. Options: `flags` (`ignorecase`/`multiline`/`dotall`), `fullmatch`. |
| `ContainsScorer` | `contains` | `expected` is a **substring** (per row); pass iff contained in `prediction`. Option: `case_sensitive`. |
| `JsonKeysScorer` | `json_keys` | Parses `prediction` as a JSON object and grades by required-key coverage (great for `planner`/`reviewer` structured output). Keys come from `required_keys` option, else from `expected` parsed as a JSON object. Option: `strict` (extra keys → score 0). Malformed prediction → failing row (`score=0`), not an error. |
| `ToolTrajectoryScorer` | `tool_trajectory` | Grades **which tools ran** (`ToolAgent`'s `metadata.tool_calls`) against `metadata.expected_tools`: `recall`, `missing_tools`, `extra_tools`, `steps`, reported separately. `passed` needs full recall, no unexpected tool unless `allow_extra`, and `max_steps` when set; `score` is the recall when those hold, else `0.0`. Arguments are not graded (the trace carries `sha256:` digests by default). A refused call never reaches it — that is an `expected_error` row. |
| `JsonFieldScorer` | `json_field` | ADR-0034's structured acceptance as a scorer: parse the whole prediction as a JSON object, walk a dotted path over **mappings only**, apply exactly one of `equals` / `at_least` / `at_most` / `min_length`. Check from scorer options or per row via `metadata.json_field` (row wins). Unparseable or unresolvable → failing row, never an error. Use it for `planner` / `reviewer` where no substring is right in both directions. |
| `CompositeScorer` | `composite` | Several scorers per row, each recorded under `metadata.scores`; the row's verdict is `min` / `all`. Option `scorers: [name | {name, options}]`. `-s a -s b` on the CLI builds one, each child taking `SCORER_OPTIONS[<name>]`. No weights by design. |
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
| `AgentTarget` | `agent` | Dispatch one registered agent (default). The agent name comes from `--agent` / `MANGOMAS_EVAL__AGENT`, else `target_options["agent"]["agent"]`. Option `llm_profile` stamps a spec-0040 provider profile on every row's request, so one dataset can be run against a local and a cloud model and the two baselines compared. |
| `PipelineTarget` | `pipeline` | Run a sequential pipeline (`orch.dispatch_pipeline`); grade the final agent's output. Option: `agents` (non-empty list). |
| `FanOutTarget` | `fan_out` | Dispatch all agents in parallel (`orch.dispatch_fan_out`). Option: `agents` (list) and `join` (`first` → first agent's content, default; `concat` → newline-joined). |
| `EchoTarget` | `echo` | Deterministic, no-LLM baseline. Returns a fixed `text` option if set, else the last user message. Useful for regression baselines and tests. |

Every target returns a `TargetOutcome(content, metadata)` (spec-0045): the
graded text plus the response metadata the dispatch produced (loop counters,
`tool_calls`, measured `usage`). The runner stores it on
`EvalRowResult.response_metadata` and hands it to scorers as
`ScorerContext.response_metadata`, alongside the row's input `messages`. A
third-party target still returning a bare `str` is wrapped with a one-time
WARNING. `EvalRunner.run(dataset, agent_name=...)` still works (the agent name is wrapped
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

`mean_cost_usd` is **measured when an adapter reports usage, declared otherwise**
(spec-0045, closing D15(b) in
`docs/analysis/20260919-council-rejection-and-replan.md`). `Target.run` returns a
`TargetOutcome` whose `metadata` is the `AgentResponse.metadata` the dispatch
produced, and the runner hands the scorer both mappings:
`ScorerContext.row_metadata` (what the JSONL row declared) and
`ScorerContext.response_metadata` (what the run produced). `cost_budget` then
resolves, in order: the adapter-written `usage` block (`input_tokens`,
`output_tokens`, `llm_calls`) when `llm_calls > 0` → explicit `cost_usd` →
declared token counts → output-character rate. The tier used is written to the
row as `source` (`measured_tokens` / `explicit` / `tokens` / `output_chars`).

Only the `usage` block counts as a measurement. A target that writes `cost_usd`
or token keys into its own response metadata is making a claim, not reporting
usage, and is ignored — the dataset's declaration keeps its tier for that. An
adapter that reports nothing leaves `llm_calls` at `0`, which reads as *silence*,
never as a free run.

Consequences, all pinned by `tests/eval/test_cost_measurement_basis.py`:

- With a **silent adapter and no declared counts**, cost is `len(prediction)` ×
  a rate: a gate fires when the model becomes **wordier** and is blind to a
  costlier model via `MODEL_OVERRIDE`, a price change per token, or extra tool
  steps. The LM Studio adapter reports usage (`utils/llm_usage.py`); Vertex
  does not yet.
- With **declared counts** — as in `tests/eval/fixtures/cost_controlled_v1.jsonl`
  — and a silent adapter, the figure is a **fixed budget cohort** for comparing
  targets at equal declared cost. It is a property of the dataset.
- With a **reporting adapter**, the figure is the run's, and a `MODEL_OVERRIDE`
  or extra tool step moves it.

So read `source` before publishing a threshold, and gate `mean_cost_usd` only over
rows whose tier you mean.

### Judge calibration (reported, not gated)

`llm_judge` scores through `ScorerContext.llm` — the orchestrator's shared
client, i.e. the system under test's own model — so self-preference bias is
structural and a judge verdict is not evidence until it has been checked
against people. Spec-0047 (reliability plan C2) makes that explicit:

- Every judge run stamps `EvalReport.judge` with a `JudgeStatus` (`calibrated`,
  `reason`, `judge_model`, `measured_at`, `sample_size`, `cohens_kappa`,
  `spearman_rho`, `self_preference`). The console prints it as `judge=…`; the
  JSON / webhook payloads and `sqlite_results` (`judge_json`) carry it.
- With no record, or one that is stale, for another model, too small, below the
  floors, or `self_preference: measured_biased`, the judge is **uncalibrated**:
  when a score threshold (`--min-mean-score`, `--min-pass-rate`,
  `--min-pass-pow-k`) is configured the gate replaces those checks with one
  informational note — `gate=PASS (note: llm_judge uncalibrated (…) — score
  thresholds not applied; …)` — and sets `GateResult.judge_excluded = true`, so a
  downstream consumer can refuse a green that rests on an uncalibrated judge.
  `--fail-on-error` and `--max-mean-cost-usd` still apply. **Refusal rows**
  (`expected_error`) never reach the judge, so the score thresholds still apply
  to that subset (`scorer-independent rows (n): …`), and a report whose rows were
  all refusal checks carries no judge at all. A regression gate on a judge run
  is demoted whenever a regression criterion is configured — `judge_excluded`
  does not depend on whether the deltas happened to fail — except that a
  regressed refusal row still fails under `--no-allow-new-failures`. This is the `cost_budget` posture (skip, say so), chosen over
  failing closed so a team can keep collecting judge scores while it produces
  the record; a future opt-in to fail closed is the right place for the
  stricter stance.
- A **composite** with a judge child is demoted as a whole (`min` / `all` make
  every aggregate judge-tainted); run the judge as its own invocation when you
  want deterministic gating beside it. The judge child is seeded with the
  calibration settings whether it comes from repeated `-s` or from an explicit
  `-s composite` (`{"scorers": ["contains", "llm_judge"]}`, bare name or object form).

#### Judge options (spec-0048)

Set through `MANGOMAS_EVAL__SCORER_OPTIONS` (flat for `-s llm_judge`, under
`"llm_judge"` when it is one of several `-s`); all default off, and the
default prompt is byte-identical to the historical one.

| Option | Default | Effect |
|---|---|---|
| `threshold` | `0.7` | `passed = score >= threshold` |
| `rubric` | (none) | A string or list of criteria rendered as a `RUBRIC` block before the output format |
| `include_input` | `false` | The judge also sees the row's input messages, system prompt included, as `INPUT: [role] content` |
| `judge_profile` | `MANGOMAS_EVAL__JUDGE_PROFILE` | Grade through that LLM profile's client instead of the shared one; logged at INFO, not as self-grading |

A reply wrapped in a code fence or prose is recovered (`parse: "recovered"` in
the row metadata; `"strict"` otherwise); recovery widens formatting tolerance
only — a recovered score passes the same numeric / finite checks.

`rubric` and `include_input` change what the judge is asked, so they change the
**prompt digest** (`EvalReport.judge.prompt_sha256`, printed short in the
console `judge=` line). A calibration record must name the same digest: one
measured under another prompt, or one that pins none, does not calibrate.
`judge_profile` changes *who* judges, so with a profile the record's
`judge_model` must be that profile's model.

The record is JSON at `MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH` (a set-but-missing
or malformed path is exit 2, never "absent"):

```json
{
  "judge_model": "local-model",
  "measured_at": "2026-10-01",
  "sample_size": 300,
  "cohens_kappa": 0.86,
  "spearman_rho": 0.91,
  "self_preference": "not_measured",
  "prompt_sha256": "sha256:<64 hex — copy judge.prompt_sha256 from the JSON report>",
  "notes": "300 expert pass/fail labels over planner_v1 + reviewer_v1; rubric v3"
}
```

`judge_model` must equal the model that judges: `MANGOMAS_LLM__MODEL` for the
shared client, or the `MANGOMAS_EVAL__JUDGE_PROFILE` profile's model. An
agent-level `MODEL_OVERRIDE` or `llm_profile` on the *target* does not change
which model is judging. `prompt_sha256` must equal the run's
`judge.prompt_sha256`. Acceptance: `sample_size >=
JUDGE_MIN_SAMPLE_SIZE`, age `<= JUDGE_CALIBRATION_MAX_AGE_DAYS`, and κ `>=
JUDGE_MIN_KAPPA` **or** ρ `>= JUDGE_MIN_RHO`. `self_preference` is one of
`not_measured`, `measured_unbiased`, `measured_biased`: the first two both
calibrate but are printed verbatim and never conflated — "not measured" is not
a clean bill — and the third never calibrates.

Producing a record: label 200–500 rows with expert pass / fail (or a graded
score) across the datasets the judge will grade; run the judge over the same
rows with candidate positions rotated and a decomposed rubric rather than one
holistic score; compute κ (binary) or ρ (graded) between judge and labels and
iterate the rubric until κ > 0.8 or ρ > 0.85; record the date, model id and
sample size. Re-measure when the judge model, its prompt, or the datasets
change — the age limit is a backstop, not the trigger.

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

## Trials and pass^k (reliability)

A single attempt per row measures capability, not reliability: an agent that
answers a row correctly nine times in ten reads as a pass. `MANGOMAS_EVAL__TRIALS=k`
(`--trials k` / `-k k`) repeats every row `k` times, each attempt with its own
correlation id, and `collapse_trials` folds the attempts into the row's one
result:

| Row field | Under `k` trials |
|---|---|
| `passed` | `True` only when **every** attempt was a clean pass |
| `score` | mean over non-errored attempts (`0.0` if all errored) |
| `error` | the first attempt's error if any attempt errored (so `fail_on_error` sees intermittent crashes) |
| `duration_ms` | sum over attempts |
| `metadata.cost_usd` | mean over attempts that reported one |
| `metadata.trials` | `{count, passed, errored, scores, correlation_ids}` |
| `prediction`, `response_metadata`, `correlation_id` | the first attempt's |

Because the collapsed row is a conjunction, `passed / dataset_size` **is**
pass^k, and the report says so explicitly: `EvalReport.trials`,
`EvalReport.pass_pow_k` (that fraction) and `EvalReport.trial_pass_rate` (passed
attempts over the attempts actually made — the pass@1 estimate; a row that
`fail_fast` skipped before dispatch made none and is left out of that
denominator, though `pass_pow_k` still counts it as not passed). At `k=8` a 90 %-per-attempt
agent reports `pass_pow_k ≈ 0.43`; `trial_pass_rate` still shows the 0.9.
`k=1` is byte-identical to a run before this field existed.

`MANGOMAS_EVAL__MIN_PASS_POW_K` (`--min-pass-pow-k`) adds a reliability
threshold to the quality gate. Unlike the cost threshold it **fails closed**:
a run with `trials < 2` cannot measure pass^k, so the gate fails with
`min_pass_pow_k requires trials >= 2` rather than skipping — a reliability
gate a single attempt can satisfy would be vacuous (ADR-0033). The regression
gate is unchanged: a row that passed at `k=1` in the baseline and is flaky at
`k=5` now is listed under `regressed_rows`, which is the point. Compare like
with like: keep `trials` the same between a baseline and the run gated
against it (`run.labels` do not record it; `report.trials` does).

Trials multiply model calls and measured cost `k`-fold. `parallelism` still
bounds concurrent *rows*; the `k` attempts of one row run sequentially.

## Report provenance and slices

Every `EvalReport` carries `run: RunProvenance` — `run_id`, `started_at`,
`dataset_sha256` (a digest of the rows as parsed), scorer and target names,
`mangomas_version`, `schema_version`, `git_sha` (`GITHUB_SHA`, else read from
the repository's `.git` files, else `null`) and `labels` the CLI adds
(`llm_provider`, `llm_model`, `llm_profile`, the scorer names, and digests of
the option mappings). Sinks serialise it, `load_baseline` round-trips it, and
`diff_reports` ignores it, so a regenerated baseline never drifts on a
timestamp and two reports can be compared knowing what each ran.

`slices` groups rows by `metadata[MANGOMAS_EVAL__SLICE_KEY]` (default
`category`) into `size / passed / failed / errored / mean_score` — reporting
only; nothing gates on a slice. The console sink also prints a Wilson 95 %
interval on the pass rate, labelled *informational*: on eight to fifty rows the
only honest regression signal is `regressed_rows`, and the interval exists so a
reader does not mistake 7/8 for a regression against 8/8.

## Deterministic lanes

Two `make` targets run on every CI runner without an LLM and are part of
`make gate` (spec-0045; see `evals/README.md` for what each can and cannot prove):

| Target | What it runs | Proves |
|---|---|---|
| `make eval-selfcheck` | `mangomas eval` with the `echo` target over `evals/datasets/selfcheck_echo_v1.jsonl` against its committed baseline | The harness's own exit codes, threshold gate and baseline regression path. A harness self-test, not a quality gate. |
| `make eval-lanes` | `tests/evals/test_dataset_lanes.py`: each row of `planner_v1`, `reviewer_v1` and `safety_toolagent_v1` replayed through the real agent with the scripted `FakeLLM` transcript in `metadata.fixture`, scored and diffed against `evals/baselines/` | Agent post-processing (planner / reviewer parse-and-validate), `ToolAgent` policy refusals and trace, the scorer contract, and — via outgoing-prompt assertions — that each agent still sends its schema / tool roster. **Not** prompt quality: replies are fixtures chosen by call index. |

`make eval-selfcheck-baseline` and `make eval-lanes-baseline` rewrite the
baselines; `git diff` on them is the review.

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

- **`EmbeddingScorer`** needs an embedding provider: it uses
  `ScorerContext.embeddings` (attached by composition when
  `MANGOMAS_EMBEDDINGS__ENABLED=true`), else an LLM client exposing `embed()`,
  and raises `NotImplementedError` only when neither is present. No LLM
  adapter shipped today exposes `embed()`, so enable the embeddings seam.
- **`LLMJudgeScorer`** recovers a JSON object wrapped in prose or a code fence
  (spec-0048) but still raises `LLMBadResponse` when no object with a numeric,
  finite `score` can be found; consider lowering temperature
  (`MANGOMAS_LLM__TEMPERATURE`) when running the judge against a less
  deterministic model. A decomposed rubric still produces one overall score;
  per-criterion scores are not recorded.

## Testing your own scorer

Scorers are pure async functions over strings — they can be tested with the
project's `FakeLLM` doubles in `tests/fakes.py`. See
`tests/eval/test_exact_match.py` for the minimal pattern.
