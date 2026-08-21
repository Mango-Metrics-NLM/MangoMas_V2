# ADR-0012: Workflow HTTP endpoint

## Status

Accepted

## Context

The declarative workflow engine (ADR-0011) shipped with a CLI and a programmatic
API but no HTTP surface, while single-agent dispatch has `/agents/{name}/invoke`.
For a Cloud-Run-targeted platform this breaks parity: a `WorkflowGraph` cannot be
run or validated over HTTP. We need to expose it without editing protected core,
without a new error type, and default-OFF.

## Decision

Add two thin api-layer routes — `POST /workflows/run` and
`POST /workflows/validate` — inside `create_app`, delegating to the public
`load_workflow` / `execute_workflow`. Graph-source resolution mirrors the CLI's
`_resolve_workflow_source`: a per-request `definition` runs even when the feature
is disabled; otherwise `workflow.enabled` + a configured definition is required,
and the disabled/unset path raises `ConfigError` (HTTP 400) through the existing
`_ERROR_STATUS` handler.

## Consequences

### Positive

- HTTP ⇄ CLI parity for workflows; zero protected-path edits; no new error type
  (reuses `ConfigError`/`AgentNotFound`/`MaxStepsExceeded` already mapped).
- Per-request `definition` makes the endpoint usable on a default (disabled)
  deployment without global config — same opt-in ergonomics as the CLI.

### Negative / Trade-offs

- The "disabled" state is a `400 ConfigError`, not a `404`. We model a disabled
  feature as a configuration error (consistent with the CLI's config exit code 2)
  rather than a missing resource, because the route genuinely exists and a
  `definition` can drive it per-invocation.

### Neutral

- Routes are unversioned (`/workflows/*`), matching the existing `/agents/*`
  surface; a global `/v1` migration is deferred to keep the surface consistent.
- New request/response models live in the api layer; core models untouched.

## Alternatives Considered

- **Conditionally mount routes when `enabled`** — rejected: a disabled deployment
  would 404 and lose the per-request `definition` opt-in the CLI already offers.
- **404 for the disabled state** — rejected: needs a bespoke `JSONResponse`
  outside the unified error envelope; `ConfigError`→400 reuses the pipeline.
- **Subclass `AgentRequest` to carry `definition`** — rejected: nesting the
  `AgentRequest` under a `request` field keeps the protected core model untouched
  and separates graph selection from the agent payload.

## References

- Code: `src/mangomas/api/app.py` (`/workflows/*` routes, `_ERROR_STATUS`
  L46–60), `src/mangomas/workflow/__init__.py` (`execute_workflow`,
  `load_workflow`), `src/mangomas/cli/commands/workflow.py::_resolve_workflow_source` (mirrored).
- Related: spec `specs/0008-workflow-http-endpoint.md`; ADR-0011 (workflow graphs).
