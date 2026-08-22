# Analysis: next-steps roadmap — a peer-reviewed case for the development program

- **Date**: 2026-08-22
- **Scope**: the whole repository at `feat/initial-release` HEAD (`720180b`),
  v0.3.1 with a large `[Unreleased]` block.
- **Method**: three parallel read-only surveys (documentation/spec/ADR corpus;
  source-tree gaps; CI/test/deploy/git state), synthesized into a draft program
  whose load-bearing claims were verified against source, then adversarially
  peer-reviewed by the repo's own `mango-architect` router before presentation.
  The review's verdict was **approve-with-changes**; every correction is folded
  in and the contested findings are recorded in §6 so they cannot silently
  re-drift. Claims below are tagged **[Confirmed]** (verified against source in
  this analysis), **[Corrected]** (the initial finding was wrong and the fix is
  stated), or **[On trust]** (from a survey, cheap to re-verify at pickup).

---

## 1. Thesis

Mango-Mas V2 has **inverted maturity**. Its verification engineering is
exceptional — a 95 % global coverage gate with 21 per-package floors, meta-tests
that test the gates themselves (CI↔Make parity, collection-gate subprocess
proofs, gitleaks self-tests), mutation-proven guards, and a protected-path
governance contract anchored in git history. Its delivery engineering is
immature — the deploy workflow discards its own manifest, no release has been
cut across roughly eight spec deliveries, there is no lockfile and no dependency
scanning. And the advertised product loop — planner → tool → reviewer
multi-agent orchestration — is not actually shipped: no composed flow exists in
`src/`, and the structured-output agents never validate their own schemas.

Because the verification machinery is strong, the marginal risk of change is
low. That argues for **aggressive sequencing on the gaps, not caution**. The
program is ordered by one principle:

> **First make the repo stop misdescribing itself, then make the advertised
> product real, then extend.**

Three of the largest findings (a deploy that would ship library defaults, an
"integration" gate that gates one test, a canonical pipeline that exists only in
docs) are integrity gaps in a repo whose brand — per its own governance-branch
theme, "claims nothing checked" — *is* integrity. That is the reputational risk
to retire first.

## 2. The four-lens argument

**Product.** The pitch is "planner→tool→reviewer multi-agent orchestration,
local-first, GCP-ready." Today a user cannot run that pipeline without
hand-authoring the composition, streaming conversations vanish from `/history`,
and starting the server requires knowing the uvicorn factory incantation.
Items 1.3 (shipped pipeline), 1.2a (streaming integrity), 2.5 (`mangomas
serve`) and 2.4 (Ollama adapter) are the minimum to make the pitch demoable and
adoptable. Ollama is prioritized over a cloud adapter because the target user is
local-first — and it publicly proves the adapter seam's "~40-line subclass"
claim (`adapters/_openai_client.py` already hosts the reusable base).

**SWE.** The architecture debt is concentrated behind three protected files,
and the governance system built precisely to permit disciplined change has never
been exercised for a breaking batch. Two governed batches (§3 Phase 1) retire
spec-0015 R4 and ADR-0013's deferred orchestrator instrumentation in one design
cycle, after which the protected paths go quiet again. Dead config
(`LoopSettings`, `MODEL_OVERRIDE`) is resolved by wiring, not deleting — env
vars that are accepted and ignored are documentation that lies.

**SQE.** The repo has world-class *unit* verification and a false *system*
verification signal: gated cloud suites (LM Studio, live Vertex/GCP, Langfuse,
embeddings-local) that no schedule executes, and a deploy contract test that
does not cover the one deploy defect that matters. The fix is the repo's own
idiom turned on itself — parity/meta-tests asserting that (a) the deploy
workflow applies the manifest, (b) every gated suite that *can* have an
executing home has one, (c) a smoke test runs post-deploy. Each new gate gets
mutation-proofed per house rules (`mango-mutation-proof`).

**Architect.** All GCP seams have landed (Vertex, Postgres, Secret Manager,
Cloud Trace, the Cloud Run pipeline), but the last mile — the deploy workflow —
discards `deploy/service.yaml`, so the seam architecture is currently
unfalsifiable in production shape. Phase 0 makes the deploy pipeline an honest
expression of the manifest, closes the supply chain, and gives the
release-triggered pipeline a real release cadence. After Phase 1, the
orchestrator surface is observable (metrics) and symmetric (streaming/
acceptance parity across topologies) — the precondition for the Phase 3
workflow frontier to compose safely.

---

## 3. The program

### Phase 0 — truth, deploy integrity, release (mechanical; ~1–2 weeks)

Ordering note (from peer review): **deploy integrity lands before the release
cut**, because `deploy.yml` triggers on `release: published` — cutting v0.4.0
first would arm a deploy of a default-config service the moment repo secrets
exist. The hazard is latent today (WIF auth fails without secrets), which
affects urgency, not ordering.

| # | Item | Priority | Basis |
|---|---|---|---|
| 0.1 | **Deploy integrity**: make `deploy.yml` apply `deploy/service.yaml` (today `.github/workflows/deploy.yml:57-64` passes `--image` only — none of the manifest's env vars, secrets, probes, limits or autoscaling bounds reach the service, so a real deploy runs auth-off / SQLite / console exporter) **[Confirmed]**; add a post-deploy smoke step (`/healthz` + `/readyz`); add a workflow↔manifest tie to `tests/deploy/test_deploy_contract.py`, which today asserts nothing about the workflow consuming the manifest **[Confirmed]**. Absorbs the recorded deferred "`verify` job gating `deploy.yml`" (`NEXT_STEPS.md`). | **P0** | spec stub `specs/0024` |
| 0.2 | **Cut v0.4.0**: carve the `[Unreleased]` block (~8 spec deliveries deep), bump `pyproject.toml`, and derive the FastAPI app version from package metadata — `api/app.py:131` hardcodes `version="0.1.0"`, two minor versions stale **[Confirmed]** — with a guard test so it cannot drift again. | **P0** | `mango-release` skill |
| 0.3 | **Supply-chain baseline**: a lockfile (none exists — runtime deps float on `>=` ranges) **[Confirmed]**; a `pip` ecosystem entry in `.github/dependabot.yml` (today `github-actions` only) **[Confirmed]**; `pip-audit` in CI (recorded deferral); pin `eval-gate.yml`'s external git ref (defaults to the moving `main` of `ianshank/Agents`) **[Confirmed]**; digest-pin the base image **[On trust]**. Opportunistic riders: Python 3.13 matrix leg, `pre-commit run --all-files` CI job (both recorded deferrals). | **P0** (riders P2) | |
| 0.4 | **Docs/ledger truth sweep**: adjudicate and tick the acceptance boxes of specs 0019–0023, all of which are fully unchecked while the work is declared done **[Confirmed]** — the repo's own "claims nothing checked" thesis applied to its own spec ledger; fix `README.md`'s `MIN_CHUNK_WORDS` description (see §6.1 — a doc fix, the knob is provably inert); fix the misattributed skip-log message in `rag/pipeline.py` (§6.1); stale `docs/architecture/observability.md` "Phase 3 deferred" note and `cloud-providers.md` v0.4.0 note **[On trust]**; spec-0019's reference to a plan file whose timestamp doesn't exist on disk **[Confirmed]**; CLAUDE.md's `GET /conversations/{id}` → the shipped `GET /history` **[Confirmed]**; `.env.example` drift (recorded as read-protected in the authoring environment — may need a manual human edit; say so rather than silently skipping it). | P1 | |

### Phase 1 — make the advertised product real (the governed tranche)

Protected-path work runs as **small governed batches, each with its own
`BREAKING-CHANGE` trailer, spec, and — where a boundary changes — ADR**. Peer
review corrected two premises here: the trailer obligation is **path-based, not
signature-based** (any touch of `core/orchestrator.py` et al. requires the
marker, additive or not — `pyproject.toml [tool.mangomas.governance]`), and the
repo's own precedent (spec-0014 descoped mid-flight "to keep PR #24 mergeable")
argues against one mega-batch.

| # | Item | Priority | Notes |
|---|---|---|---|
| 1.1 | **Batch A — spec-0015 R4**: extract `core/structured.py` from `core/tools.py` + align `errors.py`, in one trailered commit per the spec's own acceptance box. Two prerequisites the record demands: settle the `harness/governance.py` ownership question that caused the deferral (decision D3b), and **add the new `core/structured.py` to `[tool.mangomas.governance].protected_paths`** so extracted contract code does not quietly leave governance. | P1 (gates 1.2) | spec-0015 |
| 1.2 | **Batch B — orchestrator surface, split into ≥3 governed PRs**: **(a) streaming turn persistence + metrics** — `stream_dispatch` persists nothing and emits nothing, so every SSE conversation is invisible to `/history`, `SummarizeAgent`, tenancy-scoped storage, and the agent metrics (`core/orchestrator.py:286-330`; the API layer confirms no persistence either) **[Confirmed]** — plus an SSE metadata/event channel so `ToolAgent`/`SummarizeAgent` stop silently degrading to a single unlabelled chunk; **(b) orchestrator-level metrics** per ADR-0013's recorded deferral (requires the companion ADR the ADR itself calls for) **plus `LoopSettings` wiring** — both fields are dead and no per-step timeout exists anywhere **[Confirmed]**; a *per-step* timeout can only live inside `dispatch`'s loop, making this a protected-path edit that belongs in this batch, not standalone; **(c) `acceptance_fn`/`max_steps` threading** through pipeline/fan-out and a fan-out partial-results mode (`dispatch_fan_out` is `asyncio.gather` without `return_exceptions` — one bad agent discards successful siblings) **[Confirmed]**. | (a) **P0**, (b)(c) P1 | spec stub `specs/0025` + ADR |
| 1.3 | **Structured-output validation + a shipped pipeline**: `agents/_structured.py:80-89` returns raw LLM content; `planner.py:35` says "callers are responsible for parsing via `ExecutionPlan.model_validate_json()`" and no such caller exists in `src/` **[Confirmed]**. The docstring's caller-validates contract is right — the missing piece is the caller. Ship: validation at a composed-flow seam, a typed error with HTTP mapping (three-file lock-step per `mango-error`), and a canonical planner→tool→reviewer flow reachable via a shipped `WorkflowGraph` example and HTTP. No protected paths. | **P0** | |
| 1.4 | **Rate limiting** — *demoted to P1 by peer review*: ADR-0015's recorded posture is "cheap, default-OFF guards; reject, don't queue" and already ships the 503 in-flight cap; the production perimeter is Cloud Run IAM (`--no-allow-unauthenticated`); and a per-process token bucket is near-meaningless under `minScale 0 / maxScale 10` autoscaling. Present with decision D5; if adopted, library default off per convention, enabled in the deploy manifest. | P1 | ADR-0015 |
| 1.5 | **`MODEL_OVERRIDE` wiring**: a composition-layer change per spec-0014 R4 (per-agent `LLMClient` rather than one shared `ctx.llm`), explicitly *not* protected-path work. `ctx.memory` — fully wired, closed correctly, and read by nothing **[On trust]** — is a feature bet, not a bug: decision D4c. | P1 | |

### Phase 2 — verification honesty + adoption surface (parallelizable)

| # | Item | Priority |
|---|---|---|
| 2.1 | **Integration honesty, rescoped to the feasible** (peer-review correction: `tests/integration/` already runs in every CI pass via `make gated-suites`, and the Postgres suite runs nightly — the genuinely homeless suites are LM Studio, live Vertex/GCP, Langfuse, and embeddings-local **[Confirmed]**). Of those, only **embeddings-local** (CPU sentence-transformers) is feasible on hosted runners → give it a scheduled home plus a parity meta-test asserting every gated suite has an executing home *or* a recorded infeasibility reason. LM Studio cannot run on hosted runners; Vertex/GCP/Langfuse are gated on decision D2 and credentials. Also: grow `tests/integration/` beyond its current single effective test so the CI job's name stops overpromising. | P1 |
| 2.2 | Container image scan (trivy/grype) + SBOM (syft) in CI and on release. | P1 |
| 2.3 | Performance smoke baseline against the fake LLM (latency/regression budget, gross regressions only). | P2 |
| 2.4 | **Ollama adapter** (an `OpenAICompatHTTPClient` subclass; strongest local-first coherence); Anthropic adapter second for the cloud story. | P1 / P2 |
| 2.5 | `mangomas serve` CLI — removes the uvicorn-incantation adoption barrier. | P1 |
| 2.6 | RAG HTTP endpoints (ingest/query — today CLI-only) and a workflow-streaming endpoint. | P1 / P2 |
| 2.7 | pgvector `VectorStoreRepository` (Postgres already wired; completes the GCP RAG seam and gives the single-implementation protocol a second, contract-tested backend). | P2 |

### Phase 3 — workflow frontier + scale (sequenced backlog)

1. **Composite `loop` bodies** — widen `WorkflowStep` to include `SequenceNode`;
   `NEXT_STEPS.md` already records that this needs **no protected-path edit**.
2. **`dag` node kind** — design complete in ADR-0023 (compile to a
   `Sequence`/`FanOut` tree at load time); implement when a requirement appears.
3. **Multi-tenancy Phase 2** (per-tenant `AgentSettings` at dispatch) — pull
   forward only if a real second tenant appears.

**Explicitly not now:** `PluginNode`/open node kinds (no demand signal),
OIDC/multi-token auth (single static token suffices until multi-tenant demand;
revisit with tenancy Phase 2), GCP resource provisioning from this repo
(deliberate ADR-0001 posture — keep it), distributed/queue-backed orchestration
(no scale signal).

---

## 4. Decision register (sponsor-only)

These cannot be taken by an agent; each blocks or shapes a workstream above.

| # | Decision | Recommendation |
|---|---|---|
| D1 | Make the `protected-paths` CI job (and the other gate jobs) **required status checks** — a GitHub branch-protection admin action no file in this repo can express. Until then a trailer-less protected-path PR shows red but merges. | Do it now; it is the cheapest unblock in the register. |
| D2 | Provision (or name) a **GCP project** so the fixed deploy pipeline can be live-validated and the gated cloud suites gain an executing home. Without it, 0.1 ships contract-tested only. | Decide alongside Phase 0. |
| D3 | **Protected-path appetite**: approve the Batch A / Batch B(a–c) plan and its trailers. **D3b**: settle `harness/governance.py` ownership — the recorded blocker on spec-0015 R4. | Approve; assign governance.py to `mango-harness-dev` unless a better owner emerges in review. |
| D4 | Retire-vs-fix calls: **(a)** `MIN_CHUNK_WORDS` — doc fix (see §6.1); retiring the knob would break the config contract tests and is not mechanical. **(b)** `LoopSettings` / `MODEL_OVERRIDE` — wire (1.2b / 1.5). **(c)** `ctx.memory` — keep-as-experimental vs fund a consuming agent vs remove. | (a) doc fix; (b) wire; (c) keep-experimental unless a consumer is funded. |
| D5 | Rate-limiting posture given ADR-0015 and the IAM perimeter. | Adopt at P1: library default off, deploy manifest on. |
| D6 | `GET /agents` authentication. **This is an ADR-0014 reversal, not a bug fix**: the ADR records "Probes … and `GET /agents` stay unauthenticated so Cloud Run health checks and discovery keep working," and the code matches it exactly **[Confirmed]**. With entry-point agent discovery, the route enumerates installed capability. | Authenticate it, via an ADR amendment — not a silent change. |
| D7 | `claude-hud` disposition — the sole blocker on spec-0016 moving to Implemented; needs one hands-on `/claude-hud:setup` run on a host with GitHub reachability. | Schedule the manual run or record won't-adopt. |
| D8 | **Release cadence**. v0.3.1 lags ~8 spec deliveries. | Release per spec-milestone batch, ≤4 weeks between cuts, starting with v0.4.0 (after 0.1). |
| D9 | Approve the two spec stubs shipped with this analysis (`specs/0024`, `specs/0025`) and the Batch-B companion ADR when drafted. | Approve. |

## 5. Execution map — the repo's own corpus

Every executable workstream maps onto the committed agents
(`.claude/agents/mango-*.md`) and skills (`.claude/skills/`), per the
agents-own-surfaces / skills-own-procedure rule:

| Workstream | Agents | Skills |
|---|---|---|
| 0.1 deploy integrity + smoke + contract tie | `mango-ci-dev`, `mango-test-engineer` | `mango-deploy`, `mango-mutation-proof` |
| 0.2 release cut, version-from-metadata | `mango-cli-dev`, `mango-api-impl-dev` | `mango-release`, `mango-config` |
| 0.3 lockfile / SCA / pins | `mango-ci-dev` | `mango-deploy` |
| 0.4 docs/ledger sweep | `mango-adr-author`; `mango-architect` (review) | — |
| 1.1 Batch A extraction | `mango-schema-evolution`, `mango-layering-auditor`, `mango-protocol-auditor` | `mango-harness` |
| 1.2 Batch B (a/b/c) | `mango-orchestrator-dev`, `mango-sse-streamer`, `mango-telemetry-exporter-dev`, `mango-error-taxonomy-dev` | `mango-harness`, `mango-topology`, `mango-observability` |
| 1.3 validation + shipped pipeline | `mango-agent-impl-dev`, `mango-api-impl-dev`, `mango-workflow-graph-dev`, `mango-hypothesis-fuzz` (parse fuzz), `mango-error-taxonomy-dev` | `mango-agent-add`, `mango-workflow`, `mango-error` |
| 1.4 rate limiting | `mango-api-dev` (design) → `mango-api-impl-dev` | `mango-config` |
| 1.5 MODEL_OVERRIDE wiring | `mango-protocol-auditor` (compat audit); composition edit is unowned-surface, route via `mango-backend` | `mango-config` |
| 2.1 integration honesty | `mango-integration-runner`, `mango-test-engineer`, `mango-ci-dev` | `mango-testing`, `mango-coverage-audit` |
| 2.2 scan/SBOM | `mango-ci-dev` | `mango-deploy` |
| 2.4 Ollama adapter | `mango-llm-adapter-dev`, `mango-fake-builder` | `mango-adapter` |
| 2.5 `mangomas serve` | `mango-cli-dev` | `mango-config` |
| 2.6 RAG/workflow endpoints | `mango-api-impl-dev`, `mango-rag-dev`, `mango-sse-streamer` | `mango-rag`, `mango-workflow` |
| 2.7 pgvector | `mango-storage-adapter-dev`, `mango-rag-dev` | `mango-adapter`, `mango-rag` |
| 3.x workflow frontier | `mango-workflow-graph-dev`, `mango-adr-author` | `mango-workflow` |
| eval-gate ref pinning | `mango-eval-dev` | `mango-eval` |
| PR shepherding throughout | `mango-pr-watcher` | — |

## 6. Verification appendix

### 6.1 Findings the peer review corrected

1. **`MIN_CHUNK_WORDS` is provably inert — `NEXT_STEPS.md` is right.** An
   intermediate verification pass claimed the drop guard could fire on a
   trailing window fully covered by the previous chunk's overlap. The review
   refuted this by inspection of `rag/chunker.py`: for `chunks` to be
   non-empty, the previous window ended at `prev_end < n`; the next
   `start = prev_end - overlap`, so `n - start = (n - prev_end) + overlap
   ≥ overlap + 1 > overlap` — `covered_by_prev` can never hold. This matches
   the exhaustive verification already recorded in `NEXT_STEPS.md` and the
   pinning fuzz test. Consequence: D4a is a **documentation fix** (`README.md`
   describes the knob as functional). One refinement: the "document produced no
   chunks" branch in `rag/pipeline.py` **is** reachable (whitespace-only
   document), but its log message misattributes the skip to `min_chunk_words` —
   keep the branch, fix the message.
2. **Unauthenticated `GET /agents` is a recorded decision, not an oversight**
   (ADR-0014). Reframed as decision D6 with an ADR amendment.
3. **"Additive ⇒ no trailer" is false.** The governance contract is path-based:
   any change to a protected file requires the `BREAKING-CHANGE` marker,
   additive or not. Batch B's premise was corrected accordingly.
4. **"ADR-0014 auth-seam relocation" had no basis in the record** — the ADR's
   only deferral is a conditional `AuthenticationError` addition to the error
   taxonomy. The item was dropped.
5. **The "integration suites execute nowhere" claim was overstated** —
   `tests/integration/` runs in CI and Postgres runs nightly; 2.1 was rescoped
   to the genuinely homeless suites and to what hosted runners can feasibly
   execute.
6. **Rate limiting demoted from P0** — conflicts with ADR-0015's recorded
   posture; per-process buckets are near-meaningless under autoscaling; the
   perimeter is Cloud Run IAM.
7. **Deploy-before-release ordering** — the release cut arms the
   release-triggered deploy workflow, so 0.1 precedes 0.2.

### 6.2 Confirmed claims (verified against source during this analysis)

`deploy.yml:57-64` deploys image-only, no manifest application, no env/secrets;
`api/app.py:131` hardcodes `version="0.1.0"`; `agents/_structured.py:80-89`
returns unvalidated content and `planner.py:35` delegates validation to a
nonexistent caller; `core/orchestrator.py:286-330` streams without persistence
or metrics; `orchestrator.py:278` fan-out gathers without `return_exceptions`;
`api/routes/system.py:38-41` serves `/agents` without `require_auth` (matching
ADR-0014); `LoopSettings` fields are read by nothing; no lockfile exists and
`dependabot.yml` covers `github-actions` only; `eval-gate.yml` defaults to an
unpinned external git ref; specs 0019–0023 acceptance boxes are all unchecked;
spec-0019 references a plan filename that does not exist on disk.

### 6.3 Taken on trust — re-verify at pickup

Exact CHANGELOG size; `MODEL_OVERRIDE` consumer absence; `ctx.memory` consumer
absence; base-image pinning mode; stale `observability.md`/`cloud-providers.md`
passages; Langfuse gated-suite thinness; multi-tenancy Phase 2 scope; the
mutation-proof and CI↔Make parity meta-test claims (asserted by the surveys,
consistent with the committed tests, not independently re-executed here). None
of these changes the phase structure if wrong; each executing agent re-checks
its own surface before acting.
