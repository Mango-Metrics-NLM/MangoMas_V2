# Next Steps & Roadmap

This document tracks planned improvements to Mango-Mas V2.
Near-term items are sequenced; mid- and long-term items are ordered by
dependency, not priority.  All items follow the same design rules as the
existing codebase: env-driven configuration, registry/protocol-based
extension, backwards-compatible contracts.

---

## Near term

_(All cloud adapters (Vertex AI, Postgres, GCP Secret Manager) and the
offline evaluation harness landed in v0.3.0. The Claude Code enterprise
harness landed in Unreleased. The four near-term items below all landed
in the Unreleased Milestone A–E series — see `CHANGELOG.md` and
`specs/0001`–`0004`. Remaining forward work is under "Long term".)_

### ✅ Harness metrics-exporter selection — done (Milestone C)

`MANGOMAS_HARNESS__METRICS_EXPORTER` (`inherit`/`console`/`gcp`) routes
`harness.agent_invoke` spans via `build_scoped_tracer`; default `inherit`
reuses the application exporter. See ADR-0009, spec 0002.

### ✅ Cloud Logging + Cloud Trace exporter swap — done (Milestone C)

`MANGOMAS_TELEMETRY__EXPORTER=gcp` selects the Cloud Trace exporter behind
`configure_telemetry()` (lazy, under the `gcp` extra). See ADR-0009, spec 0001.

### ✅ Cloud Run deployment pipeline — done (Milestone E)

`deploy/` ships the Cloud Run manifest, the WIF-authenticated
`.github/workflows/deploy.yml`, and the `MANGOMAS_*` env contract. Author-only;
no GCP resources are provisioned here. See ADR-0001, spec 0004.

### ✅ SecretsSettings.strict mode — done (Milestone D)

`MANGOMAS_SECRETS__STRICT` makes cloud backends raise `SecretsResolutionError`
(HTTP 503) instead of returning `None` on auth/permission/timeout failures;
default preserves ADR-002. See ADR-0010, spec 0003.

---

## Done on the governance-hardening branch (Unreleased)

Specs 0022 + 0023. The theme is the same throughout: this repo had a large
number of **claims nothing checked** — a documented default, a prose count, an
architecture diagram, a secret scan's own effectiveness — and each one had
quietly drifted from the thing it described. Every item below pairs the fix
with the mechanism that keeps it fixed.

### Governance and CI

- Two-pass `gitleaks` (`dir` + `git`) replacing the history-only `detect`, so
  an uncommitted `.env` can no longer pass.
- `.gitleaks.toml` — the ruleset is declared rather than inherited, plus a rule
  for a password inside `MANGOMAS_DB__URL`, which **no built-in rule detects**
  (probed, not assumed). `make gitleaks-selftest` plants a secret of each
  covered class and requires the scan to still fail; it runs nightly.
- Deploy-job interpolation moved behind `env:`; third-party actions SHA-pinned
  with `.github/dependabot.yml` keeping the pins fresh.
- `nightly.yml` — the repo had no scheduled automation at all. Runs the
  Postgres suite and both secret-scan passes, and files a deduped tracking
  issue on failure, because a cron run otherwise reports to nobody.
- MCP write/mutation tools denied at the permission layer; an advisory Bash
  protected-path check (always `ask`, never `deny`).

### Guards that were weaker than what they guarded

- The workflow injection guard was bypassable by deleting one space.
- `scripts/check_coverage.py` — the gate itself — sat at 24% coverage.
- `sanitize_header_token`, the shared log-injection defence, had only
  example-based tests; it now has properties over arbitrary text.
- The chunker fuzz pinned `min_words=0`, hiding that
  `MANGOMAS_RAG__MIN_CHUNK_WORDS` is provably inert (see below).
- A zero-skip/xfail session guard, with a subprocess meta-test proving it
  fires — including on collection-level `importorskip`.

### Claims that had drifted from reality

- CLAUDE.md documented config **defaults** that nothing compared against the
  live `Settings` fields.
- No CLI run had ever honoured `MANGOMAS_LOG__FORMAT` or
  `MANGOMAS_TELEMETRY__EXPORTER`: four modules bound a self-bootstrapping
  tracer at import time, latching telemetry at defaults before `main()` ran.
- The README claimed 13 skills and 23 agents against a tree holding 15 and 27.
- The C4 model omitted `tenancy.py`, the eval registries, the harness
  governance package and the MeterProvider, and still called Cloud Run and
  Cloud Trace "planned" a release after both landed.
- `docs/testing/regression.md`'s floor table listed 14 of the 20 floors the
  gate enforces.
- The FastAPI assembly layer had no write-capable owning agent
  (`mango-api-impl-dev` now owns it), and ownership is derived from the agent
  bodies rather than a parallel table.

### Still open from this branch

- **`MANGOMAS_RAG__MIN_CHUNK_WORDS` is inert.** The guard that would drop a
  short trailing fragment is unreachable: the loop only steps again when the
  previous window was not last, so the final window always extends past it.
  Verified exhaustively (`n < 40` × `size < 15` × every overlap: zero reachable
  states). Dropping the fragment would lose words, so the *code* is right and
  the documentation overclaims. Resolving it — accept the word loss, or retire
  the knob — is a retrieval-quality decision, deliberately not taken here.
- **`registry.py` has no owning agent.** A generic `Registry[T]` on a protected
  path, consumed equally by five registries; naming any one owner would be
  arbitrary. Recorded in `UNOWNED_SOURCE_SURFACES` rather than assigned.

---

## Done on the harness branch (Unreleased)

### Claude Code enterprise harness

End-to-end Claude Code harness landed as a non-breaking opt-in layer.
Counts below are as-of the harness branch; the suite has since grown —
see the skill and agent tables in `CLAUDE.md` for the current set.

- **8 skills** under `.claude/skills/<name>/SKILL.md` covering
  testing, adapter authoring, agent addition, error taxonomy,
  observability, config, release, and topology. _(Since grown, and
  relocated to `.claude/` by spec-0018 — see the skill table in
  `CLAUDE.md`, which is the pinned source of truth for the current set.)_
- **19 agents** under `.claude/agents/mango-<slug>.md` (4 routers + 15 specialists)
  grouped under the 4 parent agents (including `pr-watcher` under
  `architect`). _(Since superseded: the parent/child hierarchy and its
  `sub_agents:` key are gone — see the agent tables in
  `CLAUDE.md`.)_
- **`HarnessSettings`** (env prefix `MANGOMAS_HARNESS__`,
  `enabled=False` default) drives whether `build_orchestrator`
  returns a `_HarnessOrchestrator` wrapper that adds a
  `harness.agent_invoke` parent span over `dispatch` *and*
  `stream_dispatch`. Pipeline + fan-out topologies inherit the wrap.
- **`scripts/lint_agent_frontmatter.py`** enforces frontmatter
  schemas (Pydantic), enforces the Claude Code agent format, and gates
  protected core paths via a `BREAKING-CHANGE` marker. Wired into
  CI as `frontmatter-lint`.
- **`scripts/harness_session_start.py`** emits a single-line JSON
  probe report on session start (venv + LM Studio reachability).
  Always returns `EXIT_OK` so a failed probe never blocks a session.
- **PR template** + **secret-scan job** + **ADR template** complete
  the PR automation.
- Composition coverage rises 90 % → 100 % via six new tests covering
  every harness branch (dispatch wrap, stream wrap, file-memory
  factory, memory-enabled wiring, linter EXIT_SCHEMA, git-failure
  branch in `_staged_diff`). Global coverage 96.95 % across 505
  unit tests.

See `.claude/agents/`, `.claude/skills/`, and the new `harness:`
block in `Settings`. C4 diagrams in `docs/architecture/` (c2 + c3)
describe where the harness sits.

---

## Done in v0.3.0

### Vertex AI LLM provider

`VertexClient` in `src/mangomas/adapters/llm/vertex.py` satisfies
`LLMClient`, `PingableLLMClient`, and `StreamingLLMClient` via
`vertexai.generative_models`. Registered through the existing
`llm_registry`; activate with `MANGOMAS_LLM__PROVIDER=vertex` and the
`vertex` optional extra (`pip install 'mangomas[vertex]'`). New
`LLMSettings` fields: `project_id`, `location`, `credentials_path`. The
existing `secret_ref` flow is reused — the resolved value becomes the
service-account JSON body. See `docs/adapters/vertex.md` and
`docs/testing/vertex-e2e.md`.

### Cloud SQL / Postgres storage provider

`PostgresRepository` (`src/mangomas/adapters/storage/postgres.py`)
satisfies `TurnRepository` and the new `AsyncCloseableRepository`
extension protocol. Backed by `asyncpg` with a connection pool — no
`threading.Lock` (native async). Registered as
`_storage_registry.register("postgres", _postgres_factory)`. Activate
via `MANGOMAS_DB__PROVIDER=postgres` plus
`MANGOMAS_DB__URL=postgresql://...` and the `postgres` optional extra
(`pip install 'mangomas[postgres]'`). A JSONB codec is registered on
every connection so `list_turns` returns dicts (matching SQLite's
row shape). testcontainers-backed integration suite under
`tests/postgres/` gated by `RUN_POSTGRES=1`. See
`docs/testing/postgres-integration.md`.

### Cloud Secret Manager provider

`GCPSecretManagerProvider` (`src/mangomas/secrets/gcp.py`) satisfies
`SecretsProvider`. Lazily registered in `secrets_registry` at
orchestrator-build time. Activate via `MANGOMAS_SECRETS__PROVIDER=gcp`
plus `MANGOMAS_SECRETS__PROJECT_ID=...` and the `gcp` optional extra
(`pip install 'mangomas[gcp]'`). Collapses all failure modes into
`None` per [ADR-002](docs/adr/0002-secrets-provider-error-semantics.md);
operators MUST alert on `logger=mangomas.secrets.gcp severity=ERROR`.

### Evaluation harness

`src/mangomas/eval/` ships the `Scorer` protocol, `scorer_registry`, a
JSONL dataset loader, `EvalRunner` (reuses the existing
`Orchestrator`), `EvalReport`, three built-in scorers (`exact_match`,
`llm_judge`, `embedding`), an `EvalSettings` block (`MANGOMAS_EVAL__*`),
and a `mangomas eval` CLI subcommand. The embedding scorer raises
`NotImplementedError` against providers that don't expose `.embed()`
(see "Embedding-capable LLM provider" under "Long term"). See
`docs/eval/harness.md`.

### Concurrency regression tests

`tests/test_sqlite_concurrency.py` closes the gap from v0.1.0's
`threading.Lock` fix with a direct 50-way `asyncio.gather` regression.
`tests/postgres/test_concurrency.py` pins the asyncpg pool's
concurrent-write contract symmetrically.

### Coverage / hygiene tightening

Per-package floors in `scripts/check_coverage.py` raised to match the
post-v0.3.0 actuals: `composition` / `api` / `cli` / `global` all
90 → 95. New `eval` floor at 95 %. `pyproject.toml`
`--cov-fail-under=90` → `95`. The backwards-compat
`mangomas.api.correlation` re-export shim was removed; imports must use
the canonical `mangomas.correlation` path. The `DEFAULT_ERROR_DETAIL_TRUNCATE`
constant replaces inline `[:200]` literals across the cloud adapters.

See [`docs/architecture/cloud-providers.md`](docs/architecture/cloud-providers.md)
for the full configuration matrix and the lazy-SDK-import /
ambient-identity pattern shared across all three cloud adapters.

---

## Done in v0.2.0

### LM Studio end-to-end scenarios (scenarios 2–6)

All five follow-up scenarios from the
[LM Studio E2E Scenario Plan](docs/testing/lmstudio-e2e.md) ship under
`tests/lmstudio/`, gated on `RUN_LMSTUDIO=1`:

- [x] **Chat happy path** — `tests/lmstudio/test_chat_invoke.py`
- [x] **Streaming happy path** — `tests/lmstudio/test_chat_stream.py`
- [x] **Streaming fallback warning** — `tests/lmstudio/test_stream_fallback.py`
      (uses `Registry.scoped()` to swap in a `NonStreamingLMStudioClient`)
- [x] **Summarize agent** — `tests/lmstudio/test_summarize_invoke.py`
- [x] **Error path — unavailable model** — `tests/lmstudio/test_unknown_model.py`

Shared fixtures (`lmstudio_base_url`, `lmstudio_model`,
`lmstudio_orchestrator`, `lmstudio_app`) live in `tests/lmstudio/conftest.py`.

### Planner / Reviewer streaming support

Both `PlannerAgent` and `ReviewerAgent` now satisfy `StreamingAgent` via an
async `stream()` method that yields LLM tokens incrementally and falls back
to a single buffered chunk + warning log when the configured client does
not implement `StreamingLLMClient`.

### Secrets-manager seam

`src/mangomas/secrets/` ships the `SecretsProvider` protocol,
`EnvSecretsProvider` env-var backend, and `secrets_registry`.
`LLMSettings.secret_ref` is resolved at orchestrator-build time. The
GCP Secret Manager backend landed in v0.3.0.

### Per-request correlation IDs

`src/mangomas/correlation.py` exposes a `ContextVar` + `CorrelationFilter`;
`AccessLogMiddleware` reads inbound `X-Request-ID`, sets the ContextVar,
pushes the value into OpenTelemetry baggage as `mangomas.correlation_id`,
and echoes it on the outgoing response.

---

## Done on the development-next-steps branch (Unreleased)

HTTP-surface parity + production-hardening substrates + a workflow branch node,
all additive and default-OFF (no protected-path edits). See `CHANGELOG.md`
`[Unreleased]`, specs `0008`–`0012`, and ADRs `0012`–`0016`.

- **Workflow HTTP endpoint** — `POST /workflows/run|validate`, gated exactly like
  the CLI, reusing the shared `resolve_workflow_source` + `execute_workflow`.
- **`GET /history`** — HTTP twin of `mangomas history`, with an env-bounded
  `limit` (`MANGOMAS_API__HISTORY_*`).
- **Opt-in HTTP hardening** — env-driven CORS (methods/headers/credentials, all
  default-safe), an auth seam (bearer / API-key via `SecretsProvider`,
  fail-closed), and backpressure (413 body-size + 503 at-capacity).
- **OTel metrics** — a `MeterProvider` behind the exporter seam
  (`MANGOMAS_TELEMETRY__METRICS_ENABLED`), with agent invocation / error /
  duration instruments emitted at the HTTP boundary.
- **Workflow `branch` node** — predicate-routed conditional selection.

## Done on the RAG branch (Unreleased)

### Retrieval-augmented generation

The full RAG port landed as a non-breaking opt-in layer
(`embeddings.enabled` / `vector.enabled` default `False`):

- **`EmbeddingClient` seam** (`adapters/embeddings/`) with three backends:
  `LMStudioEmbeddingClient`, `SentenceTransformersEmbeddingClient`
  (`embeddings-local` extra), and `VertexEmbeddingClient`
  (`text-embedding-004`, ADC-only, `vertex` extra). This closes the
  long-term "embedding-capable provider" gap — the `EmbeddingScorer` is
  now operational against a real provider via `ScorerContext.embeddings`.
- **`VectorStoreRepository` seam** (`adapters/vector/`) with
  `ChromaVectorStore` (`rag` extra). Cosine space + `1 - distance/2`
  similarity keeps scores in `[0, 1]`.
- **`rag/` package** — word-window chunker, document loader,
  `IngestionPipeline` (idempotent re-ingest via `delete_by_source`), and
  `Retriever` + `RetrievalTool` (auto-discovered by `ToolAgent`).
- **CLI** — `mangomas rag ingest` / `mangomas rag query`.
- **Shared adapter error helpers** (`adapters/_http_errors.py`,
  `adapters/_vertex_errors.py`) de-duplicate the httpx + Vertex error
  translation across the chat and embedding adapters.
- New `rag` 95 % coverage floor; all floors met.

See the `mango-rag` skill (`.claude/skills/mango-rag/SKILL.md`) and the
C4 diagrams in `docs/architecture/`.

---

## Done on the code-hygiene branch (Unreleased)

A hygiene pass with no public-contract change. Extends the shared-helper
precedent set by `adapters/_http_errors.py` to the four remaining duplication
seams, and reconciles config/doc drift.

- **Four shared helpers** — `adapters/_openai_client.py`
  (`OpenAICompatHTTPClient`: httpx lifecycle for both LM Studio adapters,
  keyword-only past `model`, `_LABEL`/`_BAD_RESPONSE` enforced by
  `__init_subclass__`), `adapters/embeddings/_shared.py` (`embed`/`aclose`
  mixins — a new backend implements only `embed_batch`),
  `eval/_serialize.py` (`report_payload` shared by the `json_file`/`webhook`
  sinks and round-tripped by `load_baseline`), and
  `workflow/nodes/_factory.py` (`make_node_factory`, replacing five
  hand-written factories and their `# pragma: no cover` waivers).
- **Docker build fix** — `.dockerignore` excluded `README.md`, which the
  Dockerfile copies and the wheel build requires; the Cloud Run image build
  was broken. Guarded by `tests/deploy/test_docker_build_context.py`, which
  parses both manifests at runtime.
- **`Makefile`** — every CI command as a target; `make gate` runs the full
  pipeline locally.
- **Coverage-floor truth** — `scripts/check_coverage.py` is now named as the
  single source everywhere; CI dropped its weaker `--cov-fail-under=90`
  override; docs stopped restating test counts that went stale each release.
- **Tooling lockstep** — `ruff==0.16.0` and `mypy==2.3.0` exact-pinned in the
  `dev` extra in lockstep with the pre-commit revs (both hooks were years
  behind CI); `PLR0917` enabled with two narrow per-file ignores.
- **Docs drift** — corrected `MANGOMAS_LLM__PROJECT_ID`, removed a documented
  setting that never existed, fixed the `VertexClient` name and a moved
  `correlation.py` path, and added the missing `workflow/` package to the C2
  and C3 diagrams.

### Spec-0014 follow-on: defects, further dedup, and the `api/` split

A second hygiene pass (spec-0014 / ADR-0019, PR #24) found twelve verified
defects and a further round of duplication clusters via a full-repo audit:

- **Twelve defects fixed** (D1–D12), each with a dedicated regression test —
  a silently-dropped tool prompt, untruncated upstream error bodies, eval
  config errors surfacing as per-row failures instead of an exit-2 config
  error, a broken `make rag` target, an httpx-pool leak in
  `run_workflow_e2e.py`, an unlocked metrics-singleton race,
  `IngestReport.deleted_sources` undercounting, `FileMemoryRepository`
  ignoring its own closed state, a workflow span not covering its node's
  final return, a `sqlite:///` URL sink resolving to the wrong path,
  `eval.discovery` configuring telemetry as an import side effect, and
  `eval.gate.merge_gate_results` reading the wrong verdict positionally.
- **Five more shared helpers** — `agents/_prompt.py` (`resolve_system_prompt`
  + `build_messages`, collapsing five agents' prompt-precedence logic),
  `agents/_structured.py` (`StructuredOutputAgent`, the planner/reviewer
  base), `eval/_langfuse.py` + `eval/_options.py` (Langfuse bootstrap +
  option validation, shared by the sink/source/target factories),
  `mangomas/_entry_points.py` (shared entry-point iteration for eval plugin
  discovery), and `OpenAICompatHTTPClient._request`/`_log_and_translate`
  (collapsing the POST/GET → raise → log → translate sequence repeated
  across LM Studio's chat, streaming, embedding, and ping call sites).
  `secrets.gcp.GCPSecretManagerProvider.get()`'s five failure branches
  collapse into one `_handle_failure()` helper.
- **`AgentSettings.temperature`/`max_tokens` activated** — previously dead
  config fields now forwarded to `LLMClient.complete`/`stream` via an
  additive keyword-only `max_tokens` parameter; `model_override` stays
  explicitly reserved (needs a composition-layer change, deferred).
- **`api/app.py` decomposed** into `api/errors.py`, `api/models.py`, and
  `api/routes/{system,agents,workflows}.py` (ADR-0019's first proof point) —
  `create_app` is now a slim assembly factory and the repo's last ruff C901
  violation is gone; the HTTP surface is byte-identical (OpenAPI diffed).
- **CI/Makefile parity locked** — `ci.yml`'s lint/test/bridge-coverage jobs
  invoke `make` targets instead of duplicating commands, pinned by
  `tests/deploy/test_ci_make_parity.py`.
- **Deferred to `specs/0015-package-decomposition.md`, and now mostly landed** —
  `config.py` (593 lines, the repo's #1 churn file) → `config/`,
  `telemetry.py` → `telemetry/`, and `cli/main.py` → `cli/commands/` all ship
  behind permanent re-export facades (spec-0015 R1–R3). These were in
  spec-0014's original scope but were descoped mid-execution to keep PR #24
  mergeable rather than open-ended. **Still outstanding: R4** — the
  `core/structured.py` extraction from `core/tools.py` + `errors.py`. Both are
  protected paths, so it needs a `BREAKING-CHANGE` trailer and a real
  backwards-compatibility audit rather than a mechanical split; it is also
  entangled with who owns `harness/governance.py`, which defines
  `PROTECTED_PATHS`.

### Follow-ups this branch deliberately did not take

- **`.env.example` still documents `MANGOMAS_LLM__PROJECT` and
  `MANGOMAS_LLM__MAX_OUTPUT_TOKENS`** in a duplicated Vertex block (roughly
  lines 25–33), and its eval-var block names settings that do not exist. The
  file is read-protected in the authoring environment, so it needs a manual
  edit — this is the last live instance of that drift.
- **`main` / `feat/initial-release` reconciliation — partially done.** The two
  branches genuinely diverged: `main` carried a harness-hardening layer
  (`src/mangomas/harness/`, `scripts/harness_stop_gate.py`,
  `scripts/harness_config_audit.py`) that this line lacked, and each branch
  implements `workflow/` differently (DAG + `WorkflowRunner` on `main`;
  bounded tree + node registry here). ✅ **ADR collision resolved**:
  `docs/adr/0021-protected-path-governance-contract.md` supersedes `main`'s
  ADR-0011 (harness-hook-hardening); `docs/adr/0023-workflow-implementation-reconciliation.md`
  supersedes `main`'s ADR-0007 (declarative workflows) and records that this
  line's bounded-tree implementation is kept — see that ADR for the reasoning
  (`main`'s `WorkflowRunner` is a runtime scheduler that would violate
  ADR-0011's acyclic-by-construction invariant; its level-synchronised
  execution also isn't real per-edge DAG semantics). ✅ **Governance layer
  ported**: `src/mangomas/harness/{governance,config_audit}.py` and
  `scripts/harness_config_audit.py` (the `ConfigChange` hook) landed —
  `governance.py` is adapted to read `pyproject.toml`'s
  `[tool.mangomas.governance]` table rather than hardcoding the protected-path
  set. **Deliberately not ported**: `main`'s `coverage.py` and
  `harness_stop_gate.py` — `read_coverage_floor` parses
  `--cov-fail-under` out of `pyproject.toml` and feeds it back to a pytest run
  whose addopts already set that value, a tautology on this branch where
  `scripts/check_coverage.py` is already the documented single source of
  truth. **Still outstanding**: the `dag` node kind itself (ADR-0023 records
  the design — compile to a `Sequence`/`FanOut` tree at load time, absorbing
  only `main`'s `execution_levels()` algorithm — but does not implement it).
- **Deferred tooling** — ruff `ASYNC`/`DTZ`/`C4`/`RET`/`PERF`/`C90` rule
  families, a `pip-audit` job, a Python 3.13 matrix leg, a
  `verify` job gating `deploy.yml`, and a `pre-commit run --all-files` CI job.
  (`dependabot.yml` and scheduled automation are no longer on this list — both
  landed on the governance-hardening branch.)
- **Protected-paths CI job is not yet a required status check.** Spec-0017 R1
  intends the `protected-paths` job to be a merge-blocking required status
  check, but GitHub branch-protection settings are a repo-admin action under
  Settings → Branches, not something any file in this repo can express or a
  session can configure. Until an admin adds `protected-paths` (and the other
  gate jobs) to the required-checks list, a PR that edits a protected core
  contract without a `BREAKING-CHANGE` trailer will show the check red but is
  not actually blocked from merging.

---

## Long term

_(The first long-term capability — the evaluation harness — landed in
v0.3.0; the embedding-capable provider that unblocked its `EmbeddingScorer`
landed on the RAG branch above. Follow-ups below.)_

### ✅ Multi-agent workflows — done

Composition of multiple agents (e.g. planner → executor → reviewer) through a
declarative graph definition consumed by `Orchestrator`. The opt-in `workflow/`
package compiles a frozen `WorkflowGraph` (JSON: `sequence` of `agent` /
`fan_out` / `loop`) down to the existing `dispatch_*` primitives; default-OFF, so
single-agent dispatch is unchanged. Enable via `MANGOMAS_WORKFLOW__ENABLED` +
`__DEFINITION`; drive with `mangomas workflow run|validate` or over HTTP
(`POST /workflows/run|validate`). See ADR-0011, spec 0005. ✅ **Conditional
branching** landed as the `branch` node (spec 0012 / ADR-0016). ✅ **Composite
`fan_out` branches** landed (spec 0013 / ADR-0018): a `fan_out` branch may now be
any `WorkflowStep` (nested `fan_out` / `loop` / `branch`), with an all-`agent`
fan_out preserving byte-identical `dispatch_fan_out` parity. Remaining
follow-ups: composite `loop` bodies (correction: there is no `dispatch_loop`
method — the loop lives inside `Orchestrator.dispatch` via `acceptance_fn`/
`max_steps`, and `LoopNodeExecutor` already just calls `orch.dispatch(...)`,
so per the ADR-0018 `fan_out` precedent this needs **no protected-path edit**
at all; the real blocker is that `WorkflowStep` excludes `SequenceNode`, so a
composite loop body can't loop a sub-pipeline yet), and entry-point discovery
of third-party node kinds (needs an additive `PluginNode` union member, since
`WorkflowNode` is a closed `extra="forbid"` discriminated union — an unknown
`kind` fails validation before any registry is consulted).

### Multi-tenancy

✅ **Phase 1 (storage isolation) — done** (spec 0007 / ADR-0017): tenant-scoped
conversation storage via a `tenant_id` `ContextVar` + a `tenant` column /
`WHERE tenant = ?` row filter in both SQLite and Postgres, set by
`TenancyMiddleware` from `X-Tenant-ID`. Opt-in (`MANGOMAS_TENANCY__ENABLED`),
no `TurnRepository` signature change. **Phase 2 (deferred):** per-tenant
`AgentSettings` resolved at dispatch (needs a dispatch-time resolution decision).

### ✅ Agent marketplace / dynamic loading — done (Milestone B)

`agent_registry` now loads from external packages via entry-point discovery
(group `mangomas.agents`) gated by `MANGOMAS_DISCOVERY_ENABLED`, so third-party
agents install and register without modifying `composition.py`. A discovered
name colliding with a built-in is skipped with a warning. See ADR-0008,
spec 0006.

---

## Deferred / out of scope for this branch

- GCP resource provisioning scripts (no cloud resources created by this repo).
- Model fine-tuning or RLHF pipelines.
- UI / chat interface (out of scope; existing `cli/` covers local interaction).
