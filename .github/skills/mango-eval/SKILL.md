---
name: mango-eval
description: >
  The offline evaluation harness in Mango-Mas V2. Use when: adding or changing
  a Scorer (exact_match, regex_match, contains, json_keys, llm_judge,
  embedding), a Sink (console, json_file, sqlite_results, webhook, langfuse), a
  Target (agent, pipeline, fan_out, echo), or a DatasetSource (jsonl, inline,
  langfuse); wiring the CI quality gate or the regression/baseline gate; or
  running `mangomas eval`. Covers the scorer_registry / sink_registry /
  target_registry / dataset_source_registry seams, the EvalRunner/EvalReport
  contract, exit-code 3 gating, and the entry-point plugin discovery gated by
  MANGOMAS_DISCOVERY_ENABLED.
argument-hint: "Describe the eval change (e.g. 'add a bleu scorer', 'add a csv dataset source', 'gate on pass_rate') or paste a failing eval test"
---

# Mango-Mas Evaluation Skill

## When to Use

- Add a `Scorer` under `src/mangomas/eval/scorers/` (register in `scorer_registry`)
- Add a `Sink` under `src/mangomas/eval/sinks/` (register in `sink_registry`)
- Add a `Target` under `src/mangomas/eval/targets/` (register in `target_registry`)
- Add a `DatasetSource` under `src/mangomas/eval/sources/` (register in `dataset_source_registry`)
- Wire the CI quality gate (`eval/gate.py`) or regression gate (`eval/baseline.py`)
- Run/debug `mangomas eval` and the `MANGOMAS_EVAL__*` settings

---

## Architecture (four registries + runner + gate)

```
eval/protocol.py       Scorer, ScorerContext, ScoreResult protocols
eval/registry.py       scorer_registry (+ sibling registries per component)
eval/scorers/          exact_match, regex_match, contains, json_keys, llm_judge, embedding
eval/sink.py + sinks/  console, json_file, sqlite_results, webhook, langfuse
eval/target.py + targets/  agent (default), pipeline, fan_out, echo
eval/dataset_source.py + sources/  jsonl (default), inline, langfuse
eval/runner.py         EvalRunner (reuses Orchestrator) → EvalReport
eval/gate.py           evaluate_gate() → GateResult (exit 3 on fail)
eval/baseline.py       load_baseline, diff_reports → ReportDiff, evaluate_regression_gate
eval/discovery.py      entry-point plugins (MANGOMAS_DISCOVERY_ENABLED)
```

- Everything is **additive and default-OFF**. Scorers/sinks/targets/sources are
  resolved by name through their registry — never imported concretely by the CLI.
- `EvalRunner.run` takes an optional `target=`; the legacy `agent_name` positional
  is wrapped in the `agent` target (backwards-compatible — see ADR-0004).

---

## Rules (do not regress)

| Rule | Detail |
|------|--------|
| Registry, not import | New components register a factory in their registry; the CLI/runner resolve by name. No concrete imports outside the module. |
| No hard-coded values | Thresholds/paths/timeouts are `DEFAULT_*` constants surfaced via `EvalSettings` (`MANGOMAS_EVAL__*`), keyed per-component under `*_OPTIONS`. |
| Exit codes | 1 = runtime, 2 = config, **3 = gate failure** (raised only after sinks emit). Do not reuse 3 for anything else. |
| Fault isolation | Multiple sinks compose under per-sink fault isolation — one failing sink must not abort the others. |
| Schema version | `EvalSettings.schema_version` is the forward-compat marker; bump deliberately, never silently. |
| Optional deps | `langfuse` sink/source is lazy-imported behind the `langfuse` extra; module stays importable without it. |
| Gate purity | `evaluate_gate` / `diff_reports` / `evaluate_regression_gate` are **pure functions** — no I/O; combine verdicts via `merge_gate_results`. |

---

## Add a new scorer (worked example)

1. Implement the class in `eval/scorers/<name>.py` satisfying the `Scorer`
   protocol (`async score(prediction, expected, *, context) -> ScoreResult`).
   Read `ScorerContext` for `llm` / `embeddings` when the scorer needs a model.
2. Add a `DEFAULT_*` constant for any threshold — no literals.
3. Register `scorer_registry.register("<name>", _factory)` at module import.
4. Ensure the module is imported so registration runs (mirror the siblings).
5. Add `tests/eval/test_<name>.py` using fakes from `tests/fakes.py`
   (`FakeLLM`, `FakeEmbeddingClient`). Do NOT add `embed()` to `FakeLLM`
   (it breaks the embedding-scorer fallback test).

Sinks, targets, and dataset sources follow the identical register-a-factory
pattern against their own registry.

---

## CLI

```powershell
mangomas eval -d <dataset.jsonl> -s <scorer> [-t <target>] `
  [--dataset-source <src>] [-o report.json] [--baseline base.json]
```

Gate flags map to `MANGOMAS_EVAL__GATE_ENABLED`, `MIN_MEAN_SCORE`,
`MIN_PASS_RATE`, `FAIL_ON_ERROR`, and the regression flags (`--baseline`,
`--max-mean-score-drop`, `--max-pass-rate-drop`, `--no-allow-new-failures`).

---

## Verification

```powershell
ruff check --fix src tests ; ruff format src tests
mypy
python -m pytest tests/eval -q
python scripts/check_coverage.py      # eval floor >= 95%

# Optional Langfuse sink/source (extra + env/ADC)
pip install -e ".[dev,langfuse]"
$env:RUN_LANGFUSE='1' ; python -m pytest tests/eval -q
```

See `docs/eval/harness.md`, ADR-0003 (adopt-vs-build), ADR-0004 (target/source
indirection), and ADR-0005 (regression/baseline gating).

---

## Constraints

- DO NOT lower the `eval` (95%) coverage floor to land a change.
- DO NOT hard-code thresholds/paths — route them through `EvalSettings`.
- DO NOT let a gate raise before sinks emit; the report must always be recorded.
- DO NOT import optional-extra SDKs at module top-level — lazy-import them.
