# Spec-0050: Origin-delta AQA for spec-0043..0049

- **Status:** Implemented
- **Linked ADR:** _none — no protocol, error type or registry boundary change_
  (boundary-validation, IEEE-754 finiteness, isolation-hardening, authority-key,
  and optional-SDK typing fixes per `docs/testing/rca-20261112.md`)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed — Origin-delta AQA (spec-0050)`

## Problem

`origin/feat/initial-release@9f14fe4` advanced 7 feature releases beyond the
prior AQA baseline (`6fbf689`, spec-0042): Ring 1/2/3 harness tool policy,
repair-loop staging sandbox, and destructive-`Bash` hook gate (spec-0043),
pluggable oracle isolation backends (`docker`, `wsl`/`bubblewrap`, `none`) and
`MANGOMAS_REPAIR__*` settings (spec-0044), measured evaluation token accounting,
`json_field`/`tool_trajectory`/`composite` scorers, and deterministic lanes
(spec-0045), `pass^k` reliability trials (spec-0046), `llm_judge` calibration
demotion (spec-0047), prompt-pinned judge controls (spec-0048), and read-only
GCP preflight + offline contracts (spec-0049).

Executing the full dual-population (P1 hermetic `.venv` + P2 CUDA `cu130`
`[dev,embeddings-local,rag,cloud,langfuse]` `.venv-gpu`), dual-GPU (`RTX 5060 Ti`
+ `RTX 5060`), Docker/Postgres/WSL-`bwrap`, live LM Studio, and no-mock TCP/CLI
E2E verification matrix alongside deep code review of the `6fbf689..9f14fe4`
delta surfaced **13 defects (`D-1`..`D-13`)**:

1. **`D-1` (`config/evaluation.py`, `cli/commands/eval.py`)**: `parallelism <= 0`
   bypassed `EvalSettings` and CLI validation, leaking an unclosed `Orchestrator`
   and crashing with a raw `ValueError` traceback (exit `1` instead of `2`).
2. **`D-2` (`cli/commands/rag.py`)**: `mangomas rag query --top-k N` omitted
   `max_top_k=cfg.vector.max_top_k` when constructing `Retriever`, bypassing an
   operator-configured `MANGOMAS_VECTOR__MAX_TOP_K < 50`.
3. **`D-3` (`eval/scorers/embedding.py`)**: `_cosine_similarity` propagated
   `NaN`/`Inf` embeddings or intermediate sums/norms and lacked `[-1.0, 1.0]`
   floating-point drift clamping.
4. **`D-4` (`eval/scorers/json_field.py`, `workflow/predicate/{_json_field,_spec}.py`, `harness/tool_policy.py`)**:
   non-finite floats (`NaN`, `±Inf`) were accepted as valid JSON numbers across
   `json_field` scorers/predicates and `ToolPolicy` `type: "number"` / union
   `type: [...]` schemas.
5. **`D-5` (`eval/scorers/composite.py`, `cli/commands/_eval_config.py`, `eval/dataset.py`, `eval_harness_bridge/src/{convert_dataset,mango_bridge}.py`)**:
   `or {}` Idioms silently coerced falsy non-dict containers (`[]`, `""`, `0`,
   `False`) to `{}`, while `mango_bridge` interpolated unsanitised `agent` names
   into URL paths and accepted non-positive/non-finite `MANGO_TIMEOUT`.
6. **`D-6` (`eval/gate.py`, `eval/baseline.py`)**: `merge_gate_results` copied
   `r0.judge_excluded` instead of `any(r.judge_excluded for r in present)`, and
   `load_baseline` accepted `bool` or non-finite floats for `mean_score` and
   `duration_ms`.
7. **`D-7` (`mango-integration-contracts/src/mango_contracts/authority.py`)**:
   `reject_authority_shaped_keys` inspected only `Mapping` and `Sequence`,
   allowing authority/secret-shaped fields nested inside `@dataclass`, Pydantic
   `BaseModel`, or `set`/`frozenset` values to bypass INV-10/INV-16.
8. **`D-8` (`harness/isolation/{base,none}.py`)**: `resolve_sandbox_argv([])`
   raised `IndexError`, and `NoIsolationBackend.run` neither prepended staged
   `cwd/src` + `cwd` onto `PYTHONPATH` nor disabled `.pyc` writes into staging.
9. **`D-9` (`harness/repair_sandbox.py`)**: `validate_loopback_llm_url` leaked
   raw `ValueError` on out-of-range ports (`http://127.0.0.1:99999`), and
   `validate_promotion_target` matched `PROTECTED_PATHS` case-sensitively on
   case-insensitive filesystems.
10. **`D-10` (`harness/isolation/{docker,wsl}.py`, `config/repair.py`)**:
    `DockerIsolationBackend` lacked CSV/quote/newline host-path validation on
    `--mount` and `--` before image argv; `windows_to_wsl_path` rejected Win32
    extended-length `\\?\C:\...` paths while silently mis-translating relative
    `C:foo` paths; `RepairSettings` accepted option-injected leading `-` or
    control/whitespace characters on backend identifiers.
11. **`D-11` (`scripts/lint_agent_frontmatter.py`, `scripts/_destructive_bash.py`)**:
    `PreToolUse` matched `PROTECTED_PATHS` only on exact relative strings
    (missing absolute worktree, `./`/`../`, and case-varied paths); `PostToolUse`
    `--emit-path` rejected every native Windows backslash path; and
    `_destructive_bash.py` missed `python -m guard_probe --manifest ...` /
    `python -m scripts.guard_probe --manifest ...`.
12. **`D-12` (`tests/constants/live.py`, `docs/testing/vertex-e2e.md`)**:
    `DEFAULT_VERTEX_TEST_MODEL` remained pinned to retired `gemini-2.0-flash-001`
    after production defaults moved to `gemini-2.5-flash`.
13. **`D-13` (`src/mangomas/secrets/gcp.py`)**: P2 `mypy --strict` (with
    `google-cloud-secret-manager` installed) failed with 3 errors (`[name-defined]`
    and `[attr-defined]`) because `_ensure_client`'s local `from google.cloud import secretmanager`
    shadowed the `TYPE_CHECKING` import and referenced `secretmanager.SecretManagerServiceClient`
    instead of `from google.cloud.secretmanager import SecretManagerServiceClient`
    in `GCPSecretManagerProvider`.
14. **`D-14` (`src/mangomas/telemetry/tracing.py`)**: Bare `logging.StreamHandler()`
    captured a closed `sys.stderr` handle after Click/Typer `CliRunner` swaps,
    raising `ValueError: I/O operation on closed file`; replaced with
    `_LiveStderrHandler` dynamically reading `sys.stderr`.
15. **`D-15` (`src/mangomas/config/__init__.py`)**: `validate_cli_token`
    defined in `mangomas.config.repair` was omitted from the `mangomas.config`
    facade re-exports and `__all__`, failing `test_every_owned_public_name_reaches_the_facade`.

## Requirements

- **FR-0050-01 (Full 5-Tier Matrix)** — Execute P1/P2 gates, `make guard-probe`,
  `make gated-suites`, `make studio-probe-mock`, `make embeddings-local`,
  `make gpu` (positive dual-GPU + negative `CUDA_VISIBLE_DEVICES=-1`),
  `make postgres`, `make docker-smoke`, `make oracle-isolation-docker`,
  `make oracle-isolation-wsl`, `make gcp-trace`, `make gitleaks-selftest`,
  `gitleaks`, `make lmstudio`, `make studio-probe`, read-only GCP preflight, and
  live no-mock delta E2E both before and after fixes; record outcomes in
  `docs/testing/rca-20261112.md`.
- **FR-0050-02 (Eval, CLI & Bridge Hardening — `D-1`..`D-6`)** — Enforce
  `parallelism >= 1` in `EvalSettings` and `eval_cmd` without leaking
  `Orchestrator`; forward `cfg.vector.max_top_k` in `rag query`; enforce IEEE-754
  finiteness and `[-1, 1]` cosine clamping in `embedding`, `json_field`,
  workflow predicates, `tool_policy` (including `type: [...]` unions), and
  `load_baseline`; reject falsy non-dict `options`/`metadata` across
  `composite`, `_eval_config`, `dataset`, `convert_dataset`, and `mango_bridge`;
  validate `mango_bridge` agent slugs and timeouts; preserve `judge_excluded` in
  `merge_gate_results`.
- **FR-0050-03 (Contracts, Harness & Scripts Hardening — `D-7`..`D-11`)** —
  Recurse `reject_authority_shaped_keys` into `@dataclass`, `BaseModel` (fields
  + `model_extra`), and `AbstractSet`; harden `resolve_sandbox_argv`,
  `NoIsolationBackend` (`PYTHONPATH` + `PYTHONDONTWRITEBYTECODE`),
  `DockerIsolationBackend` (`--mount` path check + `--`), `windows_to_wsl_path`
  (`\\?\<Drive>:` + drive-relative rejection), `RepairSettings` identifier
  validation, `validate_loopback_llm_url` port bounds, case-insensitive
  `validate_promotion_target`, canonical `PreToolUse`/`PostToolUse` path
  handling in `lint_agent_frontmatter.py`, and `python -m [scripts.]guard_probe`
  detection in `_destructive_bash.py`.
- **FR-0050-04 (Vertex Default, Telemetry/Config Facade & Optional-SDK Strict Typing — `D-12`..`D-15`)** —
  Update `DEFAULT_VERTEX_TEST_MODEL` (and `docs/testing/vertex-e2e.md`) to
  `gemini-2.5-flash`, import `SecretManagerServiceClient` from
  `google.cloud.secretmanager` in `GCPSecretManagerProvider` so `mypy --strict`
  passes cleanly in both P1 (without `[cloud]`) and P2 (with `[cloud]`), track
  live `sys.stderr` via `_LiveStderrHandler` in `telemetry/tracing.py`, and
  re-export `validate_cli_token` on `mangomas.config`.
- **FR-0050-05 (Red-First Guards, Mutation Proofs & Corpus Sync)** — Guard all
  fixes via four additive red-first test suites (`test_aqa_20261112_defects.py`,
  `test_aqa_20261112_bridge.py`, `test_aqa_20261112_contracts.py`,
  `test_aqa_20261112_scripts.py`), register `MUT-AQA-1112-*` mutations in
  `scripts/guard_probe_manifest.json`, and synchronize stale `.claude/skills/*`
  documentation with `spec-0043..0049`.

## Scenarios (WHEN/THEN)

- WHEN `mangomas eval -p 0` or `MANGOMAS_EVAL__PARALLELISM=0` is invoked THEN it
  exits `2` (`EXIT_CONFIG_ERROR`) without leaking an unclosed `Orchestrator` or
  printing a traceback.
- WHEN `MANGOMAS_VECTOR__MAX_TOP_K=1` is set and `mangomas rag query --top-k 3`
  runs THEN `Retriever` caps results to `1`.
- WHEN `NaN` or `±Inf` reaches `EmbeddingSimilarityScorer`, `JsonFieldScorer`,
  workflow `json_field` predicates, `ToolPolicy` number/union schemas, or
  `load_baseline` THEN it is deterministically rejected or scored `0.0` rather
  than silently passing or poisoning aggregate scores.
- WHEN an authority- or secret-shaped attribute/extra is nested inside a
  `@dataclass`, `BaseModel`, or `set`/`frozenset` value THEN
  `reject_authority_shaped_keys` raises `ValueError`.
- WHEN `mypy --strict` runs on both P1 (`.venv`) and P2 (`.venv-gpu` with
  `google-cloud-secret-manager` installed) THEN both exit `0` with `0` issues.
- WHEN any `MUT-AQA-1112-*` fix is reverted THEN `make guard-probe` fails on its
  paired guard and restores the working tree cleanly.

## Config / env additions

_None._

## Protocol / contract impact

- New/changed protocols: _none_ (`PROTECTED_PATHS` untouched: `0/13` modified)
- New error types: _none_ (reuses `ConfigError`, `ToolExecutionError`,
  `ValueError`)
- Registry additions: _none_

## Backwards-compatibility

- All valid configurations, datasets, workflow definitions, tool schemas,
  container/WSL oracle invocations, and `CognitiveSignal` 1.1.0 envelopes behave
  identically.
- Previously unhandled `ValueError` / `IndexError` / `NaN` edge cases now map to
  their documented typed error or fail-closed score boundaries.

## Test plan

- Regression (`mangomas`): `tests/regression/test_aqa_20261112_defects.py`
- Bridge (`eval_harness_bridge` 100% floor): `tests/eval_harness_bridge/test_aqa_20261112_bridge.py`
- Contracts (`mango_contracts` 100% floor): `tests/mango_contracts/test_aqa_20261112_contracts.py`
- Scripts (`scripts/` >=94% floor): `tests/harness/test_aqa_20261112_scripts.py`
- Telemetry & config facade: `tests/test_telemetry.py`, `tests/test_cli_runtime.py`, `tests/test_import_compat.py`
- Mutation probe: `MUT-AQA-1112-01..12` in `scripts/guard_probe_manifest.json`

## Acceptance criteria

- [x] Baseline and after-fix lane matrices recorded in `docs/testing/rca-20261112.md`.
- [x] All four `test_aqa_20261112_*.py` suites verified RED before fixes and GREEN after fixes.
- [x] `make gate` (P1), `mypy --strict` (P2), `make guard-probe`, GPU, Docker/WSL/Postgres, and live LM Studio + Tier-5 no-mock E2E (`6/6`) pass with all coverage floors met.
