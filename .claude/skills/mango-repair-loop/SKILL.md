---
name: mango-repair-loop
description: >
  Governed local-LLM repair loop for a sandbox the model must not write
  directly. Use when: a planner proposes a change, a chat agent drafts it into
  a staging directory, and a reviewer checks it before a deterministic oracle
  promotes the file. Covers capability probes, import-header prefills,
  line-numbered reference context, and a loopback plus path allowlist.
argument-hint: "Name the sandbox file to repair and the oracle that must pass"
---

# Mango-Mas Repair Loop Skill

## When to Use

- A local model should propose an edit and a harness should decide whether it
  lands
- The edit must be checked by a subprocess or AST oracle before promotion
- The model is reached only through the LM Studio OpenAI-compatible API

## Roles

`PlannerAgent` proposes the change. `ChatAgent` writes the candidate into a
staging directory. `ReviewerAgent` checks the candidate against the plan.
Promotion copies the staged file over the live one only after the oracle
exits zero. The model never receives a write tool aimed at the live tree.

## Procedure

1. Probe the endpoint (`GET /models`) and refuse to start when the expected
   model is not loaded.
2. Prefill the import header so the model edits a body, not the imports.
3. Give the model line-numbered source, not a bare file path.
4. Dispatch one step when the request already contains an assistant prefill.
   A multi-step loop re-sends the prefill and the model rewrites it.
5. Run the deterministic oracle in a subprocess. On failure, feed the oracle
   output back as the next user message. Cap the attempts.
6. Allow outbound calls only to the loopback LM Studio port. Reject any path
   outside the staging directory and the live file being repaired.

## Constraints

- DO NOT promote a candidate the oracle did not accept.
- DO NOT point the model at a path outside the allowlist.
- DO NOT cite a sandbox file as if it lived in this repository's test tree.
