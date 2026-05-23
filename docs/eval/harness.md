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
| `MANGOMAS_EVAL__AGENT` | `chat` | Agent name |
| `MANGOMAS_EVAL__OUTPUT_DIR` | `eval-output` | Default report directory |
| `MANGOMAS_EVAL__PARALLELISM` | `1` | Max concurrent rows |
| `MANGOMAS_EVAL__FAIL_FAST` | `false` | Cancel after first non-pass |
| `MANGOMAS_EVAL__SCORER_OPTIONS` | `{}` | Free-form scorer options |

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

## Logging events

Every event carries `extra={"event": ..., ...}` for structured-log filters:

| Event | Level | Keys |
|---|---|---|
| `eval_dataset_load_start` | DEBUG | `path` |
| `eval_dataset_loaded` | INFO | `path`, `row_count` |
| `eval_start` | INFO | `dataset_size`, `scorer`, `agent_name`, `parallelism`, `fail_fast` |
| `eval_row` | DEBUG | `row_id`, `score`, `passed`, `duration_ms` |
| `eval_error` | ERROR | `row_id`, `error_type`, `duration_ms` |
| `eval_summary` | INFO | `dataset_size`, `passed`, `failed`, `errored`, `mean_score`, `duration_ms` |
| `eval_llm_judge_request` | DEBUG | `scorer`, `threshold` |
| `embedding_scorer_unavailable` | WARNING | `scorer`, `reason` |

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
