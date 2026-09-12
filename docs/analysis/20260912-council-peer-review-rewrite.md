# Analysis: rewritten GLM / GPT-5.6 / Claude Opus 5 council review

- **Date:** 2026-09-12
- **Scope:** a three-model council review of “MangoMAS / MangoMas_V2”,
  rewritten against this checkout (`Mango-Metrics-NLM/MangoMas_V2` on
  `feat/initial-release`, package `mangomas` 0.4.0), public GitHub/HF
  artifacts, and the cited papers.
- **Method:** local source walk; `gh` + Hub API + Space `app.py` (the
  Space runtime was **SLEEPING**; UI was not exercised); harness README
  and CognitiveSignal 1.0.0 schema on `ianshank/Mango_Code_Agent-Harness`;
  arXiv / NeurIPS / OTel / A2A primaries. GitHub MCP discovery failed;
  Hugging Face MCP required auth and was not used.
- **Verdict:** **approve-with-remap**. The council correctly diagnosed the
  Hugging Face / Gradio prototype as architecture-rich and evidence-poor.
  It incorrectly treated that prototype as this FastAPI runtime. GLM’s
  mango-crop hedge is a name-collision false positive. Keep Kapoor
  cost-controlled eval, MAST as a *trace* taxonomy, INV-16 as a
  two-repo split, and public-surface honesty. Reject “become a runtime”,
  “ship `featurize64` into V2”, “freeze CognitiveSignal 1.0.0 here”,
  “A2A this quarter”, and “expert collapse is the default”.

Claims below are tagged **[Keep]**, **[Reject]**, **[Qualify]**, or
**[Overclaim]** (directionally fair, stated too strongly).

Related in-tree reviews: `docs/analysis/20260908-governed-coding-platform-architecture-review.md`
(architecture-set mix-up), ADR-0029 / spec-0030 (envelope boundary).
Do **not** re-plan from `docs/analysis/20260822-next-steps-roadmap-analysis.md`;
its “no pipeline / no lockfile / deploy discards manifest” thesis is
stale. `NEXT_STEPS.md` already records those as landed.

---

## 1. Thesis

The council could not read this repository: `MangoMas_V2` is private, and
`github.com/ianshank/MangoMAS` 404s. They triangulated from Hugging Face
and then wrote a 90-day plan as if the Gradio demo *were* V2.

That mix-up is the load-bearing error. This checkout is already a typed
orchestration runtime (FastAPI + LM Studio, five protocol agents,
bounded-tree workflows, `mangomas.eval`, CognitiveSignal 1.1.0 producer
default-off, OTel `orchestrator.*` spans). The public Gradio artifact is
a separate research/demo product. The sibling Code Agent Harness is the
authority plane. Merging those three is the orchestrator-merge ADR-0029
forbids.

The honest product sentence for **this** tree:

> A protocol-first orchestration runtime that can emit advisory
> CognitiveSignals; measurement via `mangomas.eval`; authority remains
> in the sibling harness. Hugging Face MangoMAS is an optional future
> `routing.recommendation` producer, not the C1 system.

The honest sentence for the **demo**:

> A CPU Gradio prototype of UI + an untrained ~17K-parameter `RouterNet`
> + heuristic cells; a separate unused ~6.88M torch pickle sits on the
> Hub and is not loaded.

---

## 2. Method and access

| Surface | What was readable | What it establishes |
|---|---|---|
| This repo | Full tree, origin `Mango-Metrics-NLM/MangoMas_V2`, **private** | FastAPI runtime, eval spine, CognitiveSignal 1.1.0, INV-16 producer refuse |
| Org listing | `gh repo list Mango-Metrics-NLM` | Public: `JIRA-TEST`, `MangoMas-Demo`. Private: `MangoMas_V2` |
| `ianshank/MangoMAS` | 404 unauth and with this token | Model-card citation is a dead link. No evidence the repo exists |
| `MangoMas-Demo` | Public, ~51 files, last push 2026-02-23 | Packaged Gradio demo (`mangomas_demo`) still labelled “production-grade” |
| HF Space `ianshank/MangoMAS` | `app.py` (1439 lines), blogs, README; runtime **SLEEPING** | Live path is `RouterNet`, not MoE-7M |
| HF Space `Mango-Metrics-NLM/MangoMAS` | API **401** | Demo README badges a Space that is not publicly readable |
| `ianshank/MangoMAS-MoE-7M` | README, `config.json`, Hub API | `parameter_count` 6,880,282; `downloads` 8; siblings are README / config / `model.safetensors`; `spaces: []` |
| `ianshank/Mango_Code_Agent-Harness` | Public README + `CONTRACT.md` | INV-16 dispose; envelope **1.0.0**; `ExecutionBroker` / `command_actions` |
| `ianshank/Agents` | Public | Outer eval-harness consumed by `eval_harness_bridge/` |

Council claim that the org “only shows `JIRA-TEST`”: **[Reject]** as of
this date. Council claim that OAuth App restrictions blocked GitHub:
**[Qualify]** — unverified; private visibility of V2 is sufficient.

---

## 3. Product map

Treat **HF Space + `MangoMas-Demo` + MoE-7M** as one research/demo
product. Treat **this repo** as the cognitive/orchestration runtime.
Treat the **Code Agent Harness** as the authority plane. Do **not** treat
the Space as a public skin over V2 (C1 is FastAPI → LM Studio, not
Gradio cells).

```text
Public:
  MangoMas-Demo GitHub ──same prototype family──► Space ianshank/MangoMAS
        │
        └──badge 401──► Space Mango-Metrics-NLM/MangoMAS
  Hub ianshank/MangoMAS-MoE-7M  (card claims; Space never loads)
  ianshank/Mango_Code_Agent-Harness   envelope 1.0.0, INV-16 dispose
  ianshank/Agents                     eval-harness

Private:
  MangoMas_V2 / mangomas 0.4.0
        │ CognitiveSignal 1.1.0 (not wire-compatible with harness 1.0.0)
        └── eval_harness_bridge HTTP ──► ianshank/Agents
```

Env-prefix collision: the Demo configures `MANGOMAS_LOG_LEVEL` /
`MANGOMAS_LOG_FORMAT`. This runtime uses the same `MANGOMAS_` prefix
(and `MANGOMAS_LOG__FORMAT` with a nested delimiter). Shared prefix,
different products.

`src/mangomas/harness/` in **this** tree is Claude Code governance plus
an OTel wrap of `dispatch` (ADR-0021). It is **not** `ExecutionBroker`.
Overloading `MANGOMAS_HARNESS__*` for authority is an INV-16 regression.

---

## 4. Council agreement, remapped

Unanimous rows that survive, on the plane that actually owns them.

| Finding | Tag | Plane | Restatement |
|---|---|---|---|
| Public proof is incomplete | **[Keep]** | Demo + HF | Dead GitHub citation; org Space 401; V2 private; Demo still says “production-grade” |
| Need an evaluation **program**, not more cells | **[Keep]** | V2 + Demo | V2 has `src/mangomas/eval/` and no cost-Pareto corpus. Demo publishes **zero** routing accuracy |
| Benchmark against simpler baselines | **[Keep]** | Demo first, then V2 | Kapoor: hold model, tools, and budget constant |
| Identity fragmentation | **[Keep]** | All | `MangoMas_V2` vs `MangoMAS` vs `mangomas` vs `mangomas_demo` vs `MANGOMAS_*` vs `ianshank/MangoMAS` 404 |
| HF page must become reproducible proof | **[Keep]** | HF + Demo | Load the checkpoint **or** stop claiming 7M. Demo already extracted `models.py` / `features.py`; the Hub card still imports unshipped `moe_model` |
| Naming collision | **[Keep]** | Public copy | [mangometrics.io](https://mangometrics.io/) (hospitality analytics) and OFFIS `mango-agents` 2.2.1. Disambiguate. Do not rename `mangomas` in the PR that lands this note |
| Failure taxonomy should govern messaging | **[Qualify]** | V2 contracts + harness | Typed `MangomasError`s and CognitiveSignal 1.1.0 exist. MAST annotation of **traces** does not. Workflow validate is not a policy compiler |

---

## 5. Council disagreement, adjudicated

| Topic | Council split | Ruling |
|---|---|---|
| What the repo is | GLM: hedge + agriculture/CV roadmap. GPT: cognitive-runtime SDK. Claude: router + cells | **[Reject]** GLM crop hedge (name collision with mango-leaf literature). GPT described **this** tree. Claude described **Demo/HF**. They were answering different objects |
| Highest-value next action | Prove the router; canonicalize provenance; ship `moe_model.py` | Provenance freeze is right for **public** surfaces. `moe_model.py` is right for **HF**. Neither is V2’s top gap. V2’s top gap is an eval **program** plus harness **1.1.0 ingest** |
| The defensible moat | Routing layer vs typed circuits vs governance invariants | **[Keep]** Claude’s governance invariants, already implemented as INV-16 **across two repos** (this tree proposes 1.1.0; harness disposes 1.0.0). Do not market ten cognitive cells. Do not claim `mangomas.harness` **is** the broker |
| Naming remedy | Reposition vs always use full name vs rename now | **[Qualify]** Disambiguate public copy now. Package rename is a later product call, not this analysis |
| Will the MoE win? | Neutral / ablate / “loses to an embedding probe” | **[Qualify]** Neutral-measure. Hash dims 0–31 are lexical, which *predicts* weak paraphrase transfer — a hypothesis, not a result |
| Interop | Not raised / adapters later / MCP+A2A this quarter | **[Reject]** A2A this quarter. Complementary MCP (agent→tool) vs A2A (agent→agent) is real ecosystem fact. This service’s agents are in-process FastAPI dispatch. `.mcp.json` is Claude Code session tooling, not a product API. INV-16 forbids cognition speaking MCP/A2A to GitHub |
| Research output | Optional / falsifiable paper / arXiv for traffic | **[Keep]** Gate publication on a cost-controlled, replayable result. arXiv is optional traffic |

Claude Tier 0 “promote CognitiveSignal to publicly documented v1.0.0”
is **[Reject]** as a change **here**. This repo already froze **1.1.0**
(`extra="forbid"`, Settings reject `1.0.0`). The harness still accepts
**1.0.0**. The lineage definition of done is a **companion harness bump**
(already an ADR-0029 consequence), not a new freeze in `mangomas`.

---

## 6. Claim vs reality (Hugging Face Space / Demo)

Confirmed against Space `app.py` and Hub files. Applies to the **demo
plane only**.

| Claim or component | What the accessible code does | Tag |
|---|---|---|
| 7M MoE on the live path | `MixtureOfExperts7M` is defined and **never constructed**. No `hf_hub_download` / `load_state_dict` | **[Keep]** |
| Live router | Untrained `RouterNet` 64→128→64→8, **17,096** params, `torch.manual_seed(42)`, then **+0.15** substring boosts | **[Keep]** |
| Hub `model.safetensors` | 27.5 MB **`torch.save` zip** (`PK… model/data.pkl`), not safetensors. `config.json` `parameter_count`: **6,880,282**. Usage snippet imports unshipped `moe_model`. `pipeline_tag: text-classification` with a default “I like you” widget | **[Keep]** (council understated the pickle/tag mismatch) |
| Downloads | Hub API `downloads`: **8** (not 9) | **[Qualify]** still near-zero; packaging is the better explanation than quality |
| `compose_cells` | Each cell runs on the **original** text; accumulated context is unused by later cells | **[Keep]** |
| Causal cell | Five long words + `random.uniform` effect and ±0.15 interval | **[Keep]** |
| Memory consent | `consent_status = "granted"` even when `"don't remember"` sets `opt_out` | **[Keep]** |
| MCTS | Depth-1 over canned actions; **new untrained** policy/value nets and `torch.randn` embeddings **per call** | **[Keep]** |
| Metrics tab | Times in-process stubs, then prints hardcoded `"~7.15M"` | **[Keep]** |
| Expert → agent map | `expert.replace(" Expert", " Agent")`; only Research / Security / Performance bind; **5/8 routes silently become SWE**, which maps to the `reasoning` stub | **[Keep]** — council missed this |
| Cell taxonomy | Space `CELL_TYPES` (Empathy, Curiosity, FigLiteral, R2P, Telemetry, Aggregator, …) vs profile post (Planning, Perception, Learning, Communication, MetaCognitive, Creative). Overlap: **four names** | **[Keep]** |
| Demo README | “production-grade”; badges `huggingface.co/spaces/Mango-Metrics-NLM/MangoMAS` (**401**) | **[Keep]** — council missed `MangoMas-Demo` entirely |
| Author | Cruickshank vs Shanker; HF blog URLs 404; copies live under Space `blog/` | **[Keep]** |
| Switch-style sparse MoE | Dense 16-tower weighted sum, **no** aux loss, **no** capacity factor | **[Overclaim]** if cited as Switch |

`featurize64` dimension split matches the card (32 hash-sinusoid + 16
substring tags + 8 structural + 4 sentiment + 4 novelty, then L2). Dims
0–31 are `sha256` bytes through `sin`, not embeddings. Dim 58 is a
constant 0.5 before normalisation.

---

## 7. Honest remainder in this runtime

These are true of **this** tree even after correcting the mix-up. They
are not “the demo’s bugs imported here.”

| Gap | Evidence | Tag |
|---|---|---|
| Eval **program** vs eval **spine** | Scorers/targets/sinks exist. Gate default-off (`EVAL_GATE_ENABLED`, `DEFAULT_EVAL_GATE_ENABLED=False`). Dataset `eval_harness_bridge/datasets/mango_suite.jsonl` is **n=5** chat smokes. `duration_ms` is recorded; no cost scorer; gate uses `mean_score` / `pass_rate` / `fail_on_error` only | **[Keep]** Kapoor as methodology, not as “no eval package” |
| Workflow validate is parse-only | `POST /workflows/validate` → JSON + Pydantic `WorkflowGraph`. No agent-existence check, no `policy_id`, no PDP | **[Keep]** as design (ADR-0011 / ADR-0023), not as a missing OPA engine |
| DAG compiler | Bounded tree, acyclic by construction. Future `dag` kind would compile in the **loader** to `SequenceNode` of `FanOutNode`s. Explicitly deferred | **[Reject]** GPT’s “pipeline compiler” as a V2 hole to fill *now* |
| Cognitive emit coverage | Only `planner` / `reviewer` via `_structured.handle`. Chat/summarize do not emit. `tool` raises rather than mapping to `implementer`. `routing.recommendation` exists as a **payload schema** and is not produced | **[Keep]** |
| 1.1.0 vs harness 1.0.0 | Settings reject `1.0.0`. Harness `ACCEPTED_SCHEMA_VERSION = "1.0.0"`. Ingest blocked until companion bump | **[Keep]** — this is the lineage integration blocker |
| GenAI spans | `GENAI_SEMCONV_STATUS = "development-2026-09"`; `MANGOMAS_SIGNAL__GENAI_SPANS` default-off. Alias opened **inline** in `cognitive/producer.py`, not behind `telemetry/exporters.py`. Live spans stay `orchestrator.*`. No token/cost attributes | **[Qualify]** pin as an export helper later; do not replace live span names |
| Bypass tests | `tests/test_execution_bypass_inventory.py` is an AST scan of `agents/` + `core/` (empty spawn/write allowlists). `tests/cognitive/test_readonly_tools.py` bans string literals, not runtime writes | **[Qualify]** static fences, not a runtime PDP |
| INV-16 here | `refuse_cognitive_pdp_fields` raises (does not strip). `policy_input_from_signal` is identity/policy only. No `ExecutionBroker` / `command_actions` in this tree | **[Keep]** producer-side only |
| LearningCell vs evaluator | No LearningCell. Skill constraint: no Memory/Learning write-back into `mangomas.eval` in the same `EvalRunner.run` | **[Keep]** as a **forbidden future** |
| 2026-08-22 leftover | Pipeline, `validate_output`, lockfile, deploy-manifest apply have landed (`NEXT_STEPS.md` Phase 1 / later “done” sections). Do not resurrect them as current gaps | **[Reject]** if a follow-up copies the August thesis |

Shipped orchestration that the council treated as absent:
`examples/workflows/plan-execute-review.json` (`planner` → `tool` →
`reviewer`). Supervisor-shaped `dispatch` / pipeline / fan-out already
exist. “Add a supervisor topology” is **[Reject]** as net-new.

---

## 8. Bibliography

Cite primaries. The council’s ~190-URL dump mixed these with mango-leaf
datasets, MangoBase genomics, MaNGOS, and hospitality SaaS.

### Keep (main text)

1. Sayash Kapoor, Benedikt Stroebl, Zachary S. Siegel, Nitya Nadgir,
   Arvind Narayanan. *AI Agents That Matter.* TMLR 2025.
   arXiv:2407.01502 — accuracy-without-cost rewards expensive agents;
   Pareto + holdouts.
2. Mert Cemri et al. *Why Do Multi-Agent LLM Systems Fail?* NeurIPS 2025
   Datasets & Benchmarks. arXiv:2503.13657 **v3** — 14 modes, 3
   categories; taxonomy from ~150 traces (κ = 0.88); MAST-Data = **1,642
   annotated traces** (not 1,600+ tasks). v2’s “7 frameworks / 200+
   tasks” is the construction set, not the NeurIPS dataset size.
3. Kunlun Zhu et al. *MultiAgentBench: Evaluating the Collaboration and
   Competition of LLM agents.* ACL 2025. arXiv:2503.01935 — MAS eval for
   the harness story, not a V2 unit-test stand-in.
4. OpenTelemetry `semantic-conventions` **v1.42.0** (2026-06-12) and
   `open-telemetry/semantic-conventions-genai` — `gen_ai.*` **moved**,
   still **Development** (0 of 63 attribute keys Stable as of August
   2026). v1.42.0 is a move, not a graduation.
5. A2A Protocol docs + Linux Foundation / AAIF (A2A v1.0 2026-03-12;
   A2A into AAIF 2026-08) — complementary to MCP; not a V2 deliverable
   this quarter.
6. Hugging Face model card `ianshank/MangoMAS-MoE-7M` and Space
   `ianshank/MangoMAS` `app.py` — primary for the demo plane.
7. Harness `CONTRACT.md` INV-16; this repo ADR-0029 / spec-0030 —
   cognitive proposes, harness disposes.

### Qualify (short, scoped)

8. William Fedus, Barret Zoph, Noam Shazeer. *Switch Transformers.*
   JMLR 2022. arXiv:2101.03961 — sparse top-1 + aux loss; **not** this
   demo’s dense 16-way sum.
9. Noam Shazeer et al. *Outrageously Large Neural Networks.*
   arXiv:1701.06538 — original routing-imbalance argument.
10. Zewen Chi et al. *On the Representation Collapse of Sparse Mixture
    of Experts.* NeurIPS 2022. arXiv:2204.09179 — sparse
    **representation** collapse; do not conflate with load collapse.
11. Theodore Sumers, Shunyu Yao, Karthik Narasimhan, Thomas Griffiths.
    *Cognitive Architectures for Language Agents* (CoALA). TMLR 2024.
    arXiv:2309.02427 — conceptual overlay; this runtime does not
    “implement CoALA.”
12. Rico Schrage et al. *mango: A Modular Python-Based Agent Simulation
    Framework.* SoftwareX 2024. arXiv:2311.17688; PyPI
    `mango-agents==2.2.1` — energy/simulation FIPA MAS; **namesake
    only**. Council’s v2.1.5 is docs lag.
13. [Mango Metrics](https://mangometrics.io/) — live hospitality
    investor-analytics product; disambiguate in public copy.

**[Overclaim]** “Expert collapse is the default” for 16 experts / 10
classes / online softmax. Collapse is a documented **risk** of
unbalanced sparse routers (Shazeer, Fedus). The demo is a dense mixture
with unpublished gate-entropy. Demand histograms before the slogan. The
class head sits **after** the mixture: 16 towers are not 16 specialists.

**[Overclaim]** “Star/Supervisor is ~70% of production.” Practitioner
blogs cite 55–70%; not a single audited census. This runtime already
ships supervisor-shaped dispatch.

### Drop

MangoBase; mango-leaf disease papers and datasets; mangos/MaNGOS; Mage
“Mango V2” except as search-pollution; Mango.jl-as-this-stack; any blog
that asserts `gen_ai.*` is Stable.

---

## 9. Next-step map (recommendations; not this change)

Each line names the owning repo. This analysis does not implement them.

### A. Public honesty (`MangoMas-Demo` + Hugging Face)

1. Relabel Demo/Space as a research demo. Delete “production-grade”,
   “PPO in production”, and FastAPI-gateway ASCII unless captioned as
   target-not-this.
2. Either load Hub weights into `MixtureOfExperts7M` **and call it**, or
   stop putting “~7M” on the live path. Publish **17,096** for the live
   `RouterNet`.
3. Point the Hub card at Demo’s `mangomas_demo.models` / `features` (or
   add `moe_model.py`); convert the pickle to real safetensors or rename
   to `pytorch_model.bin`; fix `pipeline_tag`; link Space↔model; replace
   404 GitHub/blog links; make `EXPERT_NAMES` match `AGENTS` so 5/8
   routes stop falling through to SWE.
4. Memory consent default-deny; label simulated causal/MCTS in the UI.
5. Routing scorecard under a frozen budget (Kapoor): regex/keyword (dims
   32–47) vs TF-IDF vs MiniLM probe vs live `RouterNet` vs (optional)
   loaded 7M vs LLM router; paraphrase and keyword-ablation splits; gate
   entropy / per-tower histograms.

### B. This repo (later PRs)

1. Eval **program**: a cost/token dimension; a dataset larger than n=5;
   hold model/tools/budget constant across `echo` / single agent /
   deterministic workflow / pipeline. Engage `EVAL_GATE_ENABLED` only
   with a pinned `ianshank/Agents` SHA.
2. MAST-inspired injected-failure tests on the shipped graph (duplicate
   event, timeout, missing artifact, unauthorized tool proposal) —
   terminal states, not new cells. Use MAST’s 14 codes; do not invent a
   fifteenth “Mango” mode.
3. Optional GenAI span **export helper**, default-off; do not replace
   `orchestrator.*` as the live contract. No token/cost attributes until
   a real usage source exists.
4. Do not emit `routing.recommendation` from planner/reviewer until a
   real router producer exists; keep the payload schema reserved.
5. Forbidden: Memory/Learning write-back into `mangomas.eval` in the
   same `EvalRunner.run`; `write_file` / `run_command` / `apply_patch`
   until the harness isolation backend exists; OPA inside
   `workflow.validate`; A2A/MCP as a product surface this quarter.

### C. Harness (`ianshank/Mango_Code_Agent-Harness`)

1. Ingest CognitiveSignal **1.1.0**.
2. Keep `command_actions` allowlist, unclassified → `destructive`, and
   verifier without `write_file`.
3. Shadow-eval before GitHub writes (Kapoor + the architecture-set
   Change 08 already recorded in the 2026-09-08 analysis).

**Definition of done (lineage, not this change):** a cost-controlled
improvement over the strongest simpler baseline, with package version /
graph id / policy snapshot / (when relevant) model SHA on the trace —
**or** a published loss to that baseline and a demo that no longer
pretends otherwise.

---

## 10. What this rewrite does *not* change

No runtime, eval corpus, DAG node, GenAI adapter, Hub write, Demo
edit, package rename, or public-ing of `MangoMas_V2`. Identity hygiene
in `README.md` / `CHANGELOG.md` / `NEXT_STEPS.md` is the only in-tree
follow-through of §3 and §4.
