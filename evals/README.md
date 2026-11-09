# `evals/` — committed evaluation datasets and baselines

Datasets a human reviews and the baselines CI diffs against. This directory is
the "incorporate open-source evaluations" surface of ADR-0041: patterns are
adopted as **data** (rows with attribution) and as deterministic scorers, never
as a runtime dependency. See `docs/eval/harness.md` for the harness itself and
`specs/0045-measured-eval-and-governance-mechanisation.md` for the contract.

## What each lane can honestly claim

| Lane | Command | Proves | Cannot prove |
|---|---|---|---|
| **Self-check** | `make eval-selfcheck` | The harness's exit codes (0 / 3), threshold gate, baseline regression diff and `--no-allow-new-failures` path still fire, using the `echo` target (no LLM). `MUT-EVAL-01` in `scripts/guard_probe_manifest.json` corrupts one row and requires exit 3. | Anything about an agent or a model. It is a harness self-test, named so it cannot be mistaken for a quality gate (ADR-0033). |
| **Agent lanes** | `make eval-lanes` (spec-0045 B7) | Planner / reviewer parse-and-validate paths, `tool_agent` policy and trajectory, INV-16 refusal, scored over the committed datasets with a scripted `FakeLLM`; `diff_reports` against the committed baseline must show no regressed row. | Model quality: the LLM replies are fixtures. |
| **Live** | `make eval-live PROFILE=<name>` (Tier 2) | A real model through `mangomas eval` with provenance (model, profile, dataset hash) so two runs are comparable. | Nothing in CI: GitHub-hosted runners have no LLM. Run it on an operator's LM Studio box and commit the report as the next baseline candidate. |

## Layout

```
evals/
├── datasets/<name>_v<N>.jsonl   # one row per line; `metadata.category` is the slice key
└── baselines/<name>_v<N>.json   # a `json_file` report from a reviewed run
```

Rows follow `docs/eval/harness.md` ("Dataset format"). Row ids are stable
identifiers, never positions: the regression gate keys on `row_id`, so renaming
a row reads as "dropped + new", not "regressed" (ADR-0005). Bump `_v<N>` when
rows change meaning; append rows freely within a version.

## Regenerating a baseline

```bash
make eval-selfcheck-baseline     # rewrites evals/baselines/selfcheck_echo_v1.json
```

Review the diff before committing: a baseline is a claim about expected
behaviour, and `git diff` on it is the review.
