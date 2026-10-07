# Docling bake-off harness

Measurement harness for spec-0035 R12: it decides, against a pre-registered
rule, whether a Docling parser or a structure-aware chunker is adopted over the
current word-window baseline.

| File | Suite | Purpose |
|------|-------|---------|
| `metrics.py` | — | Pure, stdlib-only metrics: `span_hit`, `recall_at_k`, `mrr_at_k`, `ndcg_at_k`, `context_precision_at_k`, `parse_recall`, `cluster_bootstrap_delta`, `decide` |
| `test_metrics.py` | default (no marker) | Hand-computed fixtures, decision-rule cases, a Hypothesis property |
| `test_bakeoff.py` | `@pytest.mark.docling` | The live bake-off entry point (skeleton today) |

## Running

```bash
RUN_DOCLING=1 MANGOMAS_BAKEOFF_MANIFEST=/path/to/manifest.json make docling-bakeoff
```

Needs a running docling-serve and a real embedding backend. Without
`RUN_DOCLING=1` the collection gate skips `test_bakeoff.py` with the sanctioned
reason; no CI workflow invokes the target (`HOSTED_RUNNER_INFEASIBLE` in
`tests/constants/live.py` records why).

## Decision rule

Fixed before any measurement — see "Pre-registered decision rule" in
`specs/0035-docling-document-ingestion.md`. `metrics.decide` encodes parts (a)
and (b): adopt only if the 95 % document-cluster-bootstrap CI lower bound of
Δrecall@5 is above zero and no stratum regresses by more than five points; a CI
spanning zero is **inconclusive**, never negative. Part (c), the latency and
cost ceilings, is checked by the harness against the bake-off report.

## Status

The corpus manifest, the human-labelled questions (`labels.jsonl`) and the
end-to-end harness are **pending** a later milestone. `test_bakeoff.py` fails
loudly until then rather than passing vacuously.
