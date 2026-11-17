# Spec-0051: Post-Cutover Main-Parity AQA, Live/GPU/No-Mock E2E & Enterprise Governance Sync

- **Status:** Implemented
- **Linked ADR:** _none — preserves all existing protocols, `PROTECTED_PATHS`, and wire contracts_
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed — Post-cutover origin-delta AQA, main-parity hardening & documentation sync (spec-0051)`

## Problem

After the ADR-0036 cutover (`PR #135` → `#136` → `#137` → `#138`), `origin/dev@45e04fa` (`origin/qa@a45d6cd` and `origin/main@b3f7c76` parity) included the `google.genai` SDK migration (`874aece`), POSIX/Windows isolation hardening (`e0ff510`, `9261e30`), and `eval_harness_bridge` / workflow cleanup (`76fba33`), none of which had been re-verified end to end across live LM Studio, live no-mock TCP/CLI flows, dual CUDA GPUs, containers, and offline GCP telemetry after the final commits.

Running the baseline matrix and main-parity audit (`docs/testing/rca-20261115.md`) surfaced **7 defects / drift items (`D-1`..`D-7`)**:

1. **`D-1` (`Makefile:117`, `tests/constants/deploy.py:94-102`, `deploy/README.md`)**: `IMAGE_SMOKE_IMPORTS`, `PROVIDER_REQUIREMENTS["vertex"].smoke_module`, and the deploy contract table still smoke-import retired `vertexai` inside `mangomas:ci` instead of `google.genai`.
2. **`D-2` (`src/mangomas/api/{health.py,app.py,routes/system.py}`)**: `check_ready` ignores `MANGOMAS_API__READY_TIMEOUT_SECONDS`, causing `GET /readyz` to stall for `8.24s` against an `8s` hung dependency even with `READY_TIMEOUT_SECONDS=1.0`.
3. **`D-3` (`src/mangomas/eval/{sink.py,__init__.py,sinks/*}`, `src/mangomas/cli/commands/{_eval_config.py,eval.py}`)**: `EvalSettings.output_dir` (`MANGOMAS_EVAL__OUTPUT_DIR`) is documented as the artifact directory for `json_file` / `sqlite_results`, yet is never passed to `_build_sinks`, causing `mangomas eval` with those sinks and no explicit `SINK_OPTIONS` path to exit `2`.
4. **`D-4` (`.claude/skills/mango-rag/SKILL.md`, `.claude/agents/mango-rag-dev.md`, `docs/{adapters/vertex.md,architecture/*}`, `NEXT_STEPS.md`)**: Documentation, skill, and agent references still describe retired `vertexai` / `NoTransportAcloseMixin` behavior for Vertex adapters instead of `google.genai.Client` and `close_genai_client`.
5. **`D-5` (`config/api.py`, `api/health.py`, `cli/commands/_eval_config.py`)**: `APISettings.ready_timeout_seconds`, `check_ready`, and `_resolve_gating` lacked fail-closed finite/boolean/NumPy scalar validation (`<= 0`, `bool`, `NaN`, `inf`) and structured `readiness_probe_*` / `readiness_report` event fields; sink constants were duplicated across CLI and sink factories.
6. **`D-6` (`.gitignore`, `.dockerignore`, `.gitleaks.toml`, `Makefile`, `tests/tooling/_corpus.py`)**: Secondary virtualenvs (`venv/`, `.venv-*`) and local `uv` artifacts (`uv.lock`, `.uv/`) were ignored inconsistently across git, Docker build context, `gitleaks`, and repo-walking tests; `Makefile clean` omitted Windows scanner binaries/zips and `rendered-service.yaml`.
7. **`D-7` (`docs/architecture/*`, `docs/eval/harness.md`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `.claude/*`)**: C3/C4 diagrams, observability correlation docs, `README.md` (`/llm/profiles`, `MANGOMAS_SIGNAL__*`, `MANGOMAS_EMBEDDINGS__DEVICE`, `make gate`), `AGENTS.md`, `CLAUDE.md`, and `.claude` agent/skill metadata carried stale floors (`85 %`), omitted `StepTimeout`/`ToolEffects` details, or lacked deterministic enforcement tests.

## Requirements

- WHEN `make docker-smoke` validates `mangomas:ci`, THEN `IMAGE_SMOKE_IMPORTS`, `PROVIDER_REQUIREMENTS["vertex"].smoke_module`, and `deploy/README.md` MUST verify `google.genai`.
- WHEN `MANGOMAS_API__READY_TIMEOUT_SECONDS` is configured (default `2.0`), THEN `APISettings` MUST reject `<= 0`, `NaN`, or `inf`, and `GET /readyz` / `/ready` MUST bound both the LLM `ping()` check and the DB `list_turns(limit=1)` check via `asyncio.wait_for`, returning HTTP `503` (`status="error"`, `"timed out after ...s"`) and structured `readiness_probe_failed` logs if either exceeds the budget. Calling `check_ready(orchestrator)` directly accepts finite positive `numbers.Real` / NumPy scalars, rejects `bool`/`<= 0`/`NaN`/`inf` with `ConfigError`, and stays unbounded when `timeout_seconds=None`.
- WHEN `mangomas eval` runs with `json_file` (without `--output-json`) or `sqlite_results` and `sink_options` omits `"path"` / `"db_path"`, THEN `_build_sinks` MUST default the path cleanly under `EvalSettings.output_dir` via shared immutable `DEFAULT_SINK_ARTIFACTS` (`report.json` / `results.db`), while preserving `--output-json` > `sink_options` > `output_dir` precedence and failing closed (`EXIT_CONFIG_ERROR`) on explicit malformed values, direct `sink_registry` factory calls without options, or `bool`/`NaN`/`inf` gate thresholds (`_resolve_gating`).
- WHEN scanning or packaging the repository, THEN secondary virtualenvs (`.venv-*`, `venv/`) and local `uv` artifacts (`uv.lock`, `.uv/`) MUST be excluded consistently by `.gitignore`, `.dockerignore`, `.gitleaks.toml`, and shared `is_ignored_repo_walk_path`, enforced by deterministic contract tests.
- WHEN inspecting `README.md`, `AGENTS.md`, `CLAUDE.md`, `.claude/agents/*`, `.claude/skills/*`, `docs/adapters/vertex.md`, `docs/architecture/*`, `docs/eval/harness.md`, and `NEXT_STEPS.md`, THEN C4 diagrams, OpenAPI endpoint tables, coverage floors, correlation docs, and Vertex adapter docs MUST match current `origin/main` behavior without citing retired `vertexai.generative_models`, `mangomas.api.correlation`, or stale `85 %` floors.
- Live Vertex AI (`make vertex`) and GCP Secret Manager (`make gcp-secrets`) remain gated offline per operator consent decision (`hrmtrain`), verified via `make gcp-preflight`, `make gcp-trace`, and fake-backed suites.

## Config / env additions

_None_ — activates existing `MANGOMAS_API__READY_TIMEOUT_SECONDS` and `MANGOMAS_EVAL__OUTPUT_DIR` settings cleanly without adding or renaming any env vars.

## Protocol / contract impact

- Protected paths: `0/13` modified ([pyproject.toml](../pyproject.toml#L148-L166) untouched).
- New/changed protocols or errors: _none_.
- Existing signatures preserved: `check_ready(orchestrator, *, timeout_seconds=None)` and `_build_sinks(..., *, output_dir=None)` default-safe.

## Backwards-compatibility

- Callers invoking `check_ready(orch)` directly keep unbounded default (`timeout_seconds=None`).
- Direct calls to `sink_registry.get("json_file")({})` / `sink_registry.get("sqlite_results")({})` and 3-arg `_build_sinks(...)` continue raising `ConfigError` when paths are omitted.
- `--output-json` and explicit `MANGOMAS_EVAL__SINK_OPTIONS` continue overriding `MANGOMAS_EVAL__OUTPUT_DIR`.

## Test plan

- Red-first regression & AQA suite: `tests/regression/test_aqa_20261115_defects.py` (`D-1`..`D-7`, HTTP `/readyz` + `check_ready`, finite/NumPy scalar bounds, CLI `eval` `output_dir`, repo-walk exclusions, docs & C4 parity)
- Test doubles: `FakeLLM.ping_delay_seconds` and `FakeRepository.list_turns_delay_seconds` in `tests/fakes.py`
- Mutation probes: `MUT-AQA-1115-01..04` in `scripts/guard_probe_manifest.json`
- Full verification: `make gate` (P1, `BASE_REF=origin/main` & `origin/dev`), P2 `mypy --strict`, `make gpu` + negative `CUDA_VISIBLE_DEVICES=-1`, `make postgres`, `make docker-smoke`, `make oracle-isolation-docker`, `make oracle-isolation-wsl`, `make gcp-trace`, `make gcp-preflight`, `make lmstudio`, `make studio-probe`, `scripts/run_workflow_e2e.py`, live `15/15` no-mock TCP/CLI E2E rerun, `make guard-probe`, `gitleaks`, `pre-commit`, and read-only `narrow-critic`.

## Acceptance criteria

- [x] Baseline and post-fix results recorded in `docs/testing/rca-20261115.md`.
- [x] `tests/regression/test_aqa_20261115_defects.py` verified RED before fixes and GREEN after fixes.
- [x] `make gate`, P2 `mypy --strict`, `make guard-probe`, dual-GPU, container/isolation, live LM Studio, and live no-mock TCP/CLI E2E pass after final edit with `0/13` protected paths touched and read-only `narrow-critic` findings triaged.
