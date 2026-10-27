# Spec-0037: Agent2Agent (A2A) interoperability as an opt-in boundary adapter

- **Status:** Draft — design recorded; implementation deferred (see "Go / no-go gate")
- **Linked ADR:** ADR-0037
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — A2A interoperability decision record (ADR-0037)`

## Problem

Every Mango-Mas agent is in-process, and the only way to reach one is the
bespoke `POST /agents/{name}/invoke|stream` surface with its own SSE framing.
Nothing outside this service can discover an agent or delegate a task to it,
and no pipeline here can delegate a hop to an agent that lives elsewhere. The
Agent2Agent protocol (A2A, Linux Foundation / AAIF, spec v1.0.1, Python SDK
`a2a-sdk` 1.2.x) is the agent↔agent complement of MCP, which this repository
already uses for tooling. Two earlier decisions deferred A2A
(`docs/analysis/20260912-council-peer-review-rewrite.md`, "[Reject] A2A this
quarter"; `docs/plans/20260916T214636Z-reliability-evidence-plan.md`, "revisit
after C4"). This spec records *where* and *how* A2A fits so that, when a
consumer appears, the work starts from a reviewed design rather than a blank
page. It does not override the deferral.

## Requirements

- R1 — A2A is a **boundary adapter**, never a core change: no edit to
  `core/agent.py`, `core/orchestrator/`, `core/tools.py`. A2A `Part`s are
  projected onto the string-only `Message`/`AgentRequest`/`AgentResponse` at
  the edge.
- R2 — **Server** (Phase 1): publish an Agent Card at
  `/.well-known/agent-card.json` and a JSON-RPC 2.0 endpoint for
  `SendMessage`, `SendStreamingMessage`, `GetTask`, `SubscribeToTask`,
  `CancelTask`, backed by the
  existing `Orchestrator.dispatch` / `stream_dispatch`. Skill `id` is the
  registry agent name (operator-controlled, stable); the card advertises only
  controls the code enforces (ADR-0033).
- R3 — **Client** (Phase 2): a `RemoteA2AAgent` satisfying `Agent` +
  `StreamingAgent`, registered through `orch.register()` under an
  **operator-chosen local name bound to a configured URL**, so every topology
  (`dispatch_pipeline`, `dispatch_fan_out`, workflow `agent` nodes, eval
  targets) reaches a remote peer with zero core edits. The remote card's
  `name` is presentation metadata only (arXiv 2609.27624).
- R4 — Same auth and tenancy as the rest of the API: the JSON-RPC route sits
  behind `require_auth` and inside `TenancyMiddleware`; the card route is
  public by A2A convention. Because `require_auth` is a single shared secret
  with no principal, v1 task/context ownership is `(tenant, "shared")` and
  documented as such; per-principal auth (ADR-0014 revisit) is a prerequisite
  for multi-party deployments (arXiv 2609.10871, finding 3). Mechanism: the
  SDK's `add_a2a_routes_to_fastapi` is **not** used (its routes cannot carry a
  `Depends`); a Mango-Mas `APIRouter` declares `dependencies=[Depends(require_auth)]`
  and forwards the JSON-RPC body to the SDK's request handler, with a custom
  `ServerCallContextBuilder` carrying the resolved tenant — so the 401 envelope
  and `TenancyMiddleware` apply exactly as on every other guarded route.
- R5 — v1 **excludes** push notifications (`pushNotifications=false`; removes
  the webhook-SSRF class, spec §13.2), `input-required` / `auth-required`
  (agents are single-shot; removes the unbounded-interrupted-state class),
  gRPC and the extended card. Each returns only behind its own spec.
- R6 — Inbound A2A text is **untrusted user content**: it enters as
  `role="user"` or as delimited data with provenance, never as `system`
  (ties to the trust-boundary work the reliability plan reserves as
  spec-0036).
- R7 — Observability: `X-Request-ID` and W3C `traceparent` are propagated on
  every outbound call and mirrored into `message.metadata` inbound; A2A has
  no super-task id of its own.
- R8 — Remote hops are measurable: an eval target (`remote_agent`) registered
  in `target_registry`, so "does the remote hop beat `chat`" is answered by
  the same machinery as C4.
- Must remain **additive & default-OFF**: `MANGOMAS_A2A__ENABLED=false` keeps
  the route table, the OpenAPI snapshot and `import mangomas` byte-identical
  (no `a2a` import on the disabled path).

## Scenarios (WHEN/THEN)

- WHEN `MANGOMAS_A2A__ENABLED` is unset THEN `app.openapi()` equals today's
  snapshot and `sys.modules` has no `a2a` entry after `import mangomas`.
- WHEN enabled without `MANGOMAS_A2A__PUBLIC_URL` THEN `Settings` validation
  fails at parse, the same shape as `ALLOWED_MODELS` (ADR-0033); the URL
  cannot be inferred behind Cloud Run.
- WHEN the official `a2a` client resolves our card and calls `send_message`
  THEN it receives `Task(submitted)` → `artifactUpdate`(s) → stream closure;
  WHEN it calls `tasks/get` afterwards THEN the task is `completed`.
- WHEN a request names an agent not in `EXPOSE_AGENTS` THEN a JSON-RPC error
  is returned **before** any task is created.
- WHEN a message carries a file part THEN `ContentTypeNotSupportedError`.
- WHEN a JSON-RPC call has no valid token and auth is enabled THEN the
  response is the existing 401 `error_envelope`, never an SDK auth error.
- WHEN a `tasks/get` names a task owned by another tenant THEN the A2A
  authorization error; WHEN same tenant THEN the task.
- WHEN `tasks/cancel` arrives mid-stream THEN the executor's `aclosing`
  releases `stream_dispatch` and the task ends `canceled`; WHEN the SSE
  client merely disconnects THEN the agent runs to completion (documented).
- WHEN two peers, or a peer and a built-in, share a local name THEN
  `build_orchestrator` raises `ConfigError` (fail closed); WHEN names are
  distinct THEN both register.
- WHEN a peer is configured with `require_signed_card=true` and the card's
  signature does not verify against the configured trust anchor THEN the
  peer is not registered; WHEN it verifies THEN it is.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_A2A__ENABLED` | `false` | Mount the Agent Card + JSON-RPC routes |
| `MANGOMAS_A2A__PUBLIC_URL` | _(none)_ | Base URL written into `supportedInterfaces[].url`; required when enabled |
| `MANGOMAS_A2A__PATH_PREFIX` | `/a2a` | JSON-RPC endpoint path |
| `MANGOMAS_A2A__EXPOSE_AGENTS` | `[]` | Explicit allow-list of agents published as skills; empty = none |
| `MANGOMAS_A2A__TASK_STORE` | `memory` | `memory` \| `sql` (`sql` needs the `a2a-sql` extra + `a2a-db` migrations) |
| `MANGOMAS_A2A__TASK_TTL_SECONDS` | `3600` | Eviction age for finished tasks in the memory store (the SDK store has none) |
| `MANGOMAS_A2A__PEERS` | `[]` | Phase 2: JSON list of `{name, url, secret_ref?, require_signed_card?}` |
| `MANGOMAS_A2A__PEER_TIMEOUT_SECONDS` | `60.0` | Phase 2: httpx timeout for remote calls |
| `MANGOMAS_A2A__TRUST_ANCHOR_JWKS_URL` | _(none)_ | Phase 2: JWKS for card-signature verification; a card-supplied `jku` is never trusted |

All tunables are `DEFAULT_*` module constants in `config/a2a.py` — **no
hard-coded values**. Rows land in `AGENTS.md`, `.env.example` and
`deploy/README.md` when the group lands (`tests/deploy/test_env_example_contract.py`).

## Protocol / contract impact

- New/changed protocols: `adapters/a2a/base.py::RemoteAgentClient`
  (`@runtime_checkable`, Phase 2). No existing Protocol gains a method.
- New error types (Phase 2, protected path): `RemoteAgentError` (502) ⊃
  `RemoteAgentUnavailable` (503), `RemoteAgentTimeout` (504) — `errors.py`,
  `api/errors.py::_ERROR_STATUS` and `tests/test_errors.py` in lock-step.
- Registry additions: `target_registry` → `remote_agent` (Phase 2). Peers
  register as agents on the orchestrator, not in `agent_registry`.
- New package `src/mangomas/a2a/` (pure domain: card, mapping, executor) joins
  the import-linter independence contract and gets a 95 % floor.
- Dependency: optional extra `a2a = ["a2a-sdk[fastapi]>=1.2,<2"]` (pulls
  `protobuf`, `google-api-core`; raises floors to `httpx>=0.28.1`,
  `pydantic>=2.11.3`). `pyproject.toml` is a protected path →
  `BREAKING-CHANGE: pyproject.toml — …`.

## Backwards-compatibility

- Disabled: route table, OpenAPI snapshot, `AgentContext` and every public
  signature are unchanged; the `a2a` package is never imported.
- Enabled: purely additive routes; the existing `/agents/*` surface and its
  SSE framing are untouched.
- No deprecations.

## Test plan

- Unit: `tests/a2a/test_card.py`, `test_mapping.py` (Hypothesis over parts →
  request; every `MangomasError` subclass maps to a terminal state without
  leaking `detail`), `test_executor.py` (FakeLLM-backed orchestrator;
  `MaxStepsExceeded` → `failed`; cancel mid-stream → `canceled`),
  `test_disabled.py` (flag-off identity).
- Self-interop: `tests/a2a/test_roundtrip.py` drives the official `a2a`
  client over `httpx.ASGITransport` into `create_app(...)`. Phase 2 reverses
  it (`RemoteA2AAgent` → in-process SDK server with a scripted executor).
- Fakes: `FakeRemoteAgentClient` in `tests/fakes.py`.
- Gated: `RUN_A2A=1 pytest tests/integration/test_a2a_live.py` against the
  a2a-samples Hello World agent.
- Gates both directions: the self-interop test must fail when the card
  omits `streaming`, when auth is bypassed, or when ownership scoping is
  removed (`mango-mutation-proof`).
- Coverage: 95 % global, `a2a/**` 95 %, `adapters/**` 85 %.

## Go / no-go gate

Implementation starts when **one** of these holds, recorded on the PR:

1. A named partner or framework (ADK, Microsoft Agent Framework, BeeAI, …)
   needs to call Mango-Mas agents over A2A.
2. A pipeline needs a specialist that exists only as a remote A2A agent.

Until then this spec stays **Draft** and the reliability plan's "revisit
after C4" stands.

## Acceptance criteria

- [x] Design reviewed (SDLC / AI-ML / Product panel + independent reviewer; see ADR-0037).
- [ ] Go / no-go gate met and recorded.
- [ ] Feature off by default → no behaviour change (test proves it).
- [ ] Feature on via env → documented behaviour (self-interop test proves it).
- [ ] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint`, `protected-paths` clean.
- [ ] CHANGELOG updated; ADR-0037 moves from Proposed to Accepted.
