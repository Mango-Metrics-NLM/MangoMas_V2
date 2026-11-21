---
name: mango-workflow-generator
description: Skill for generating and managing declarative workflow definitions (JSON) for AI agents.
argument-hint: Ask for generating workflows
---

# Mango Workflow Generator

## Overview
This skill is utilized when defining, generating, or validating declarative agent workflows. It enforces the use of JSON graph definitions (`WorkflowGraph` in `src/mangomas/workflow/graph.py`) loaded via `src/mangomas/workflow/loader.py` (`load_workflow`) and executed through `src/mangomas/workflow/executor.py` (`execute_workflow`) over hard-coded procedural scripts.

## Guidelines
- **Declarative Graphs**: Workflows must be defined as typed JSON node graphs (`agent`, `sequence`, `fan_out`, `loop`, `branch` nodes) validated through `src/mangomas/workflow/validation.py` rather than imperative glue code.
- **No Hard-coded Logic**: Parameterize workflow toggling, graph paths/inline payloads, and inline HTTP graph execution via `MANGOMAS_WORKFLOW__ENABLED`, `MANGOMAS_WORKFLOW__DEFINITION`, and `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION`.
- **Enterprise Standards**: Follow enterprise AQA (Automated Quality Assurance) by ensuring workflows are predictable, testable, and validatable before execution via `mangomas workflow validate` and `POST /workflows/validate` (before running with `mangomas workflow run` or `POST /workflows/run`).
- **Dynamic Adaptability**: Design workflow schemas that can be extended without breaking older graph definitions, preserving frozen node models and registry-based executor lookup.

## Best Practices
1. **Schema Validation**: Run `mangomas workflow validate` locally or submit definitions to `POST /workflows/validate` so `WorkflowGraph` and predicate validation fail fast with config errors before invoking an orchestrator.
2. **Idempotency**: Workflow definitions loaded via `load_workflow` must validate identically across environments with no hidden environment-specific graph branches.
