# ADR-0041: Open-source eval patterns — port semantics, adopt data, never depend

## Status

Accepted on merge of spec-0045 PR-B (#124); the governance half landed in #123.

## Context

ADR-0003 weighed exactly one external framework (`ianshank/Agents`) and decided
to port its *patterns* natively. It also recorded the price: "feature parity
with the upstream project is a deliberate, ongoing choice rather than
automatic". The question returned with a wider field — DeepEval, Ragas,
promptfoo, Inspect AI, garak/PyRIT, the OTel GenAI semconv, OpenSSF
Scorecard/CodeQL, mutmut — and three constraints that did not exist in 2026-05:
CI runs on GitHub-hosted runners with no LLM; the eval seam is string-only
(`src/mangomas/eval/target.py:31`), so nothing a framework measures about
usage or tool trajectory can reach a scorer; and ADR-0033 forbids a name
(`eval-smoke`, `faithfulness`) that promises a control the code does not
implement. The HTTP black-box seam (`eval_harness_bridge/README.md:23-30`)
already lets *any* external harness evaluate the platform without touching
`src/`.

## Decision

Port only **deterministic metric semantics** natively — measured usage,
Ragas-style tool-call accuracy as recall / extra / policy-violation counts
(not set similarity), promptfoo-style deterministic assertions, Inspect-style
per-sample provenance, and ADR-0034's `json_field` structured scoring.
Adopt frameworks only as **data** (garak/OWASP probe categories, ported into
`evals/datasets/` with attribution) or through the **HTTP seam**; never as a
runtime or `dev` dependency, and never a prompt-based metric without a
calibration record (reliability plan C2).

## Consequences

### Positive

- Nothing new in `pyproject.toml`; `make gate` stays LLM-free and offline.
- Each ported metric is a four-boolean or count-based scorer that a `FakeLLM`
  can drive, so it is provable red-first (`mango-mutation-proof`).
- Attributed datasets teach contributors the threat model directly.

### Negative / Trade-offs

- Maintenance parity is ours — ADR-0003's own stated cost, now across five
  vocabularies instead of one. Each port cites its source so drift is visible.
- Judge-based metrics (faithfulness, relevancy, G-Eval) are **not** available
  until a calibration record exists; a local 4B judge is reported, never gated.
- The trajectory scorer is deliberately coarse: it cannot rank "almost right".

### Neutral

- Inspect's `ModelAPI` adapter and PyRIT multi-turn scenarios remain possible
  as optional extras (Tier 3); this ADR does not foreclose them.
- The bridge remains the integration point for any framework not listed here.

## Alternatives Considered

Written per project as Thesis / Counter-argument / Rebuttal / Verdict.

- **DeepEval.** *Thesis:* adopt its `ToolCorrectnessMetric` and G-Eval for an
  agent platform. *Counter:* tool-correctness is name-set overlap — it rewards
  the right tool with wrong arguments and penalises a legitimate retry; G-Eval
  is judge-based. *Rebuttal:* the useful questions are booleans (required tools
  called, none unexpected, no policy violation, within budget).
  *Verdict:* do not port name-set tool-correctness; `tool_trajectory` records
  counts; G-Eval → Tier 2 behind C2.
- **Ragas.** *Thesis:* `ToolCallAccuracy` and the RAG triad (faithfulness,
  context recall/precision). *Counter:* the triad's prompts were validated on
  GPT-4-class judges, not local 4B models; `ToolCallAccuracy` compares
  names+args by exact match. *Rebuttal:* names are deterministic and
  graded now by `tool_trajectory` (recall / missing / extra / budget);
  arguments are recorded as `sha256:` digests by default, and the recorded
  trace is a *list*, which the `json_field` path (mappings only) cannot index.
  *Verdict:* port the deterministic tool-*name* semantics in Tier 1;
  argument-level exact-match is Tier 2 (list indexing or a dedicated
  `tool_trajectory` option, comparing digests); no faithfulness/relevancy
  prompts without a calibration record; context recall waits for a `Tool`
  metadata channel (Tier 2).
- **promptfoo.** *Thesis:* adopt its YAML assertion runner. *Counter:* a Node
  runtime and a second config language in a Python repo. *Rebuttal:* its
  `json-schema`, `contains`, `latency-under` assertions are one-liners over
  data we already have. *Verdict:* port deterministic assertion semantics as
  scorers; run promptfoo itself only through the HTTP seam if ever.
- **Inspect AI.** *Thesis:* adopt as the eval runtime (`Task`/`Solver`/
  `Scorer`, sample logs). *Counter:* it replaces `EvalRunner`, `Registry[T]`
  and the sinks wholesale, and its `ModelAPI` would be a third LLM adapter.
  *Rebuttal:* its sample-log fields (run id, model, dataset hash, per-sample
  scores dict) are the provenance we lack. *Verdict:* port `RunProvenance` and
  `metadata["scores"]` semantics; `ModelAPI` adapter extra is Tier 3.
- **garak + PyRIT.** *Thesis:* add a red-team dependency and a nightly job.
  *Counter:* both need a live model; CI has none; PyRIT is multi-turn and
  orchestrator-shaped. *Rebuttal:* the OWASP LLM categories are the valuable
  artefact and need no runtime — scripted transcripts through `FakeLLM` +
  `FakeTool(effects=MUTATES)` exercise them deterministically. *Verdict:*
  categories as attributed data (`safety_toolagent_v1`); garak nightly behind
  `vars.REDTEAM_ENABLED` → Tier 2; PyRIT → Tier 3.
- **OpenTelemetry GenAI semconv.** *Thesis:* emit `gen_ai.usage.*` on
  `harness.agent_invoke`. *Counter:* the semconv is Development-status and
  renamed attributes twice; we have no measured usage yet. *Rebuttal:* once
  `_usage.py` records tokens the attributes are a pin away. *Verdict:* Tier 2
  with a pinned semconv version, after measured usage lands.
- **OpenSSF Scorecard + CodeQL.** *Thesis:* "YAML only", add and forget.
  *Counter:* `tests/deploy/test_workflow_hardening.py` pins workflow count and
  action set; both need `id-token: write` / `security-events: write`, the
  first such grants outside `deploy.yml`. *Rebuttal:* Scorecard's
  `Branch-Protection` check *is* the externally maintained branch-rules report
  v1 wanted to script, free on a public repo. *Verdict:* adopt both in PR-A
  with explicit `permissions:` and the hardening test updated first; CodeQL
  skipped while private.
- **mutmut.** *Thesis:* replace the hand-written guard-probe manifest with
  mutation testing. *Counter:* full mutation runs take hours on a 98 %-covered
  tree and the manifest already encodes *which* mutations matter.
  *Rebuttal:* a nightly ratchet on a subset is cheap once the manifest covers
  the governance gate. *Verdict:* keep `guard_probe.py` as the gate; mutmut
  nightly ratchet → Tier 2.

## References

- Spec: `specs/0045-measured-eval-and-governance-mechanisation.md`
- Plan: `docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md`;
  `docs/plans/20260916T214636Z-reliability-evidence-plan.md` (C1–C3)
- Code: `src/mangomas/eval/target.py:31`, `src/mangomas/eval/targets/agent.py:28-30`,
  `src/mangomas/workflow/predicate/_json_field.py`, `eval_harness_bridge/README.md:23-30`
- Related ADRs: ADR-0003 (adopt vs build), ADR-0004/0005 (target and baseline
  seams), ADR-0033 (names must not over-promise), ADR-0034 (`json_field`
  semantics), ADR-0039 (`ToolEffects` enforcement)
