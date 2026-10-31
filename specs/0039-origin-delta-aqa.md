# Spec-0039: Origin-delta AQA and the CUDA placement gate

- **Status:** Implemented
- **Linked ADR:** _none — no protocol, error type or registry boundary change_
  (additive `cli` exports `SettingsErrorGroup` and `format_settings_error`
  re-exported on `mangomas.cli.main`, plus bugfixes in `tool_agent.py` and the
  LM Studio adapters per `docs/testing/rca-20261029.md`)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added` / `Fixed`

## Problem

`origin/feat/initial-release@a98b626` had not been run through the full local
matrix on a workstation with CUDA hardware and a local LM Studio. Two gated
tiers (live model, GPU) are `HOSTED_RUNNER_INFEASIBLE`, so only an operator
run can find defects there.

spec-0029 E2 compares top-1 ranking under forced CPU and under auto-detect.
That check is green whether auto-detect lands on CUDA or quietly falls back
to CPU, so a CUDA box whose torch became a CPU wheel passes every tier-3
test. Nothing on origin asserts which device the model actually loaded on.

## Requirements

- **FR-AQA-03/04/05** — `make gate`, `make gated-suites`, and the spec-0029
  R7 meta-tests pass on the clone. Evidence goes in `artifacts/validation/`.
- **FR-AQA-09** — every failure is entered in
  `docs/testing/rca-20261029.md`, classified as a product defect (`D-n`) or
  `ENV`, before any src change.
- **FR-AQA-10** — every `D-n` gets a guard in
  `tests/regression/test_aqa_20261029_*.py` that was red on the pre-fix tree.
  Reverting the fix turns it red again (`mango-mutation-proof`).
- **FR-AQA-11** — the defects VII reports for this same SHA are hypotheses.
  Each one is reproduced red here before any fix, or recorded as
  `NOT-REPRODUCED`.
- **FR-AQA-GPU** — `gpu` marker and `RUN_GPU` gate. With the gate set, a
  missing CUDA device **fails**; it never skips. With CUDA visible,
  auto-detect lands on `cuda`, and every visible device index loads there
  with CPU ranking parity.
- Must remain **additive & default-OFF**: `RUN_GPU` unset means every
  `gpu`-marked test is skipped with the sanctioned reason. The default
  `pytest` run touches no device and no network.

## Scenarios (WHEN/THEN)

- WHEN `RUN_GPU` is unset THEN every `gpu`-marked test skips with exactly
  `set RUN_GPU=1 to run CUDA device tests`, and the zero-skip guard stays
  green.
- WHEN `RUN_GPU=1` and torch sees no CUDA device THEN the `gpu` tests
  **fail** with a message naming the absent device. A skip in that path is
  itself the defect (MUT-GPU-no-cuda).
- WHEN `RUN_GPU=1` and CUDA is visible THEN auto-detect resolves to
  `device.type == "cuda"`, and forcing each index lands on that index with
  the same top-1 source as CPU (MUT-GPU-placement).
- WHEN a `D-n` fix is reverted THEN its regression guard fails. WHEN the fix
  is present THEN it passes.

## Config / env additions

| Env var | Default | Purpose |
|---|---|---|
| `RUN_GPU` | unset | Opt-in CUDA device suite; fails without a device |

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_
- Registry additions: _none_

## Backwards-compatibility

- `RUN_GPU` unset: test collection and outcomes are identical to `a98b626`
  apart from the newly collected, skipped `gpu` items.
- `make embeddings-local` is unchanged. `RUN_GPU` must never ride the nightly
  CPU job.

## Test plan

- Gated: `tests/rag/test_device_resolution_gpu.py` (`embeddings_local`,
  `rag`, `gpu`).
- Meta: `tests/deploy/test_gated_suite_homes.py` keeps `RUN_GPU` in
  `HOSTED_RUNNER_INFEASIBLE`. `HARDWARE_CONTRACT_SCOPE` gains
  `tests/rag/test_device_resolution*.py`, so
  `tests/tooling/test_e2e_hardware_contract.py` lints the new module.
- Regression: `tests/regression/test_aqa_20261029_*.py`, one docstring
  section per `D-n`.

## Acceptance criteria

- [x] `RUN_GPU` unset leaves default behaviour unchanged (sanctioned skip).
- [x] `RUN_GPU=1` without CUDA fails, and `RUN_GPU=1` with CUDA passes.
- [x] `ruff`, `mypy`, `pytest` (95% gate) and `frontmatter-lint` all clean.
- [x] Every `D-n` has a mutation-proven guard (12/12 in `mutation-proof.txt` + 2 `FAIL(GATE)` runs, `docs/testing/rca-20261029.md`).
