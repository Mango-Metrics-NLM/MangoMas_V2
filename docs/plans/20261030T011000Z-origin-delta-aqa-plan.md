# Origin-delta AQA and CUDA placement gate — delivery plan

- **Branch:** `sdlc/origin-delta-aqa-20261029`
- **Date:** 2026-10-29
- **Target release:** rolling
- **Status:** In progress
- **Specs:** spec-0039
- **ADRs:** none — no boundary change

## Executive summary

This branch runs the full local matrix against `origin/feat/initial-release@a98b626`
on a workstation with two CUDA devices and a local LM Studio. The lanes are
the hermetic gate, tier-1 e2e, the live model, tier-3 compute, and GPU.
Every failure is entered in `docs/testing/rca-20261029.md` before anything is
fixed. Each product defect then gets a red-first guard in
`tests/regression/test_aqa_20261029_*.py`, proven by reverting the fix. The
one constraint on ordering: the baseline runs before any src change, so a
red result is attributable to origin rather than to this branch.

## PR A — origin-delta AQA (spec-0039)

### Milestone A0 — pin and baseline

- **Failing test first:** n/a. This milestone records the baseline.
- **Depends on:** nothing.
- Pin the interpreter in `artifacts/validation/env.txt`, then run
  `make gate`, `make gated-suites` and the spec-0029 R7 meta-tests.

### Milestone A1 — confirmation lanes and CUDA placement gate

- **Failing test first:** with `RUN_GPU=1` and CUDA hidden, the `gpu` tests
  must fail rather than skip.
- **Depends on:** A0.
- Run `make embeddings-local`, `make lmstudio` and `make vertex` where their
  services exist. Add `make gpu` and `tests/rag/test_device_resolution_gpu.py`.

### Milestone A2 — RCA and regression guards

- **Failing test first:** each `D-n` guard, red on the pre-fix tree.
- **Depends on:** A0, A1.

## Deferred / out of scope

- Porting fixes from other trees without reproducing the defect here
  (spec-0039 FR-AQA-11).
- Fixing local-model capability gaps such as a chat model with no
  embeddings endpoint. These are deployment gaps, recorded in
  `NEXT_STEPS.md`.

## Verification

```bash
make gate
make gated-suites
RUN_EMBEDDINGS_LOCAL=1 RUN_RAG=1 python -m pytest tests/rag --no-cov -q
make gpu
make lmstudio
```
