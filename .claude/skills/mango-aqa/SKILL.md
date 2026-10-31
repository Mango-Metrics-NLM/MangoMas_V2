---
name: mango-aqa
description: >
  Origin-delta automated quality assurance for Mango-Mas V2. Use when: triaging
  a branch against origin, recording a defect before fixing it, proving a
  regression guard goes red first, running the CUDA placement lane, or checking
  that a live suite's step budget is not tighter than the adapter budget it
  wraps. Covers the lane status matrix, interpreter pinning, and the
  dual-population install check.
argument-hint: "Name the lane (hermetic, GPU, live) or paste the failing guard"
---

# Mango-Mas AQA Skill

## When to Use

- A branch needs a defect register before any fix lands
- A regression guard was added and must be shown red against the pre-fix tree
- `make gpu` or a live LM Studio / Vertex lane is part of the change
- The per-step timeout and the adapter timeout might be inverted

## Lane status

Record every lane as one of `PASS`, `FAIL`, `NOT-RUN`, or `FAIL(GATE)`.
`NOT-RUN` is an explicit choice (no GPU, no live server), not a silent skip.
`FAIL(GATE)` means the suite was collected and the gate itself rejected it.

## Procedure

1. Pin the interpreter. `sys.executable` and `pip show mangomas` must name the
   same environment. A second population (`[dev,embeddings-local,rag]` plus a
   `cu130` CUDA wheel) is a different interpreter, not a flag on the first.
2. Write the defect in the regression module before changing production code.
   Run that guard and keep the failure.
3. Fix the smallest cause. Re-run the same guard green.
4. Hermetic lanes use fakes and `httpx.MockTransport`. They do not open a
   socket and do not import torch unless `RUN_GPU=1`.
5. `make gpu` is fail-not-skip: with the gate set, a missing CUDA device fails
   the run. Do not add a `skipif` for "no GPU".
6. Live suites resolve `loop.step_timeout_seconds` from
   `resolve_live_step_timeout`, so an unset step budget equals the adapter
   budget instead of the 30s production default.

## Commands

```powershell
python -m pytest tests/regression -q --no-cov
make gpu
make guard-probe
```

`make guard-probe` mutates the tree. Do not run it as part of `make gate`.
