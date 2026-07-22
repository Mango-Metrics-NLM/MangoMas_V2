# Spec-0008: Workflow HTTP endpoint

- **Status:** Implemented
- **Linked ADR:** ADR-0012
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

The declarative workflow engine (spec 0005 / ADR-0011) is reachable only from
Python (`execute_workflow`) and the `mangomas workflow` CLI — the FastAPI surface
exposes single-agent dispatch (`/agents/{name}/invoke|stream`) but no workflow
route. For a platform architected for Cloud Run this is a parity gap: a graph
cannot be run or validated over HTTP. This spec adds `POST /workflows/run` and
`POST /workflows/validate`, mirroring the CLI's proven semantics.

## Requirements

- `POST /workflows/run` executes a `WorkflowGraph` against the app orchestrator
  and returns the final node's `AgentResponse` (same envelope as
  `/agents/{name}/invoke`).
- `POST /workflows/validate` parses + validates a graph without any LLM I/O and
  returns its `name` + root node `kind`.
- Graph source resolution mirrors `cli/main.py::_resolve_workflow_source`: a
  per-request `definition` (inline JSON or path) runs even when the feature is
  disabled (per-invocation opt-in); otherwise the feature must be enabled AND a
  `MANGOMAS_WORKFLOW__DEFINITION` configured.
- Must remain **additive & default-OFF**: with no per-request `definition` and
  `workflow.enabled=false` (the default) the endpoints raise `ConfigError` (400);
  the existing `/agents/*` + health routes are unchanged.

## Config / env additions

None. Reuses the existing `WorkflowSettings` (`MANGOMAS_WORKFLOW__ENABLED` /
`__DEFINITION`). No new `DEFAULT_*` constants.

## Protocol / contract impact

- New/changed protocols: _none_ (routes delegate to the public
  `mangomas.workflow.load_workflow` / `execute_workflow`).
- New error types: _none_ — `load_workflow` already normalises bad input to
  `ConfigError` (400); `execute_workflow` surfaces `AgentNotFound` (404) /
  `MaxStepsExceeded` (422) / LLM errors, all already in `_ERROR_STATUS`.
- Registry additions: _none_.
- New api-local request/response models (`WorkflowRunRequest`,
  `WorkflowValidateRequest`, `WorkflowValidateResponse`) — additive, api layer
  only; the core `AgentRequest`/`AgentResponse` models are untouched.

## Backwards-compatibility

- Feature off (default): `/workflows/*` respond `400 ConfigError` when called
  without a `definition`; every existing route byte-identical.
- No protected-path edit; `create_app` signature unchanged.

## Test plan

- Unit (`tests/test_workflow_api.py`, api floor 95): stub orchestrator via
  `create_app(orchestrator=...)` + `FakeLLM`/`FakeRepository`; cover run
  (single-agent, fan_out concat, loop), validate (ok + malformed→400), and the
  disabled-gate (no definition→400). Route/graph constants in `tests/constants.py`.
- No new fake, no Hypothesis, no network path.
- Coverage: maintain the 95% global gate and the api per-package floor.

## Acceptance criteria

- [x] Feature off by default + no `definition` → `400 ConfigError` (test proves it).
- [x] A per-request `definition` runs a graph over HTTP regardless of the enabled
      flag, returning the final `AgentResponse` (test proves it).
- [x] `validate` returns `name` + `root_kind` for a valid graph and `400` for a
      malformed one.
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; ADR-0012 added.
