# Declarative workflow graphs

Compose multiple agents through a declarative JSON graph that the `Orchestrator`
executes — without writing Python. The feature is **additive and default-OFF**
(`MANGOMAS_WORKFLOW__ENABLED=false`); see spec `0005` and ADR-`0011`.

## Model

A workflow is a **bounded tree** with one `root` node. Composition lives only in a
`sequence`; a `fan_out` fans to agents; a `loop` wraps one agent. Every leaf maps
1:1 onto a single public dispatch call, so the executor never reimplements
pipeline threading, fan-out `gather`, or the acceptance loop. The tree is acyclic
by construction — there is no cycle detection.

| kind | fields | compiles to |
|------|--------|-------------|
| `agent` | `agent: str` | `dispatch(agent, request)` — returned verbatim |
| `fan_out` | `branches: [agent…]`, `join: first \| concat` | `dispatch_fan_out(names, request)` + join |
| `loop` | `agent: str`, `accept: PredicateSpec`, `max_steps: int` | `dispatch(agent, acceptance_fn=…, max_steps=…)` |
| `sequence` | `steps: [agent \| fan_out \| loop]` | threads content+metadata like `dispatch_pipeline` |

Executors are **metadata-transparent** — they return the underlying `dispatch*`
result verbatim (provenance goes to OTel spans), so an all-agent `sequence` is
byte-identical to `dispatch_pipeline([names], request)`. The `fan_out` `concat`
reduce is the one synthesized response: `agent="fan_out"`, empty metadata.

## Acceptance predicates

The `loop` node's `accept` is a declarative `PredicateSpec` compiled once into a
pure, **synchronous** `AcceptanceFn`:

- `{"kind": "contains", "value": "DONE", "case_sensitive": false}`
- `{"kind": "regex", "value": "^OK", "flags": ["ignorecase"]}` — flags are any of
  `ignorecase` / `multiline` / `dotall`.
- `{"kind": "json_field", "field": "passed", "equals": true}` — parse the response
  as a JSON object and test one addressed field. `field` is a dotted path over
  mappings (`"review.passed"`); exactly one of `equals` (a JSON scalar),
  `at_least` or `at_most` (numeric) selects the comparison.

The same vocabulary drives `branch`'s `when`, since both are typed `PredicateSpec`.

### Never text-match a structured agent

`contains` and `regex` match the response **text**, which is correct for an agent
that emits prose and wrong for one that emits JSON. `ReviewerAgent` emits
`{"passed": …, "score": …, "feedback": …, "suggestions": […]}`, and **no spelling
of a substring match over that is correct** — measured, both directions:

| Needle | Response | Truth | Predicate | |
|---|---|---|---|---|
| `"passed": true` | compact `model_dump_json()` | accept | **reject** | false negative |
| `"passed":true` | pretty-printed | accept | **reject** | false negative |
| `passed:true` | rejecting review, `feedback` mentions it | reject | **accept** | false positive |
| `passed:true` | rejecting review, `suggestions` echo it | reject | **accept** | false positive |

A human writes the needle with a space after the colon; `model_dump_json()` emits
it without one; the loop runs to `max_steps` and raises `MaxStepsExceeded` **on an
approving review**. Dropping the quotes to survive that then matches prose inside
`feedback` — accepting a **rejecting** review. Fixing the first manufactures the
second. Use `json_field` for `planner` and `reviewer`; `contains` / `regex` remain
correct for `chat`, `summarize` and `tool`.

Parsing is **strict** — the whole response must be a JSON object, matching
`MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT`, so the two guards agree. A response
that is not parseable JSON, is a JSON array, or whose path does not resolve is
simply **not accepted**: the predicate never raises, so non-convergence still
surfaces as `MaxStepsExceeded` (422). Set the `mangomas.workflow.predicate` logger
to `DEBUG` to see the addressed path, whether it resolved, and the verdict per step.

## Example

```json
{
  "schema_version": 1,
  "name": "plan-review-refine",
  "root": {
    "kind": "sequence",
    "steps": [
      {"kind": "agent", "agent": "planner"},
      {"kind": "fan_out", "join": "concat",
       "branches": [{"kind": "agent", "agent": "reviewer"},
                    {"kind": "agent", "agent": "summarize"}]},
      {"kind": "loop", "agent": "chat", "max_steps": 5,
       "accept": {"kind": "contains", "value": "DONE", "case_sensitive": false}}
    ]
  }
}
```

### Canonical example

The repo ships the canonical planner → tool → reviewer pipeline as
[`examples/workflows/plan-execute-review.json`](../../examples/workflows/plan-execute-review.json):
an all-`agent` `sequence`, so it is byte-identical to
`dispatch_pipeline(["planner", "tool", "reviewer"], request)`. The planner and
reviewer emit schema-constrained JSON (`ExecutionPlan` / `ReviewResult`); set
`MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT=true` and
`MANGOMAS_AGENTS__REVIEWER__VALIDATE_OUTPUT=true` to have each agent reject
malformed output with `LLMBadResponse` instead of passing it downstream.
Pinned loadable by `tests/test_plan_execute_review.py`.

```bash
mangomas workflow validate -f examples/workflows/plan-execute-review.json
mangomas workflow run "ship the release" -f examples/workflows/plan-execute-review.json
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `MANGOMAS_WORKFLOW__ENABLED` | `false` | Enable declarative graph dispatch |
| `MANGOMAS_WORKFLOW__DEFINITION` | _(none)_ | Path to a JSON graph, or inline JSON |
| `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION` | `true` | Allow a caller-supplied graph (CLI `-f`, `POST /workflows/run` body). `false` refuses one outright (ADR-0033) |

The graph declares its own `schema_version`, validated at load (an unsupported
version is a `ConfigError`, unlike the settings' forward-compat warn).

`ALLOW_INLINE_DEFINITION` exists because the per-invocation opt-in below is a
real capability: a caller who can reach `POST /workflows/run` can compose and
run an arbitrary agent graph, even with the feature disabled. Set it to `false`
where one shared credential should not carry that, and only the
server-configured `DEFINITION` runs.

## CLI

```bash
export MANGOMAS_WORKFLOW__ENABLED=true
export MANGOMAS_WORKFLOW__DEFINITION=./graph.json
mangomas workflow validate            # parse + validate, no LLM I/O
mangomas workflow run "ship it"       # execute; prints the final node's content
# --definition / -f overrides the setting (and runs even when disabled),
# unless MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION=false, which refuses it
mangomas workflow run "ship it" -f ./graph.json
```

Exit codes mirror the eval CLI: **2** for a config error (disabled + no
`--definition`, a `--definition` refused by `ALLOW_INLINE_DEFINITION=false`,
malformed graph), **1** for a runtime error (unknown agent, LLM failure).

## Programmatic use

```python
from mangomas.workflow import execute_workflow, load_workflow

graph = load_workflow("graph.json")  # path or inline JSON string
response = await execute_workflow(graph, request, orch=orchestrator)
```

## Errors

No new error types. Malformed / unknown-kind / unsupported-version → `ConfigError`
(400, at the loader boundary and via `UnknownProvider`); unknown agent →
`AgentNotFound` (404) at execution; loop exhaustion → `MaxStepsExceeded` (422).
`errors.py`, `api/errors.py::_ERROR_STATUS`, `core/*`, and the `composition/`
package are unchanged.

## Extending

Add a node kind with the `mango-workflow` skill or the
`mango-workflow-graph-dev` agent: define a frozen model in `graph.py` (add
it to the `WorkflowNode` union), write an executor under `workflow/nodes/` that
delegates to a public dispatch method, register it in one line via
`make_node_factory` from `workflow/nodes/_factory.py` (which supplies the shared
`isinstance` guard and names the closure `_<kind>_factory`), and add a parity
test in `tests/test_workflow_executor.py`.

## Non-goals (v1)

- Explicit edges + cycle detection (the graph is a bounded tree, not a DAG).
- Multiple named graphs / a graph catalog; streaming a whole graph; a loop
  "best-effort on exhaustion" mode; entry-point discovery of third-party node
  kinds.

Two original v1 non-goals have since shipped: conditional branch-on-predicate
selection landed as the `branch` node (spec 0012 / ADR-0016), and `fan_out`
branches widened from single agents to any `WorkflowStep` (spec 0013 /
ADR-0018).
