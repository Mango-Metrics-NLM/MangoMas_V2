# Declarative multi-agent workflow graphs — delivery plan

- **Branch:** `claude/multi-agent-workflow-plan-hoyydz`
- **Date:** 2026-07-21
- **Target release:** `[Unreleased]`
- **Status:** Delivered
- **Spec / ADR:** `specs/0005-declarative-agent-workflows.md` / `docs/adr/0011-declarative-agent-workflows.md`

## Executive Summary

Compose multiple agents through a declarative JSON graph consumed by the
`Orchestrator`, additive and default-OFF. The graph is a bounded tree
(`sequence` of `agent` / `fan_out` / `loop`) compiled to the existing public
dispatch methods, so no protected core file changes. Milestones M1→M4 ship the
runtime + CLI; M5 refreshes docs/agents/skills; M0 is the spec/ADR. Delivered as
one PR under `[Unreleased]`.

---

## Milestone M1 — Domain model + predicate compiler

### Objective.
Frozen `WorkflowGraph` and a pure/sync `AcceptanceFn` compiler, unit-testable
without an orchestrator.

### Dependency edge.
Requires: `mangomas.config` constants. Consumes: pydantic. Produces:
`WorkflowGraph`, `PredicateSpec`, `compile_predicate`.

### Gap analysis.
**Exists.** `Registry`, `ConfigError`, `AcceptanceFn`. **Missing.** graph model,
predicate compiler. **Redundant-if-added.** cycle detection (acyclic tree).

### File-level plan.
| File | New/Modify | Purpose |
|------|------------|---------|
| `src/mangomas/workflow/graph.py` | New | Node models + `WorkflowNode` union + `WorkflowGraph` |
| `src/mangomas/workflow/predicate.py` | New | `PredicateSpec` + `compile_predicate` (local flag map) |
| `src/mangomas/config.py` | Modify | `DEFAULT_WORKFLOW_*` constants |

### Settings additions. `DEFAULT_WORKFLOW_{ENABLED,DEFINITION,SCHEMA_VERSION,LOOP_MAX_STEPS}`.
### Registry wiring. None (M1 is pure data).
### Logging touchpoints. None (models only).
### Error model. `ConfigError` on bad predicate flag/pattern.
### Test strategy. **Unit.** `test_workflow_graph.py`, `test_workflow_predicate.py`.
### Backwards-compatibility checklist. New package; nothing imported by default.
### Acceptance criteria. Valid graphs parse; invalid kinds/fields/containers raise.
### Risk + mitigations. Recursive-union forward refs → `model_rebuild()` at import.

---

## Milestone M2 — Node executors + registry + driver

### Objective.
Compile the graph to public `dispatch*` calls; orchestrator injected per call.

### Dependency edge.
Requires: M1, `Orchestrator`. Produces: `node_registry`, `execute_workflow`.

### Gap analysis.
**Exists.** `dispatch`/`dispatch_pipeline`/`dispatch_fan_out`. **Missing.** node
executors + registry + driver. **Redundant-if-added.** an "all-agent fast path"
(the single recursive `sequence` path already equals `dispatch_pipeline`).

### File-level plan.
| File | New/Modify | Purpose |
|------|------------|---------|
| `src/mangomas/workflow/registry.py` | New | `node_registry` + `resolve_executor` |
| `src/mangomas/workflow/executor.py` | New | `NodeExecutor` protocol + `execute_workflow` |
| `src/mangomas/workflow/nodes/*.py` | New | Self-registering executors (agent/sequence/fan_out/loop) |
| `src/mangomas/workflow/__init__.py` | New | Public surface; seeds the registry |

### Settings additions. None.
### Registry wiring. `node_registry.register("<kind>", factory)` per node module; seeded by `import mangomas.workflow`.
### Logging touchpoints. OTel spans `workflow.execute` / `workflow.node.<kind>` (metadata-transparent).
### Error model. `ConfigError` on factory kind-mismatch; `AgentNotFound` at execution; `MaxStepsExceeded` from `dispatch`.
### Test strategy. **Unit.** `test_workflow_registry.py`. **Parity.** `test_workflow_executor.py` (sequence≡pipeline incl. `planner→tool→reviewer`; fan_out≡fan_out+join; loop accept + `MaxStepsExceeded`; nesting; unknown agent).
### Backwards-compatibility checklist. Executors return `dispatch*` results verbatim → full-model parity with imperative calls.
### Acceptance criteria. All parity + composition tests green; workflow package ≥95%.
### Risk + mitigations. Empty-registry/import-cycle → seed in `__init__` via submodule paths only.

---

## Milestone M3 — Settings + loader

### Objective. Env-driven, path-or-inline definition, default-OFF.
### Dependency edge. Requires: M1. Produces: `WorkflowSettings`, `load_workflow`.
### Gap analysis. **Exists.** `EvalSettings` pattern. **Missing.** `WorkflowSettings`, loader. **Redundant-if-added.** a settings `schema_version` (lives on the graph).
### File-level plan.
| File | New/Modify | Purpose |
|------|------------|---------|
| `src/mangomas/config.py` | Modify | `WorkflowSettings` + attach to `Settings` |
| `src/mangomas/workflow/loader.py` | New | `load_workflow` (ConfigError boundary) |

### Settings additions. `MANGOMAS_WORKFLOW__ENABLED` / `__DEFINITION`.
### Registry wiring. None.
### Logging touchpoints. None.
### Error model. `ConfigError` on unreadable/invalid JSON, schema-validation, unsupported `schema_version`.
### Test strategy. **Unit.** `test_workflow_settings.py`, `test_workflow_loader.py`.
### Backwards-compatibility checklist. `Settings()` constructs with `workflow.enabled=False`.
### Acceptance criteria. Enabled-without-definition raises; inline & file both load.
### Risk + mitigations. Inline-vs-path heuristic (`{`-prefix) documented.

---

## Milestone M4 — CLI surface

### Objective. `mangomas workflow run` / `validate`, off-by-default; API deferred.
### Dependency edge. Requires: M2, M3. Produces: `workflow` Typer sub-app.
### Gap analysis. **Exists.** `rag`/`eval` CLI idioms. **Missing.** `workflow` sub-app. **Redundant-if-added.** an HTTP endpoint (spec is CLI/programmatic).
### File-level plan.
| File | New/Modify | Purpose |
|------|------------|---------|
| `src/mangomas/cli/main.py` | Modify | `workflow` sub-app (`run`, `validate`) |

### Settings additions. None.
### Registry wiring. `import mangomas.workflow` seeds the registry at CLI import.
### Logging touchpoints. `--verbose` DEBUG.
### Error model. Config → exit 2; runtime (`AgentNotFound`/LLM) → exit 1.
### Test strategy. **Unit.** `test_workflow_cli.py` (off-by-default exit 2; run/validate).
### Backwards-compatibility checklist. Existing CLI commands unaffected.
### Acceptance criteria. Disabled + no `--definition` → exit 2; enabled → prints content.
### Risk + mitigations. `--definition` overrides the enabled gate per-invocation.

---

## Milestone M5 — Docs, sub-agent, skill refresh

### Objective. Keep the harness/docs in lock-step (CI runs `frontmatter-lint`).
### File-level plan.
| File | New/Modify | Purpose |
|------|------------|---------|
| `.github/agents/backend/workflow-graph-dev.agent.md` | New | Sub-agent owning `workflow/` |
| `.github/agents/backend.agent.md` | Modify | Add `workflow-graph-dev` to `sub_agents` |
| `.github/skills/mango-workflow/SKILL.md` | New | Declarative-workflow skill |
| `CLAUDE.md` / `NEXT_STEPS.md` / `CHANGELOG.md` / `README.md` / `deploy/README.md` | Modify | Doc-sync |
| `docs/workflow/graphs.md` | New | Feature doc |
| `scripts/run_workflow_e2e.py` | New | LM Studio e2e driver |
| `scripts/check_coverage.py` | Modify | `workflow` per-package floor |

### Acceptance criteria. `frontmatter-lint` `EXIT_OK`; `deploy` contract test green.

---

## Milestone M0 — Spec + ADR (spec-before-code)

### Objective. Lock the contract. Deliverables: fleshed-out `specs/0005`,
`docs/adr/0011`, this plan, `specs/README.md` index update.
