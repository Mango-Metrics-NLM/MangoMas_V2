# Analysis: workflow-governance audit (nine control questions)

- **Date:** 2026-09-16
- **Scope:** `Mango-Metrics-NLM/MangoMas_V2` @ `7de769f`, package `mangomas`,
  plus the in-repo `mango-integration-contracts` and `eval_harness_bridge`
  sub-packages.
- **Method:** static source walk of `src/mangomas/`,
  `mango-integration-contracts/src/`, `scripts/`, `tests/`, `.claude/`,
  `.github/workflows/`, `pyproject.toml`, `sitecustomize.py`, `.mcp.json`,
  `specs/`, `docs/adr/`. No runtime execution; no network. Every verdict below
  cites a file and line.
- **Passes:** two. The nine numbered sections are the first pass. **Peer review
  — second pass** (below) re-reads them adversarially: it corrects two claims,
  reworks one verdict's reasoning, and adds four findings (N1–N4) the first
  sweep missed. Read the second pass before acting on the first — N1 changes
  what §3 is about.
- **Plan:** `docs/plans/20260916T140000Z-governance-hardening-plan.md`.
- **Frame:** the nine questions describe an **execution-governance** posture
  (a governed agent acting on a repository through a policy decision point).
  Mango-Mas V2 is not that system. It is an **LLM dispatch runtime** whose
  sibling repo (`ianshank/Mango_Code_Agent-Harness`) holds the harness, the
  PDP, and the broker — INV-16, recorded in ADR-0029, puts execution
  authority there by design. So several "no" verdicts below are **correct
  scope decisions, not defects**, and are marked as such. The findings that
  matter are the ones where V2 *claims* a control it does not actually hold,
  or holds the ingredients and leaves them unwired.

---

## Verdict table

| # | Question | Verdict | Severity |
|---|----------|---------|----------|
| 1 | Canonical workflow state | **No** — in-memory call tree only | High |
| 2 | Stable identifiers | **Partial** — human IDs exist, no machine link | Medium |
| 3 | Policy authority | **No** — four paths let the tree weaken its own gates (§3, N1, N2, N4) | **Critical** |
| 4 | Approval binding | **No** — one shared bearer grants everything | High |
| 5 | Replay resistance | **Partial** — expiry modelled, never enforced; no nonce | High |
| 6 | Evidence schemas | **Partial** — one strong schema, four surfaces unversioned | Medium |
| 7 | Failure containment | **Partial** — loops bounded, failures leave no record | High |
| 8 | Idempotency | **No** — key type exists, unwired; turns duplicate | High |
| 9 | Model selection | **Partial** — centrally configured, not reviewable | Medium |

Second-pass additions, not numbered above because they cut across sections:

| # | Finding | Section | Severity |
|---|---------|---------|----------|
| N1 | `sitecustomize.py` can silently disarm the test + coverage gates | 3, 7 | High |
| N2 | A plugin can override the scorer the CI eval gate reads | 3, 6 | High |
| N3 | `PostToolUse` runs a model-chosen path through `sh -c` | 4 | Medium |
| N4 | `.mcp.json` auto-loads six servers, unprotected (versions pinned) | 3 | Low-med |

---

## 1. Canonical workflow state

**Is agent progress represented in a persistent state machine or only in
prompt/session history?**

**Verdict: only in prompt/session history.** There is no state machine, no run
record, no checkpoint, no resume.

Evidence:

- `src/mangomas/workflow/executor.py:33-40` — `execute_workflow` resolves the
  root node and awaits it. The entire "state" of a workflow is the Python call
  stack plus the `AgentResponse` threading between nodes. Nothing is written.
- `src/mangomas/core/orchestrator.py:269-296` — the dispatch loop's progress
  lives in local variables (`current_messages`, `steps_taken`, `accepted`), and
  advances by **appending the assistant reply to the message list**. That is
  literally "progress as prompt history".
- `src/mangomas/core/orchestrator.py:317-318` — the only durable write in the
  whole dispatch path: one `save_turn` call, **after** the loop finishes.
- `src/mangomas/adapters/storage/sqlite.py:36-44` — the `turns` table is
  `(id, ts, agent, request, response, tenant)`. A flat append-only log of
  finished turns. There is no run table, no step table, no status column, no
  parent/child edge.

A `grep` for `checkpoint|resume|state_machine|run_state` across
`workflow/`, `core/` and `eval/` returns nothing but `save_turn` call sites.

**Consequence.** A workflow that dies at step 3 of 5 cannot be resumed,
inspected, or reconciled. The only trace is OTel spans (ephemeral, sampled)
and log lines. Nothing external can answer "what is run X doing right now?"
or "what did run X do before it died?"

**Note on scope.** For a synchronous request/response LLM API this is a
defensible design. It stops being defensible the moment a workflow acquires
external side effects — which `ToolAgent` already permits
(`src/mangomas/agents/tool_agent.py:135`).

---

## 2. Stable identifiers

**Can every task and side effect trace back to immutable requirement, ADR, and
architecture-element IDs?**

**Verdict: partial — a good human-readable scheme, with no machine link and no
architecture-element IDs at all.**

What exists and works:

- `specs/NNNN-slug.md` (31 specs) and `docs/adr/NNNN-slug.md` (26 ADRs), both
  with stable integer IDs, mirrored numbering, and a `Linked ADR:` field in
  `specs/TEMPLATE.md:4`.
- Dense citation in source: **50 of 180** `src/**/*.py` files cite at least one
  `spec-NNNN` or `ADR-NNNN`.
- Requirement-level citations exist and **all five resolve**: `spec-0015 R4`
  (`specs/0015-package-decomposition.md:95`), `spec-0017 R7` (`:67`),
  `spec-0022 R14` (`:60`), `spec-0026 R2` (`:59`), `spec-0029 R5` (`:148`).

The gaps:

- **No requirement-ID convention.** `specs/TEMPLATE.md:16-20` asks for
  unnumbered bullets. Requirements that *are* numbered use three incompatible
  forms across the corpus: `### RN` (9 occurrences), `- **RN` (38), `- RN `
  (29). Most specs have no numbered requirements at all, so most code
  *cannot* cite one.
- **No link checker.** Nothing in `make gate` verifies that a cited
  `spec-NNNN RN` exists. The five current citations resolve by care, not by
  construction. `scripts/lint_agent_frontmatter.py` lints agent/skill
  frontmatter — not cross-references.
- **No architecture-element IDs.** `docs/architecture/c1..c4-*.md` are C4
  models in prose; a grep for any `ARCH-`/`ELEM-`/`COMP-` style identifier
  returns zero. There is no addressable name for "the LLM adapter seam" that a
  commit or a signal could reference.
- **Side effects carry no ID at all.** The `turns` row
  (`adapters/storage/sqlite.py:36-44`) has no `run_id`, no `task_id`, no
  `trace_id`, no spec reference. `CognitiveSignal` has `run_id`/`task_id`
  (`cognitive_signal.py:78-79`) but they are minted fresh per call when the
  caller omits them (`cognitive/producer.py:75-87`) and are never persisted to
  the turn store. The two evidence surfaces cannot be joined.

---

## 3. Policy authority

**Can a governed project alter its own verifier or approved policy hashes?**

**Verdict: yes, on both counts. This is the most serious finding.**

### 3a. The protected-path policy is self-amendable

The protected-path set is data in the governed tree:

```toml
# pyproject.toml:135
[tool.mangomas.governance]
protected_paths = [ "src/mangomas/core/agent.py", ... ]
breaking_change_marker_aliases = ["BREAKING-CHANGE", "# approved-breaking-change"]
```

`pyproject.toml` **is not itself in `protected_paths`**. Neither is any part of
the enforcement machinery:

| File | Role | Protected? |
|---|---|---|
| `pyproject.toml` | the policy table | **No** |
| `scripts/check_protected_paths.py` | the authoritative CI gate | **No** |
| `scripts/_governance.py` | shared marker matcher | **No** |
| `src/mangomas/harness/governance.py` | in-package fallback set | **No** |
| `.github/workflows/ci.yml` | invokes the gate | **No** |
| `Makefile` | `protected-paths` target | **No** |

So an agent working in this repo can delete an entry from `protected_paths`,
or edit `check_protected_paths.py` to `return EXIT_OK`, and that commit needs
no `BREAKING-CHANGE` marker — because the gate reads the policy *from the same
commit it is checking* (`scripts/check_protected_paths.py:126-129`
loads the table, then diffs). The verifier validates against a policy the
candidate change controls.

**The one real mitigation**, and it is a good one:
`tests/harness/test_governance.py:179-206` pins the `pyproject.toml` table to
the two hand-maintained fallback constants
(`src/mangomas/harness/governance.py:41-53` and the `scripts/` copy). Silently
shrinking the protected set in one place fails the suite. But all three
locations are unprotected and editable in a single commit, so the pin raises
the cost of the change — it does not prevent it. It is a **drift detector, not
an authority boundary.**

The docstring at `scripts/check_protected_paths.py:6-9` states the gate reads
"state an in-session agent cannot rewrite". That is true of *commit messages*.
It is not true of the *policy* or the *gate's own code*, both of which sit in
the working tree. The claim is stronger than the mechanism.

### 3b. The cognitive policy hash is self-computed

`src/mangomas/config/signal.py:40-42` derives the approved policy hash from
the policy's own id and version:

```python
def policy_snapshot_hash_for(policy_id, policy_version):
    return "sha256:" + sha256(f"{policy_id}:{policy_version}").hexdigest()
```

and `_rebind_default_policy_hash` (`:81-102`) recomputes it whenever the
operator changes `policy_id`/`policy_version`. Every `CognitiveSignal` then
carries that hash (`cognitive/producer.py:234-236`).

This is internally consistent and the rebind logic is correct for what it
claims. But it is a **checksum of two env vars, not a digest of a policy
document.** It proves nothing about policy content; it cannot detect a changed
rule; and the emitting process computes it itself. A verifier that trusts
`policy_snapshot_hash` to mean "this signal was produced under approved policy
P" is trusting a self-signed assertion. `MANGOMAS_SIGNAL__POLICY_SNAPSHOT_HASH`
is operator-settable to any 64-hex value, so a signal can claim any policy
snapshot.

The honest reading — and `config/signal.py:1-8` half-says this ("It never
grants tools, models, or timeouts") — is that this field is **provenance
labelling, not attestation.** The gap is that nothing in the codebase or docs
says so at the point a consumer would read it.

### 3c. Session-level self-governance is advisory

`.claude/settings.json:22-23` denies `Edit(/.claude/settings.json)` and
`Edit(/.claude/settings.local.json)`, and `harness_config_audit.py` handles
`ConfigChange`. Two structural limits:

- `MANGOMAS_HARNESS__CONFIG_AUDIT_MODE` defaults to `off`
  (`harness/config_audit.py:56-57` returns `allow` unconditionally), so the
  audit hook is inert unless an operator opts in.
- The deny rules match `Edit`/`Write`. `Bash` is not denied for file writes —
  `sed -i .claude/settings.json` is not covered, exactly as ADR-0021 concedes
  for the `PreToolUse` hook. The same bypass applies here.

ADR-0021 is admirably honest that the hook layer is advisory. That honesty
does not extend to the CI gate, which is described as authoritative while its
policy input remains inside the audited tree.

---

## 4. Approval binding

**Are approvals tied to exact actions, resources, destinations, and
revisions?**

**Verdict: no.** Two approval mechanisms exist; neither binds to an action.

### 4a. The `BREAKING-CHANGE` marker is a scope-free token

`scripts/check_protected_paths.py:123-153`: if *any* protected file changed
and *any* commit in the range contains a `BREAKING-CHANGE` line, the gate
passes. The marker does not name:

- **which** protected file it approves (one marker covers all six),
- **what** the change does,
- **which revision** it was granted against (a marker on commit 1 approves
  arbitrary protected-path edits in commits 2..N of the same branch),
- **who** granted it (any commit author).

The line-anchored regex (`scripts/_governance.py`, mirrored at
`harness/governance.py:108-112`) is well built — it correctly rejects
mid-sentence mentions and deleted marker lines. That is careful work on the
*parsing* of a token that carries no scope.

### 4b. API authorization is one shared bearer token

`src/mangomas/api/auth.py:60-95` resolves **a single** expected token from
`AuthSettings.secret_ref`; `require_auth` (`:124-159`) compares the presented
credential against it in constant time. The comparison is correct and
carefully reasoned. But the grant it implements is binary and global:

- No principal identity. `AuthState` (`:52-57`) is `(enabled, expected_token)`.
  There is no subject, no claims, no scope, no audience, no expiry.
- No per-route or per-agent scoping — `agents.py:37,59,69` and
  `workflows.py:35,54` all take the same `Depends(require_auth)`.
- **No actor in the audit trail.** The access log
  (`api/middleware/access_log.py:62-69`) records `request_id`,
  `correlation_id`, `method`, `path`, `status_code`, `latency_ms` — no principal. The `turns`
  row stores no actor either. The system cannot answer *who* did anything.
- **Tenant is client-asserted.** `TenancyMiddleware`
  (`api/middleware/tenancy.py:34`) reads `X-Tenant-ID` straight from the
  request; `tenancy.py` sanitizes the characters but nothing binds the tenant
  to the credential. With auth enabled, any holder of the one token can set
  any tenant and read any tenant's history via `GET /history`. The tenancy
  isolation in `sqlite.py:122-131` is real at the SQL layer and unauthenticated
  at the trust layer.

### 4c. The widest grant: arbitrary graph execution

`src/mangomas/api/routes/workflows.py:33-49` — `POST /workflows/run` accepts a
caller-supplied inline `definition` and executes it. Per its own docstring
(`:40-41`), **"A per-request `definition` runs even when the feature is
disabled"**. So `MANGOMAS_WORKFLOW__ENABLED=false` does not stop workflow
execution; it only stops *server-configured* graphs. One shared bearer token
authorizes an unauthenticated-as-to-identity caller to compose and run an
arbitrary agent graph — fan-outs, loops up to `max_steps`, branch trees — with
no approval bound to that graph, its agents, or its revision.

Bounds that do apply: the graph is a depth-2 acyclic tree
(`workflow/graph.py:80-84`), `LoopNode.max_steps` is `ge=1` with a default,
and body size/concurrency middleware can cap load. Those bound *cost*, not
*authority*.

---

## 5. Replay resistance

**Are expiry and nonce controls implemented, rather than only documented?**

**Verdict: expiry is implemented in the schema and enforced nowhere. There is
no nonce.**

Implemented, and genuinely well:

- `cognitive_signal.py:89-91` — `created_at`, `expires_at`, `ttl_seconds`
  (bounded `1 .. 30 days`).
- `:143-150` — a model validator rejects an envelope whose `expires_at` drifts
  more than `TTL_SKEW_SECONDS = 5` from `created_at + ttl_seconds`. A forged
  long-lived envelope cannot be constructed by inflating `expires_at` alone.
- `:116-121` — timestamps must be timezone-aware; naive input is rejected.
- `:172-182` — `is_expired()` and `is_prompt_eligible()` are provided.

Not implemented:

- **No consumer.** `grep -rn "is_expired|is_prompt_eligible|ttl_seconds" src/`
  returns **zero hits outside the contracts package.** Nothing in `mangomas`
  ever asks whether a signal is expired. Expiry is a property of the record
  that no code reads.
- **TTL is not configurable.** `cognitive/producer.py:227-242` calls
  `CognitiveSignal.create(...)` without `ttl_seconds`, so every signal this
  repo emits uses `DEFAULT_TTL_SECONDS` (24 h). `SignalSettings`
  (`config/signal.py:58-80`) exposes no TTL field.
- **No nonce, anywhere.** `signal_id` is a `uuid4` (`:214`), which gives
  uniqueness but is not a nonce: nothing records seen ids, so re-POSTing the
  same envelope to `HttpCognitiveSink` (`cognitive/sink.py:70-78`) is accepted
  as many times as it is sent. `JsonlCognitiveSink` appends unconditionally
  (`:57-60`).
- **No signature or MAC.** The envelope is unsigned. `policy_snapshot_hash` is
  self-computed (see §3b). A replayed or hand-forged envelope is
  indistinguishable from a genuine one at the sink.
- **The API surface has none of this.** No nonce header, no timestamp header,
  no request-id dedupe. `correlation_id` is explicitly client-supplied and
  re-used across requests by design (`api/middleware/access_log.py:77-80`), so
  it is the opposite of a nonce.

This is the clearest instance of the pattern the question is aimed at: the
control is **documented in the type** and absent from the system.

---

## 6. Evidence schemas

**Are actor, trace, policy, gate, action, and side-effect records
machine-readable and versioned?**

**Verdict: one surface is exemplary; the rest are ad hoc.** Per record type:

| Record | Machine-readable | Versioned | Where |
|---|---|---|---|
| **Cognitive signal** | Yes (Pydantic, `extra="forbid"`, frozen, JSON Schema export) | **Yes** — `schema_version: Literal["1.1.0"]` | `cognitive_signal.py:66-109`; `json_schema.py:11` |
| **Proposed action** | Yes (frozen, validated, JSON Schema export) | **No version field** | `proposed_action.py:23-56` |
| **Evidence bundle** | Yes | Inherits the signal's | `evidence.py` |
| **Policy binding** | Yes — `(run_id, task_id, policy_id, policy_version, policy_snapshot_hash)` | Via `policy_version` | `validation.py:14-20` |
| **Actor** | **Absent** | — | nothing records a principal (§4b) |
| **Trace** | Partial — OTel spans + `correlation_id`; join keys land in `SignalLineage.source_event_ids` as `otel-trace:` / `mangomas.correlation_id:` strings | No | `cognitive/producer.py:101-109` |
| **Gate result** | `GateResult` / `ReportDiff` dataclasses; `EvalReport` serialised to JSON | **No version field** on `EvalReport` | `eval/runner.py:90-111`, `eval/gate.py` |
| **Side effect (turn)** | Two opaque JSON blobs in SQL | **No** | `sqlite.py:36-44` |
| **Access log** | Structured `extra=` fields, JSON formatter available | **No** | `api/middleware/access_log.py:62-69` |

The strong part deserves saying plainly: `CognitiveSignal` is a genuinely
well-built evidence record — versioned, frozen, strict, temporally validated,
with an exported JSON Schema and an authority-key walker
(`authority.py:72-87`) that recursively rejects capability-shaped keys inside
nested payloads, including camelCase and hyphen variants (`:57-69`). The
`FORBIDDEN_AUTHORITY_KEYS` / `FORBIDDEN_SECRET_KEYS` sets are thoughtful. This
is the standard the other five record types should be held to.

The weak part: the record that describes **what actually happened to the
world** — the turn — is the least structured thing in the system. It has no
schema version, so no consumer can safely evolve with it; `_ensure_tenant_column`
(`sqlite.py:62-72`) is an ad-hoc migration in place of one.

`EvalReport` deserves a note: the team clearly *thought* about evolution —
`target_name` and `mean_cost_usd` default so old baseline JSON stays readable
(`runner.py:103-111`). That is version tolerance by convention. An explicit
`schema_version` would make it checkable.

---

## 7. Failure containment

**What happens when an agent loops, partially changes a repository, or fails
after an external side effect?**

**Verdict: loops are well contained. Partial change and post-side-effect
failure are not contained at all.**

### Looping — contained, and the best-engineered area in this audit

- `orchestrator.py:299-300` — acceptance loop exhaustion raises
  `MaxStepsExceeded` (→ 422).
- `:322-338` — a documented precedence chain for the step budget (kwarg >
  non-default request field > `MANGOMAS_LOOP__MAX_STEPS` > field default).
- `:341-357` — each step runs under `asyncio.timeout`, raising typed
  `StepTimeout` (→ 504). Stdlib structured concurrency, no hand-rolled timer.
- `agents/tool_agent.py:90` — an inner `max_tool_steps` bound, distinct from
  the outer dispatch loop.
- `workflow/graph.py:80-84` — the graph is acyclic by construction: a
  `sequence` cannot nest a `sequence`, so no cycle is representable.

Four independent bounds on non-termination. This is done properly.

### Partial change / post-side-effect failure — not contained

- **A failed run leaves no record.** `save_turn` is reached only at
  `orchestrator.py:317-318`, after the loop returns normally. Any exception —
  `MaxStepsExceeded`, `StepTimeout`, `ToolExecutionError`, an LLM error —
  propagates past it. So the durable evidence of a failed run is **nothing**.
  The system's only persistent log records successes.
- **Tool side effects are not transactional.**
  `agents/tool_agent.py:133-160` awaits `tool.execute(...)`, wrapping unexpected
  exceptions as `ToolExecutionError` and re-raising. If a tool has already
  mutated external state and a *later* step times out, the mutation stands and
  no turn is written. There is no compensation, no undo, no outbox — a grep
  for `outbox|saga|compensat|rollback|two-phase` across `src/` returns one hit,
  a comment in `rag/pipeline.py:265` using "ledger" figuratively.
- **The `Tool` protocol carries no safety metadata.**
  `core/tools.py:62-77` is `name` / `spec` / `execute(arguments) -> str`.
  Nothing declares whether a tool is read-only, idempotent, or destructive, so
  neither the agent nor a future broker can treat a write differently from a
  read.
- **Streaming is the documented exception, handled well.**
  `orchestrator.py:635-638, 670-676, 714` — a stream persists **only on full
  drain**; an abandoned or errored stream is explicitly not a turn
  (spec-0025). Deliberate, documented, and tested. Note the asymmetry: the
  streaming path reasons carefully about partial completion; the non-streaming
  path does not reason about failure at all.
- **RAG ingestion is the one place failure was engineered.**
  `rag/pipeline.py:1-29` and `:185-234` are worth reading as a model: every
  embedding call for a document completes *before* the first index mutation, so
  a provider outage fails the run with the index untouched. The docstring then
  **names its own residual window** (`:21-28`): store failure between
  `delete_by_source` and the following upserts can still lose a source's
  vectors, and closing it needs an id-targeted delete on the protocol. That is
  exactly the right way to document a known gap.

### Repository changes

V2 does not write to repositories — that is the sibling harness's job, and
`cognitive/roles.py:17-26` enforces the boundary well: `FORBIDDEN_HARNESS_ROLES`
blocks `implementer`/`destructive`/`write_file`/`shell`, unknown agents raise
rather than defaulting, and the `tool` agent raises outright
(`:38-52`). Fail-closed, and correct. The question's repository-mutation half
is therefore **out of scope for this repo by design** — but only for as long as
no tool with write capability is registered, and nothing in the `Tool` protocol
prevents one.

---

## 8. Idempotency

**Can a workflow safely resume without repeating writes or duplicating
external changes?**

**Verdict: no. There is no resume, and the idempotency primitive that exists
is never used.**

- **The primitive exists and is well designed.**
  `proposed_action.py:42` requires a 64-char lowercase-hex `idempotency_key`,
  validated at `:44-49`; `compute_idempotency_key` (`:59-81`) derives it as
  `sha256` over a canonical, sort-keyed JSON of
  `(run_id, task_id, kind, intent, artifact_refs, policy_version)` — stable,
  collision-resistant, policy-scoped. `ProposedAction` also rejects shell
  metacharacters in `intent` (`:51-56`).
- **Nothing uses it.** `grep -rn "ProposedAction|idempotency_key|compute_idempotency" src/ eval_harness_bridge/ scripts/` returns **zero hits**. Every
  occurrence is under `tests/mango_contracts/`. No `CognitiveSignal` this repo
  emits populates `recommendation.proposed_action_refs`
  (`cognitive/producer.py:227-242` passes no `recommendation`), so no action
  proposal is ever produced.
- **Turn writes duplicate freely.** `TurnRepository.save_turn`
  (`adapters/storage/base.py:20-30`) takes `(agent, request, response)` and
  returns a fresh autoincrement row id. No key, no upsert, no conflict clause
  (`sqlite.py:90-97` is a bare `INSERT`). Re-running identical work writes
  identical duplicate rows.
- **No resume path.** Following §1: with no persisted step state, "resume" has
  nothing to resume from. Re-running a workflow re-executes every node from
  the beginning, re-issuing every LLM call and re-executing every tool.
- **The HTTP sink has no dedupe.** `cognitive/sink.py:70-78` POSTs without an
  idempotency header; a retry at any layer delivers twice.
- **The one genuine idempotency win**, again, is RAG:
  `rag/pipeline.py:236-277`'s delete-then-upsert makes re-ingesting the same
  path converge rather than accumulate, and `_ensure_tenant_column`
  (`sqlite.py:62-72`) is idempotent by design. Both are local, both are
  correct, neither generalises to workflow execution.

---

## 9. Model selection policy

**Are model assignments centrally configured and reviewable?**

**Verdict: centrally *configured* — yes, and cleanly. Centrally *reviewable* —
no.**

Configured, and well factored:

- One default: `MANGOMAS_LLM__MODEL` → `LLMSettings.model`, consumed by exactly
  two factories (`composition/llm.py:25-71`). No model literal is hard-coded in
  any agent.
- Per-agent override: `MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE`, resolved in
  `build_agent_llm_overrides` (`composition/llm.py:74-108`) — one place,
  spec-0028 / ADR-0028. It inherits `base_url`, secrets and timeouts from the
  base config, skips blank or default-equal overrides (so the common case
  builds zero extra clients), and de-duplicates clients by model name.
- Teardown is handled: `_AgentLLMOverrideCloseMixin` (`:111-136`) extends
  `Orchestrator._close_hooks` so override clients close on the same
  fault-isolated path as `ctx.llm` — without editing the protected
  `core/orchestrator.py`. A neat use of the composition seam.

Not reviewable:

- **No allowlist.** `model_override` is an unconstrained `str | None`
  (`config/agents.py`); any value reaching `LLMSettings.model_copy(update=...)`
  (`composition/llm.py:98`) is built. Nothing declares which models are
  approved, and nothing rejects one that is not.
- **No manifest.** Model choice is env state, so the approved set for a given
  deployment exists only in that deployment's environment. `.env.example` is
  pinned by `tests/deploy/test_env_example_contract.py`, but that pins *names
  and defaults*, not an approved-model roster.
- **Not recorded on the evidence.** The `turns` row has no model column
  (`sqlite.py:36-44`); `AgentResponse` (`core/agent.py:39-44`) has no model
  field. `CognitiveSignal` carries `producer_id` / `producer_version` — the
  *agent* and *package* version, not the model. So no stored record answers
  "which model produced this output?"
- **Not bound to policy.** `policy_snapshot_hash` covers `policy_id:version`
  only (`config/signal.py:40-42`); swapping `MANGOMAS_LLM__MODEL` changes no
  hash and invalidates no prior approval.

The `mean_cost_usd` plumbing in `EvalReport` (`eval/runner.py:107-111`) and the
`cost_budget` scorer show cost is taken seriously per-run. Model *governance*
is the missing half.

---

## Remediation plan

Superseded. The plan that shipped with the first pass of this audit was a flat
P0–P3 list of one-line items — the wrong shape for work that spans four
reviewable units and has real ordering constraints between them. It has been
rewritten as a proper delivery plan, per this repo's own convention that
`specs/` holds the WHAT, `docs/adr/` the decisions, and `docs/plans/` the
sequencing:

> **`docs/plans/20260916T140000Z-governance-hardening-plan.md`**

Four PR blocks (gate integrity → the run record → replay and idempotency →
boundary honesty), eighteen milestones, each with the failing test that must go
red before it and green after, its honest dependency status, and the specs and
ADRs it needs written first. Nothing in it is implemented.

The one sequencing claim worth repeating here: **PR A comes first because every
other control is worth exactly what the gate protecting it is worth.** Sections
3, 4a and the second-pass findings N1–N3 below all describe the same failure —
a gate that reads its policy from the tree it is judging — and until that is
closed, hardening anything else is building on a foundation the builder can
move.

---

# Peer review — second pass

The first pass was written in one sweep and read the load-bearing paths. This
pass re-read it adversarially, asking two questions: **which verdicts are
wrong or overstated**, and **what did the sweep not look at?** It changed one
verdict's reasoning, corrected two claims, and found four issues the first pass
missed entirely — one of which is arguably the sharpest finding in the whole
audit.

## Corrections to the first pass

**C1 — "Requirement IDs dangle" was wrong.** The first draft's measurement
grepped only for `- **RN`, found 5 of 31 specs matching, and concluded that
code citing `spec-0026 R2` pointed at a spec with no R-numbers. It does not.
Specs use three heading forms — `### RN` (9 uses), `- **RN` (38), `- RN ` (29)
— and **all five requirement-level citations in `src/` resolve**:
`spec-0015 R4` → `specs/0015-package-decomposition.md:95`; `spec-0017 R7` →
`:67`; `spec-0022 R14` → `:60`; `spec-0026 R2` → `:59`; `spec-0029 R5` →
`:148`. The §2 verdict stays **Partial**, but for a different and weaker
reason: the citations resolve *by care, not by construction*. There is no
convention in `specs/TEMPLATE.md` and no link checker, so the next one is as
likely to dangle as not. That is a real gap; "they already dangle" was not.

**C2 — The `.claude/settings.json` deny-list claim was dropped, correctly.**
An earlier draft was going to argue that `"Edit(/.claude/settings.json)"` has a
leading-slash path pattern that may not match the repo-relative file. That
depends on Claude Code's pattern semantics, which are not verifiable from this
repository, and the entry may well be correct. It is not in the audit and
should not be — an unverifiable claim about a security control is worse than
no claim. The point that *does* hold, and is in §3c, needs no pattern
semantics: the rule matches `Edit`/`Write`, and `Bash` writes are not covered.

**C3 — §7's "repository changes are out of scope" needed its condition
stated.** It now carries one, but it is worth sharpening: the boundary holds
because no write-capable tool is registered today, and `RetrievalTool` is
read-only. Nothing in the `Tool` protocol (`core/tools.py:62-77`) keeps it that
way. The scope decision is sound; it is one tool registration away from not
being.

## N1 — `sitecustomize.py` can silently disarm the test and coverage gates

**Severity: high. The first pass did not look at this file at all.**

The repo ships a 15-line `sitecustomize.py` at its root. Python imports
`sitecustomize` automatically at interpreter startup when it is importable, and
this one mutates the environment of **every** Python process that loads it:

```python
# sitecustomize.py:12-15
if "-p no:randomly" not in os.environ.get("PYTEST_ADDOPTS", ""):
    existing = os.environ.get("PYTEST_ADDOPTS", "").strip()
    os.environ["PYTEST_ADDOPTS"] = f"-p no:randomly {existing}".strip()
```

`PYTEST_ADDOPTS` is applied by pytest to every run. It can carry
`--cov-fail-under=0`, `-k`, `--deselect`, or `-p no:cacheprovider`. So an edit
to this one file — which **is not a protected path**, is not referenced by
`make gate`, and reads as inert plumbing — can silently weaken the test and
coverage gates for every invocation in the repo, including CI's `make test`.

The guard that exists is one-sided:

```python
# tests/regression/test_origin_defects.py:35
assert "-p no:randomly" in result.stdout.strip()
```

That asserts the expected token is **present**. It does not assert that nothing
else was injected. Append `--cov-fail-under=0` to what `sitecustomize.py`
writes and this test still passes, `make test` still passes, and the coverage
gate is off.

This is precisely the fail-open defect class this repo has already hunted
twice. `scripts/check_coverage.py:37-42` names it in its own comment — "the
failure mode is **fail-open, not fail-closed**" — and specs 0020 and 0021 were
spent on it. The `mango-mutation-proof` skill exists to catch exactly this
shape. The skill's own method, applied to this test, fails it immediately.

*Verification note, stated because it bounds the claim:* in this audit
container the repo's `sitecustomize.py` does **not** execute — a system
`/usr/lib/python3.11/sitecustomize.py` shadows it, and a probe run from the
repo root showed `PYTEST_ADDOPTS` unset. So the finding is conditional on the
file being on the startup path, which `tests/regression/test_origin_defects.py`
asserts it is (by putting the repo root on `PYTHONPATH`). Either it executes —
and the gate-mutation surface is real — or it does not, and the pytest-randomly
protection it exists to provide is silently absent. **Both are findings**, and
nothing currently distinguishes them. (`pyproject.toml:307` also carries
`-p no:randomly` in `addopts`, so the protection is belt-and-braces; that
redundancy is good, and it also means a silently-inert `sitecustomize.py` would
never be noticed.)

## N2 — A third-party plugin can override the scorer the CI gate reads

**Severity: high.** `MANGOMAS_DISCOVERY_ENABLED=true` makes
`eval/discovery.py` call `ep.load()` — arbitrary import and module-level
execution — for every installed distribution declaring a `mangomas.eval.*`
entry point. That much is inherent to entry-point plugins. What is not
inherent is what happens on a name collision:

```python
# eval/discovery.py:93-100
if ep.name in existing:
    logger.info("Eval plugin overrides built-in %s %r", label, ep.name, ...)
registry.register(ep.name, factory)
```

Last-call-wins. A plugin named `exact_match` **replaces the built-in scorer**.
The same applies to `json_file` (which writes the regression baseline),
`cost_budget` (the cost gate), and every target and dataset source.

These registries are the inputs to the CI quality gate that exits 3
(`eval/gate.py`) and to the regression baseline diff. So an installed package
can decide whether the eval gate passes, and the only trace is an INFO log line.

The asymmetry is what makes this a finding rather than a design choice: the
**agent** discovery path already refuses this. `agents/discovery.py:73-76`
skips a built-in collision with a WARNING and does not load it. Two discovery
modules, written to the same shape, sharing
`_entry_points.load_entry_point_factory` — and the one that guards the gate is
the permissive one. Whichever policy is right, they should not disagree by
accident.

## N3 — The `PostToolUse` hook runs a model-chosen string through `sh -c`

**Severity: medium.** `.claude/settings.json`'s `PostToolUse` hook:

```
python scripts/lint_agent_frontmatter.py --hook post-tool-use --emit-path \
  | xargs -r -I{} sh -c 'python -m ruff check --fix "{}" …'
```

`--emit-path` prints `tool_input.file_path` verbatim.
`_extract_tool_path` (`scripts/lint_agent_frontmatter.py:554-568`) does one
`isinstance(value, str)` check and returns the raw string — no normalisation,
no metacharacter rejection. `xargs -I{}` substitutes it inside a double-quoted
word in a string handed to `sh -c`, so a path containing `"` closes the quote
and everything after it is shell.

The interpolated value is a path the **model** chose, and a model can be
steered by file content it has read. This is not the highest-severity item in
the audit — it needs a model already behaving adversarially — but it is the
only place in the repo where untrusted-ish text reaches a shell, and the fix is
two small changes (reject metacharacters in `--emit-path`; use `xargs -r -0`
instead of `sh -c`).

The irony is worth stating because it shows the standard the repo already
holds elsewhere: `ProposedAction.intent`
(`mango-integration-contracts/.../proposed_action.py:51-56`) **does** reject
shell metacharacters — for a string that is never executed by anything. The
contracts package is stricter about a description than the hook is about a
string it feeds to `sh -c`.

## N4 — `.mcp.json` loads six servers with no approval step in cloud sessions

**Severity: low-medium, and partly already handled well.** `CLAUDE.md` states
plainly that cloud/Agent-SDK sessions "load them with no approval step", unlike
an interactive terminal session's one-time workspace-trust prompt. The file
grants a `filesystem` server rooted at the project and a `git` server, among
others.

Credit where it is due, and it is real: **every server version is pinned** —
`@modelcontextprotocol/server-filesystem@2026.7.10`, `mcp-server-git@2026.7.10`,
`repomix@1.18.0`, `ghcr.io/github/github-mcp-server:v0.20.1`. That is better
supply-chain hygiene than most repos manage, and it blunts this finding
considerably.

What remains: `npx -y` and `uvx` resolve those pinned versions from a public
registry at run time with no integrity hash, and `.mcp.json` is **not a
protected path** — so the set of servers a cloud session auto-loads can be
changed without a `BREAKING-CHANGE` marker. Protecting the file is the
actionable half (plan milestone A0); hash pinning needs upstream support and is
not actionable here.

## What the second pass confirmed rather than changed

Re-checked and unchanged:

- **Postgres/SQLite tenancy parity is genuine.** `postgres.py:259-322` reads
  `get_tenant()` inside `save_turn`/`list_turns` and scopes both statements,
  exactly as `sqlite.py:82-131` does. §4b's criticism is of the trust boundary,
  not the implementation — the storage-layer isolation is correctly built on
  both backends.
- **The coverage gate is the strongest gate in the repo**, and is the model PR
  A should copy. `tests/test_check_coverage.py` proves its own gate
  non-vacuous: every top-level source path has a floor
  (`test_every_top_level_source_path_has_a_floor`), directory floors must use
  a recursive glob (`test_directory_floors_use_a_recursive_glob`), no exclude
  pattern may swallow real code (`test_no_exclude_pattern_swallows_real_code`),
  and the doc table must match the script
  (`test_regression_doc_floor_table_matches_the_script`). Fifteen tests, and
  they check the *denominator*, not just the number. Nothing equivalent exists
  for the protected-path gate — which is the whole of §3.
- **The fallback-drift pins are correct and worth keeping**
  (`tests/harness/test_governance.py:179-206`). §3a's "drift detector, not an
  authority boundary" stands, and is not a criticism of the tests.

## Revised severity, after the second pass

Only §3 moves, and only upward. The first pass called policy authority
critical on one finding: the policy table is self-amendable. There are now
**four** independent paths by which the governed tree can weaken its own gates
— `pyproject.toml` (§3a), `sitecustomize.py` (N1), eval plugin override (N2),
and `.mcp.json` (N4) — of which only the first was known. Three of the four are
closed by one change: putting the file in `protected_paths`. That is milestone
A0, it is roughly a ten-line diff, and it is the highest ratio of governance
value to blast radius anywhere in this audit.

---

## What this codebase already does well

Stated plainly, because the verdict table is unkind and the engineering is not:

- **Loop containment** (§7) is genuinely well done — four independent bounds,
  typed errors, stdlib primitives, no hand-rolled timers.
- **`CognitiveSignal`** is a strong evidence record: versioned, frozen, strict,
  temporally validated, JSON-Schema-exported, with a recursive authority-key
  walker that handles camelCase and separator variants.
- **INV-16 role mapping** (`cognitive/roles.py`) is properly fail-closed:
  unknown agents raise rather than defaulting, and execution roles are refused
  outright.
- **RAG ingestion** (`rag/pipeline.py`) is the best failure-mode reasoning in
  the repo — and it names its own residual window instead of hiding it.
- **ADR-0021** is honest that the `PreToolUse` hook is advisory and *why*
  (`Bash`/MCP bypass the matcher). That candour is the reason this audit could
  be written from the source at all.
- **The fallback-drift pins** (`tests/harness/test_governance.py:179-206`) show
  someone already thought about governance data drifting silently.

The recurring pattern in the findings is not carelessness. It is **controls
designed at the type level and not yet wired at the system level** —
`expires_at` with no consumer, `idempotency_key` with no producer,
`policy_snapshot_hash` with no policy document, `protected_paths` with no
external authority. The remediation plan is mostly wiring, and the P0 items are
the ones that cannot wait.
