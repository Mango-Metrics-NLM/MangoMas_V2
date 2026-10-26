# ADR-0037: A2A is an opt-in boundary adapter, not a core protocol

## Status

Proposed — design accepted for the record; implementation gated by spec-0037's
go / no-go gate. Does not override the deferral in
`docs/plans/20260916T214636Z-reliability-evidence-plan.md` ("revisit after C4").

## Context

Agents are in-process and reachable only through the bespoke
`/agents/{name}/invoke|stream` surface. A2A (v1.0.1, Linux Foundation / AAIF;
`a2a-sdk` 1.2.x) is the agent↔agent complement of MCP. Research for spec-0037
found: A2A costs ~50 % more code and pays off only for stateful delegation
(arXiv 2607.23884); eleven protocol-level gaps persist with every optional
safeguard on — unowned `contextId`s, unverified webhook URLs, multi-hop
identity loss (arXiv 2609.10871); routing by card `name` dispatches to the
wrong peer (arXiv 2609.27624). Here, `require_auth` is a shared secret with
no principal and the tenant header is client-asserted.

## Decision

A2A lives at the boundary only: an SDK-backed server façade over the existing
`Orchestrator` under `api/` + a new pure-domain `a2a/` package, and a
`RemoteA2AAgent` under `adapters/a2a/` + `agents/` that satisfies the `Agent`
protocol and is registered under an operator-chosen name bound to a configured
URL. Default-off; no edit to any `core/` contract.

## Consequences

### Positive

- Every topology reaches a remote peer through `orch.register()` with zero
  core change.
- Flag-off builds are byte-identical; the SDK is an optional extra.
- Excluding push notifications and `input-required` in v1 removes the two
  worst A2A threat classes outright.

### Negative / Trade-offs

- v1 task ownership is `(tenant, "shared")` — no principal exists; stated on
  the card per ADR-0033. Multi-party use needs ADR-0014 revisited.
- The SDK pulls `protobuf`/`google-api-core`, raises `httpx`/`pydantic`
  floors, and broke compatibility 0.3 → 1.0.
- `InMemoryTaskStore` is per-instance; on scaled Cloud Run `tasks/get` is
  best-effort unless the `sql` store is chosen.

### Neutral

- INV-16 (ADR-0029) is untouched: the cognitive layer never speaks A2A.
- "No agent-to-agent calls inside agents" holds: the remote agent is
  dispatched *by* the orchestrator, not called from an agent body.

## Alternatives Considered

- **Hand-rolled JSON-RPC server** — rejected: conformance drift outweighs
  the dependency saved.
- **Multi-part `Message` in `core/agent.py`** — rejected: breaks every caller
  for a boundary-only need; project parts at the edge.
- **`remote_agent` workflow node kind** — rejected: edits the closed
  `WorkflowNode` union (ADR-0011); a registered `Agent` gives the same reach.
- **MCP-only interop** — rejected for delegation: no task lifecycle
  (arXiv 2607.23884); kept for tooling.
- **Route peers by card `name`** — rejected (arXiv 2609.27624, CWE-706).

## References

- Spec: `specs/0037-a2a-interop.md` (phases, env table, test plan, gate)
- Code seams: `src/mangomas/api/auth.py::require_auth`,
  `src/mangomas/composition/llm.py::_AgentLLMOverrideCloseMixin`
- Related ADRs: ADR-0001 (swap matrix), ADR-0008, ADR-0011, ADR-0014,
  ADR-0029, ADR-0033
- External: https://a2a-protocol.org/latest/specification/ ·
  https://github.com/a2aproject/a2a-python · arXiv 2505.02279, 2607.23884,
  2609.10871, 2609.27624, 2603.23801, 2606.31498
