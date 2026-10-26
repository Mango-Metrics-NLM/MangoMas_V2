# ADR-0037: A2A is an opt-in boundary adapter, not a core protocol

## Status

Proposed — design recorded; implementation gated by spec-0037's go / no-go
gate. The reliability plan's "revisit after C4" deferral stands.

## Context

Agents are in-process, reachable only through `/agents/{name}/invoke|stream`.
A2A (v1.0.1, Linux Foundation / AAIF; `a2a-sdk` 1.2.x) is the agent↔agent
complement of MCP. Research for spec-0037 found: A2A costs ~50 % more code and
pays off only for stateful delegation (arXiv 2607.23884); eleven protocol-level
gaps persist with every optional safeguard on — unowned `contextId`s, unverified
webhook URLs, multi-hop identity loss (arXiv 2609.10871); routing by card `name`
dispatches to the wrong peer (arXiv 2609.27624). Here `require_auth` is a shared
secret with no principal and the tenant header is client-asserted.

## Decision

A2A lives at the boundary only: an SDK-backed server façade over the existing
`Orchestrator` under `api/` + a new pure-domain `a2a/` package, and a
`RemoteA2AAgent` under `adapters/a2a/` + `agents/` that satisfies the `Agent`
protocol and is registered under an operator-chosen name bound to a configured
URL. Default-off; no edit to any `core/` contract.

## Consequences

### Positive

- Every topology reaches a remote peer via `orch.register()`; no core change.
- Flag-off builds are byte-identical; the SDK is an optional extra.
- Excluding push notifications and `input-required` removes the two worst
  A2A threat classes.

### Negative / Trade-offs

- v1 task ownership is `(tenant, "shared")` — no principal exists; stated per
  ADR-0033. Multi-party use needs ADR-0014 revisited.
- The SDK pulls `protobuf`/`google-api-core`, raises `httpx`/`pydantic`
  floors, and broke compatibility 0.3 → 1.0.
- `InMemoryTaskStore` is per-instance; on scaled Cloud Run `tasks/get` is
  best-effort unless the `sql` store is chosen.

### Neutral

- INV-16 (ADR-0029) is untouched: the cognitive layer never speaks A2A.
- "No agent-to-agent calls inside agents" holds: the orchestrator dispatches
  the remote agent; no agent body calls it.

## Alternatives Considered

- **Hand-rolled JSON-RPC server** — rejected: conformance drift outweighs
  the dependency saved.
- **Multi-part `Message` in `core/agent.py`** — rejected: breaks every caller
  for a boundary-only need.
- **`remote_agent` workflow node kind** — rejected: edits the closed
  `WorkflowNode` union (ADR-0011).
- **MCP-only interop** — rejected for delegation: no task lifecycle; kept for
  tooling.
- **Route peers by card `name`** — rejected (arXiv 2609.27624, CWE-706).

## References

- Spec: `specs/0037-a2a-interop.md` (phases, env table, test plan, gate)
- Code seams: `src/mangomas/api/auth.py::require_auth`,
  `src/mangomas/composition/llm.py`
- Related ADRs: ADR-0001 (swap matrix), ADR-0008, ADR-0011, ADR-0014,
  ADR-0029, ADR-0033
- External: https://a2a-protocol.org/latest/specification/ ·
  https://github.com/a2aproject/a2a-python · arXiv ids in spec-0037
