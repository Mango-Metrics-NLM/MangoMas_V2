---
name: mango-workflow
description: >
  Declarative multi-agent workflow graphs in Mango-Mas V2. Use when authoring or
  debugging a WorkflowGraph JSON (sequence, fan_out, loop with an acceptance
  predicate), adding a node kind or predicate, or wiring the `mangomas workflow
  run` CLI. Covers the graph schema, node_registry, predicate compilation to
  AcceptanceFn, and parity with dispatch_pipeline / dispatch_fan_out.
argument-hint: "Describe the graph or node kind (e.g. 'planner then fan-out review then fix loop') or paste a failing workflow test"
---

# Mango-Mas Workflow Skill

## When to Use

- Author a declarative `WorkflowGraph` (JSON) and run it via `mangomas workflow run`
  or over HTTP (`POST /workflows/run|validate`)
- Compose a `sequence` of `agent` / `fan_out` / `loop` / `branch` steps
- Route conditionally with a `branch` node (predicate-selected child; spec 0012 / ADR-0016)
- Add a node kind or an acceptance predicate (`contains` / `regex`)
- Diagnose parity gaps vs the imperative `dispatch_pipeline` / `dispatch_fan_out`
- Enable the feature (`MANGOMAS_WORKFLOW__ENABLED` + `__DEFINITION`)

---

## Quick Commands

```powershell
# Workflow tests
python -m pytest tests/test_workflow_*.py -v

# Validate / run a graph
$env:MANGOMAS_WORKFLOW__ENABLED='true' ; mangomas workflow validate -f graph.json
mangomas workflow run "ship it" -f graph.json
```

```bash
python -m pytest tests/test_workflow_*.py -v
MANGOMAS_WORKFLOW__ENABLED=true mangomas workflow validate -f graph.json
mangomas workflow run "ship it" -f graph.json
```

---

## Graph Rules

| Rule | Detail |
|------|--------|
| Bounded tree | Composition lives only in `sequence`; `fan_out` fans to agents; `loop` wraps one agent; `branch` selects one child. Acyclic by construction — no cycles. |
| Every leaf is one dispatch call | `agent`→`dispatch`, `fan_out`→`dispatch_fan_out`+join, `loop`→`dispatch(acceptance_fn=…, max_steps=…)`. `branch` runs the first matching case's child via `resolve_executor` (no dispatch of its own). |
| Sequence threading | A step's `content` becomes the next step's user message; `metadata` threads too — identical to `Orchestrator.dispatch_pipeline`. |
| Metadata-transparent | An all-agent `sequence` result **equals** `dispatch_pipeline([names], request)` (full model, incl. metadata). |
| Fan-out reduce | `first` returns the first branch verbatim; `concat` newline-joins `content` (`agent="fan_out"`, empty metadata). |
| Predicate is sync | `PredicateSpec` (`contains`/`regex`) compiles once to a sync `AcceptanceFn`; never async, never I/O. |
| Default-OFF | `MANGOMAS_WORKFLOW__ENABLED=false` by default; `--definition` overrides per-invocation. |
| No `errors.py` change | Bad graph → `ConfigError` (400); unknown agent → `AgentNotFound` (404); loop exhaustion → `MaxStepsExceeded` (422). |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/workflow/graph.py` | Node models + `WorkflowNode` union + `WorkflowGraph` |
| `src/mangomas/workflow/predicate.py` | `PredicateSpec` + `compile_predicate` |
| `src/mangomas/workflow/executor.py` | `NodeExecutor` protocol + `execute_workflow` |
| `src/mangomas/workflow/nodes/` | Self-registering `agent` / `sequence` / `fan_out` / `loop` / `branch` executors |
| `src/mangomas/workflow/nodes/_factory.py` | `make_node_factory(kind, node_cls, executor_cls)` — the shared registry factory + `ConfigError` type guard every node module registers through |
| `src/mangomas/workflow/loader.py` | Path/inline JSON → `WorkflowGraph` |
| `src/mangomas/core/orchestrator.py` | `dispatch`, `dispatch_pipeline`, `dispatch_fan_out` (the primitives graphs compile to) |
| `src/mangomas/core/loop.py` | `AcceptanceFn` type alias |
| `tests/test_workflow_executor.py` | Reference parity + composition tests |

---

## Template — Graph JSON

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

## Template — Add a node kind

1. Add a frozen model in `graph.py` and to the `WorkflowNode` union (and, if it
   may be a sequence step, to `WorkflowStep`).
2. Create `nodes/<kind>.py` with an executor whose `run(request, *, orch)`
   delegates to a public dispatch method; register it at module bottom with the
   shared factory builder — one line, no hand-written guard:

   ```python
   from mangomas.workflow.nodes._factory import make_node_factory

   node_registry.register("<kind>", make_node_factory("<kind>", <Kind>Node, <Kind>NodeExecutor))
   ```

   `make_node_factory` supplies the `isinstance` type guard (raising `ConfigError`
   on a mismatched node) and names the closure `_<kind>_factory` so tracebacks and
   registry reprs still identify the node.
3. Add a parity/validation test in `tests/test_workflow_executor.py`.

---

## Workflow

1. Decide the shape: a single node, or a `sequence` of `agent` / `fan_out` / `loop`.
2. Confirm every referenced agent name is registered in `composition.py::agent_registry`.
3. For a `loop`: pick a declarative `accept` predicate and cap `max_steps`.
4. `mangomas workflow validate -f graph.json` (parse-only) before `run`.
5. Add a test using `FakeLLM(replies=[...])` proving parity with the imperative equivalent.

---

## Constraints

- DO NOT reimplement the acceptance loop or fan-out `gather` — delegate to `Orchestrator`.
- DO NOT put workflow provenance in `AgentResponse.metadata` (breaks parity); use spans.
- DO NOT import from `mangomas.eval` — `workflow` is a pure sibling, so a helper it
  needs is re-stated under `workflow/` (and, if more than one workflow module wants
  it, extracted into a shared `workflow/` module such as `nodes/_factory.py` — never
  duplicated within the package).
- DO NOT nest a `sequence` inside a `sequence`, or a composite inside `fan_out`/`loop` (v1 bounds nesting).

---

## Diagnosing Failures

1. `workflow run` exits 2 "disabled" → set `MANGOMAS_WORKFLOW__ENABLED=true` or pass `--definition`.
2. `ConfigError: invalid workflow graph` → unknown `kind`, extra field, or empty `steps`/`branches`.
3. `AgentNotFound` → a node names an agent not registered in `composition.py`.
4. `MaxStepsExceeded` → a `loop` predicate never matched within `max_steps`; refine the predicate or raise the cap.
5. Parity test fails on metadata → an executor mutated `AgentResponse.metadata`; move it to a span.
