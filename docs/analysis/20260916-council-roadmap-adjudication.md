# Council roadmap adjudication — source-verified (2026-09-16)

A three-model council (Kimi K3, GLM-5.3, Claude Opus 5 Thinking) reviewed the
next development cycle. Kimi K3 produced no output (tool-call loop), so the
council is effectively two models. This document adjudicates their claims
**against the tree at `e1a4717`** rather than restating them. Every row is
either confirmed with a file path, corrected, or marked as a finding neither
model made.

The delivery sequencing that follows from it lives in
[`docs/plans/20260916T214636Z-reliability-evidence-plan.md`](../plans/20260916T214636Z-reliability-evidence-plan.md).

---

## 1. Confirmed

| Claim | Verification |
|---|---|
| The PR-blocking gate chain is entirely structural | `Makefile:gate` = validate-config, lint, format-check, typecheck, lint-imports, frontmatter, protected-paths, test, coverage, bridge-coverage, contracts-coverage, scripts-coverage. Twelve targets, zero behavioural. |
| `llm_judge` routes through the orchestrator's own LLM | `src/mangomas/eval/scorers/llm_judge.py` scores via `context.llm` — the same client the system under test uses. Self-preference bias is structural, not hypothetical. |
| 27 agents, no routing eval | `.claude/agents/` = 27 files; `.claude/skills/` = 17. The only mechanical check is `scripts/lint_agent_frontmatter.py`, which validates YAML frontmatter — not whether a router routes correctly. |
| Coverage floors are past the discriminating range | `scripts/check_coverage.py` sets 100 % on eight floors (`errors`, `registry`, `core`, `secrets`, `correlation`, `tenancy`, `_headers`, `_entry_points`) — the council said seven. |
| `retrieve` is the only tool, and it is read-only | `src/mangomas/composition/rag.py:41-42` builds a one-entry `ToolRegistry` holding `RetrievalTool`, and only when RAG is enabled. The council's "cheapest possible moment to establish effect-identity contracts" is correct. |
| No `AGENTS.md` | Absent at root. The `.claude/`-scoped corpus is invisible to Codex, Cursor, Copilot, Gemini CLI, Windsurf. |
| MCP `2026-07-28` deprecated Roots, Sampling, Logging, DCR and HTTP+SSE | Confirmed upstream, with a **twelve-month minimum** deprecation window measured from the revision that first marks a feature Deprecated. |
| `pass^k` is the reliability metric of record | Confirmed: τ-bench introduced it; `pass^k = p^k` decays exponentially, and a 90 %-pass@1 agent sits at 57 % by k=8. |
| MAST's dominant failure mode is self-verification | Confirmed and **stronger than stated**: FM-3.3 (Incorrect Verification) is the strongest single predictor of failure, and follow-on work puts false success at 44–52 % of failures in single-control domains, rising to **75.8 % among architectures that emit an explicit completion signal**. |

---

## 2. Corrected

### 2.1 A behavioural gate does exist — it is just not load-bearing

The council's framing ("none of them can fail because the agent platform got
worse at its job") is substantially right about the PR-blocking chain and wrong
as stated. `.github/workflows/eval-gate.yml` runs the sibling `eval-harness`
against a live Mango-Mas API. But every property that would make it a gate is
switched off:

- `on: workflow_dispatch` only — it **never runs on a pull request**.
- `if: vars.EVAL_GATE_ENABLED == 'true'` — default-disabled.
- The dataset is **five rows** (`eval_harness_bridge/datasets/mango_suite.source.jsonl`).
- The only active scorer is `exact_match`; the `llm_judge` and `contains`
  blocks are commented out.
- The gate rule is `pass_rate >= 0.5` — three of five rows.
- It exercises one agent (`MANGO_AGENT` defaults to `chat`).
- It installs and runs `git+https://github.com/ianshank/Agents@${AGENTS_REF}`.

This is a better starting position than the council assumed. The bridge,
converter, dataset shape, readiness probe and artefact upload are already
built and unit-covered (`tests/eval_harness_bridge/`, floor 100 %). The work
is **promotion, not construction** — which changes the cost estimate for the
behavioural-gating milestone materially.

### 2.2 There is nothing to port for mutation proofs

The council recommends porting a `gate-mutation-proof` skill from the sibling
harness. It is already here:

- `.claude/skills/mango-mutation-proof/SKILL.md` documents the
  back-up / mutate / assert-failure / restore loop, names the discriminating
  mutation, and records **four** silent-pass failures this repo has already hit.
- `specs/TEMPLATE.md` *mandates* two-sided gate scenarios: "State **both
  directions** for every gate: WHEN the guarded defect is present THEN the gate
  fails … AND WHEN it is absent THEN the gate passes."

So the doctrine is written, the procedure is documented, and **nothing
mechanises either**. There is no `mutmut`, no mutation target, no `guard-probe`
recipe anywhere in `Makefile`, `pyproject.toml` or `.github/workflows/`. The
gap is not knowledge; it is that a prose mandate cannot fail a build. Per the
repo's own spec-0022 R15 ("a constraint written as a mechanism survives agent
turnover"), this is the highest-leverage correction available.

### 2.3 "Pin the semconv version" is not currently actionable

The council advises pinning the OTel GenAI semconv version as explicit config.
The conventions moved out of core semconv in **v1.42.0 (12 June 2026)** into a
dedicated `semantic-conventions-genai` repository precisely so they could
iterate below the core stability bar — and that repository **has no releases or
tags yet**, so there is no versioned schema URL to pin against.

The repo's existing hedge is already the correct one and should simply be left
alone: `src/mangomas/cognitive/constants.py` carries
`GENAI_SEMCONV_STATUS = "development-2026-09"` with the comment "Do not treat
this as a frozen semconv version — aliases are additive", and
`cognitive/genai.py` keeps `gen_ai.invoke_agent` additive behind
`MANGOMAS_SIGNAL__GENAI_SPANS`, never replacing `orchestrator.*` /
`harness.agent_invoke`. Revisit when a tagged release exists; until then the
action item is "no action", not "pin".

### 2.4 The MCP deprecation risk is lower than implied; the injection risk is not

Mango-Mas does not implement MCP — it *consumes* six servers declared in
`.mcp.json`, pinned at `@2026.7.10` / `@2026.7.4` / `v0.20.1`, all predating the
`2026-07-28` revision. With a twelve-month minimum deprecation window, the
audit is a 2027 calendar item, not a now item.

What is a now item is unchanged by that: `fetch` and `github` return
third-party content into agent context, and `mangomas rag ingest <path>` is the
same channel with no stated trust model. Note also that `.mcp.json` is itself a
**protected path** under ADR-0030 (`pyproject.toml:[tool.mangomas.governance]`),
so any change to it requires a `BREAKING-CHANGE: .mcp.json — <rationale>`
trailer.

### 2.5 Ordering: gate-proof before operating profiles

GLM-5.3 wants three operating profiles (Local Solo / Team Evaluation /
Governed Service) as Phase 0; Claude Opus 5 wants gate-proof first. These are
compatible, and gate-proof wins the ordering argument on a specific ground:
gate-proof is two weeks and changes what you *know*, whereas profile definition
is a documentation-and-CI-matrix exercise that is much cheaper to do once you
know which gates are load-bearing. Profiles become the **input** to the
behavioural-gating milestone's CI matrix rather than a phase of their own.

---

## 3. Finding neither model made: acceptance cannot be expressed correctly

> **Revised 2026-09-16 after measurement.** The first version of this section
> claimed the hazard was a false *positive* — a `contains` predicate matching
> `"passed": true` inside `feedback` prose. **That mechanism is false.** JSON
> escapes interior quotes, so a needle carrying quotes can never match inside a
> string value. The probe that disproved it also proved something worse, below.
> The conclusion survives; the asserted mechanism did not, and the published
> version was wrong for several hours.

`src/mangomas/workflow/predicate.py` defines the entire acceptance vocabulary
for the `loop` node — and, via `BranchCase.when`, for branch routing too:

```python
kind: Literal["contains", "regex"]
```

Both compile to a closure over `response.content` — a substring or regex match
against the response **text**. `src/mangomas/agents/reviewer.py` emits
structured JSON (`passed: bool`, `score: float`, `feedback: str`,
`suggestions: list[str]`). So a workflow looping a reviewer until it approves
can only text-match the reviewer's serialised self-report.

Measured against the real `ReviewResult` class, not conjectured:

| Needle | Response | Truth | Predicate | Verdict |
|---|---|---|---|---|
| `"passed": true` | `model_dump_json()` → `{"passed":true,…}` | accept | **reject** | false negative |
| `"passed":true` | pretty-printed (`indent=2`) | accept | **reject** | false negative |
| `passed:true` | rejecting review, `feedback` mentions it | reject | **accept** | false positive |
| `passed:true` | rejecting review, `suggestions` echo it | reject | **accept** | false positive |

The false negatives come first in practice. A human writes the JSON fragment
the natural way, with a space after the colon; `model_dump_json()` emits it
without one; the needle never matches; the loop runs to `max_steps` and raises
`MaxStepsExceeded` **even though the reviewer approved**. Tuning the needle to
the compact form then breaks the moment anything pretty-prints.

The false positives are what the obvious fix produces. Dropping the quotes to
survive formatting drift makes the needle match prose inside `feedback` and
`suggestions` — accepting a review that rejected. **Fixing the false negative
manufactures the false positive.** That trap is the actual finding, and it is a
sharper one than the claim it replaces: there is no spelling of a substring
match that is correct here, and the two wrong spellings fail in opposite
directions.

Two further consequences follow from the same root cause — acceptance expressed
over serialised text rather than parsed structure:

1. **`score >= 0.8` is inexpressible.** There is no numeric comparison in the
   vocabulary at all.
2. **Silent divergence from `validate_output`.** `MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT`
   enforces the schema via `model_validate_json`, but the predicate never
   consults the parsed model, so the two guards cannot agree by construction.

This is MAST FM-3.3 territory — acceptance bound to an agent's own completion
signal with no ground truth — but the mechanism is more mundane and more
certain than the literature framing suggests: the predicate cannot read the
field it claims to test. The fix is small, additive and default-safe: a third
kind that parses the response and tests a named field. See
[spec-0032](../../specs/0032-structured-acceptance-predicates.md) and ADR-0031.

## 4. Procedural gotchas for the work that follows

Two governance constraints will bite any implementation of the council's
recommendations, and neither model accounted for them:

- **`pyproject.toml` is a protected path.** Adding a `[tool.mutmut]` table —
  the natural home for mutation config — requires a
  `BREAKING-CHANGE: pyproject.toml — <rationale>` trailer on at least one
  commit, enforced authoritatively by `scripts/check_protected_paths.py`
  reading the diff between base and head. The gate reads policy from the
  **base ref**, so a branch cannot shrink the set it is judged by.
- **`.mcp.json` is a protected path** on the same terms (ADR-0030).

---

## 5. Net position

The repository's verification engineering is genuinely strong and its
*self-knowledge* is unusually honest — `specs/TEMPLATE.md` already demands
two-sided gate proofs, `mango-mutation-proof` already catalogues four
silent-pass failures, and `check_coverage.py` carries a comment explaining that
it was once the gate's own blind spot at 24 % coverage. The deficit is not
insight. It is that the strongest constraints in the repo are written as prose
mandates in templates and skills, where they cannot fail a build, while every
mechanised gate measures structure rather than behaviour.

The corrected picture is therefore more favourable than the council's: a
behavioural gate skeleton exists, the mutation-proof doctrine exists, and the
GenAI telemetry hedge is already correct. What is missing is the wiring that
turns three existing assets into gates — plus one real correctness hole in the
`loop` acceptance path that no one had yet spotted.
