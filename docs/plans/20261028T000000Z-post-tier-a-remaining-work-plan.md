# Post-Tier-A remaining work — delivery plan

- **Branch:** `feat/initial-release` (each PR from `claude/quirky-bohr-5rub25`, restarted per PR)
- **Date:** 2026-10-28
- **Target release:** rolling (pre-0.5.0)
- **Status:** In progress — Sprint 1 executing; Sprints 2–3 approved backlog
- **Specs:** spec-0038 (Sprint 1); spec-0039 / spec-0040 (Sprint 2, spec-only); spec-0041 (Sprint 3)
- **ADRs:** ADR-0038 (fan-out cancellation, Sprint 2), ADR-0039 (import-linter contracts, Sprint 1)

## Executive summary

PR #90 merged the A2A decision records and the ten Tier A branch-audit groups.
A panel-style second look (SDLC / product / scrum / AI-ML / security) at the
merged tree found two deploy **blockers** — the production image did not
install the extras the reference manifest selects, and the manifest selects
providers without their required settings — plus a set of wiring gaps
(undeclared dependencies, unread settings, a readiness probe with no budget,
`make gate` narrower than CI, stale corpus citations) and a reliability
backlog (fan-out cancellation, pipeline metadata loss, deterministic eval
gate, pass^k). Sprint 1 ships the deploy unblock first (P0-A → P0-B) because
it is the only tier that changes what reaches production; everything after it
is additive behind defaults or pure guards. Every change to the orchestrator
client is serialised (B2 → B3 → B1) to avoid three-way conflicts on the
protected file.

Decisions taken by the user on 2026-10-27: execute Sprint 1 only now; the
reference manifest gets safe defaults (auth on via secret ref, bounded body
and concurrency); approved for the backlog: fan-out cancels siblings, a
ToolAgent lenient fenced-JSON knob (default off), OpenAPI hidden outside
`local`, a RAG source-prefix knob (default empty); **deferred**: orchestrator
package decomposition and `sitecustomize.py` deletion.

## Conventions binding every PR

Additive, default-OFF; `BREAKING-CHANGE: <exact path> - <why>` trailer per
protected path; every new guard mutation-proved (break → named test red →
restore, named in the PR body); domain literals in `tests.constants`; no
mocking of internal protocols; `make gate` + `make gated-suites` green; one
`CHANGELOG.md [Unreleased]` block per PR; ≤ ~600 changed lines; docs updated
in the same PR; specs/ADRs from the templates with the `specs/README.md` row
and "next free" line.

## PR P0-A — deploy: cloud image closure (spec-0038) ✅

### Milestone A0 — one lock, compiled with the image's extras ✅

- **Failing test first:** `test_cloud_extra_distributions_are_pinned_in_the_lockfile`
  (red on the base lock: 0 of the cloud SDKs pinned).
- **Depends on:** nothing.
- `pyproject.toml`: `gcp-monitoring` extra (alpha-only upstream, excluded
  from `cloud`); `requirements.lock.header` tracked; Makefile `lock`
  (additive, `-c` the current lock) and `lock-upgrade`; `LOCK_NORMALIZE`
  strips the self-reference and makes the output a fixed point.
- Measured: additive regeneration adds 36 pins (32 → 68), moves none;
  pip-audit clean on the result.

### Milestone A1 — image installs `mangomas[cloud]` ✅

- **Failing test first:** `test_runtime_install_defaults_to_the_cloud_extras`.
- **Depends on:** A0 (the install resolves through the lock).
- `Dockerfile` `ARG MANGOMAS_EXTRAS=cloud` expanded on the install line;
  Dependabot `docker` ecosystem; exporter install hint names the new extra.
- Proof without a Docker daemon: fresh Python 3.11 venv, both wheels built,
  installed with the Dockerfile's exact line through `-c requirements.lock`;
  `vertexai`, `asyncpg`, `google.cloud.secretmanager`,
  `opentelemetry.exporter.cloud_trace`, `mango_contracts` and `create_app`
  import; `cloud_monitoring` absent by design; `pip check` clean.

## PR P0-B — deploy: manifest contract, safe defaults, docker smoke job (spec-0038) ✅

### Milestone B0 — manifest carries what its providers need ✅

- **Failing test first:** `test_each_selected_provider_has_its_required_settings`
  (red: no `MANGOMAS_DB__URL`, `LLM__MODEL`, `LLM__PROJECT_ID`).
- **Depends on:** P0-A (extras expansion reused from `tests/deploy/_dockerfile.py`).
- `deploy/service.yaml`: `DB__URL` via `secretKeyRef mangomas-db-url`,
  `LLM__MODEL: ${LLM_MODEL}`, `LLM__PROJECT_ID: ${PROJECT_ID}`,
  `SECRETS__PROJECT_ID: ${PROJECT_ID}` (found while writing the rows: the gcp
  secrets provider refuses to build without it); safe defaults
  `AUTH__ENABLED=true`, `AUTH__SECRET_REF=mangomas-api-token`,
  `API__MAX_BODY_BYTES=1048576`, `API__MAX_CONCURRENT_REQUESTS=80`
  (= `containerConcurrency`, not 64: the app must never refuse what Cloud Run
  routes);
  per-environment overlays in `deploy/environments.yaml`;
  `DEPLOY_RENDER_ENV_VARS += LLM_MODEL`; `deploy.yml` `LLM_MODEL: ${{ vars.VERTEX_MODEL }}`.
- `tests/constants/deploy.py::PROVIDER_REQUIREMENTS`;
  `tests/deploy/test_manifest_provider_contract.py` (six named tests over
  every rendered environment).

### Milestone B1 — CI builds the image and imports each provider's SDK ✅

- **Failing test first:** `test_docker_smoke_job_delegates_to_make`.
- **Depends on:** B0.
- Makefile `docker-build` / `docker-smoke` with `IMAGE_SMOKE_IMPORTS`;
  `ci.yml` `docker-smoke` job (`timeout-minutes: 20`); `deploy/README.md`
  provider → extra → required-settings table; admin prerequisites noted
  (`vars.VERTEX_MODEL`, the `mangomas-db-url*` / `mangomas-api-token*` secrets).

## PR P0-C1 — wiring: lazy contracts import, readiness budget, dead knob, env example

- **Failing test first:** `test_flag_off_never_imports_contracts`;
  `test_readiness_probe_honours_the_timeout_budget`;
  `test_env_example_commented_values_match_model_defaults`.
- **Depends on:** nothing — parallel-safe after P0-A.
- `cognitive/producer.py` + `pdp.py`: `mango_contracts` lazy on the flag-on
  path, `ImportError` → `ConfigError`; `api/health.py::check_ready(...,
  timeout_seconds=)` under `asyncio.wait_for`, wired from
  `api.ready_timeout_seconds`; delete `LogSettings.body_truncate`;
  `.env.example` commented values corrected and contract-tested.

## PR P0-C2 — deps: declare starlette and pre-commit; `mangomas serve`

- **Failing test first:** `test_serve_passes_settings_host_and_port`.
- **Depends on:** nothing — parallel-safe after P0-A.
- `[project]` += `starlette>=0.40`; `[dev]` += `pre-commit>=3.7`;
  `cli/commands/serve.py` reading `api.host` / `api.port`; Makefile `serve`
  uses it. Trailer: `BREAKING-CHANGE: pyproject.toml - declare starlette and pre-commit`.

## PR P0-D1 — ci: timeouts, concurrency, gate == CI, deploy verify

- **Failing test first:** `test_every_job_has_a_timeout`,
  `test_every_workflow_declares_a_concurrency_group`, `test_gate_includes_gated_suites`.
- **Depends on:** nothing — parallel-safe after P0-A.

## PR P0-D2 — corpus: pr-watcher body, src path guard, harness skill, hook paths

- **Failing test first:** `test_read_only_agents_do_not_instruct_write_actions`;
  `_CITED_SRC_FILE_RE` guard (red on six stale citations);
  `test_relative_script_hook_is_reported`.
- **Depends on:** nothing — parallel-safe after P0-A.

## PR P1-B2 — orchestrator: pipeline stages carry request metadata and max_steps

- **Failing test first:** `test_pipeline_threads_request_metadata_through_every_stage`.
- **Depends on:** nothing; but every later `_client.py` change serialises after it.
- `core/loop.py::next_stage_request`; used by `_pipeline_pass` and
  `workflow/nodes/sequence.py`. Trailer on `src/mangomas/core/orchestrator/_client.py`.

## PR P1-B4 — layering: import-linter contracts (ADR-0039)

- **Failing test first:** `test_declared_contracts_match_the_expected_set`.
- **Depends on:** nothing — parallel-safe after P0-A.
- Four `forbidden` contracts (all hold today); `AGENTS.md` design-rules row
  → "`make lint-imports`". Trailer on `pyproject.toml`.

## Sprint 2 backlog (approved, not scheduled)

P1-B3 metrics for non-Mangomas exceptions + per-chunk span attach/detach in
`_stream_agent`; P1-B1 `core/concurrency.py::gather_fail_fast` (TaskGroup;
ADR-0038); P1-B5 `AgentSettings.lenient_tool_parse` (default off); P2-A
`WorkflowSettings.max_depth`; P2-B `ApiSettings.expose_openapi` (None ⇒ only
when `MANGOMAS_ENV=local`); P3-A deterministic `echo` eval gate in CI with a
checked-in baseline, plus spec-0039 (pass^k) and spec-0040 (judge calibration)
written spec-only; P5-B status-drift sweep with numbering guards.

## Sprint 3 backlog

P3-B pass^k implementation; P4-B `postgres.py::_insert_turn`; P4-C
`SCRIPTS_TESTS` completeness and floor re-measure; P5-A skills
`mango-dependency-audit` / `mango-spec`; P5-C spec-0041 turn-record identity
columns; P1-B6 `RagSettings.source_prefix`.

## Deferred / out of scope

- Orchestrator package decomposition (`_client/_pipeline/_fan_out/_stream`):
  deferred by user decision 2026-10-27; re-opening needs ADR-0040, the
  governance list plus its two lock-step copies, and four trailers.
- `sitecustomize.py` deletion: deferred by the same decision.
- Admin items (user's side): GCP project, GitHub Environments and
  `vars.VERTEX_MODEL`, the per-environment `mangomas-db-url*` /
  `mangomas-api-token*` secrets, `dev/qa/main` branch creation, required
  status checks, the 0.4.0 release tag, and the duplicate `## [0.4.0]`
  CHANGELOG section question (raised, not resolved).

## Verification

```bash
make gate && make gated-suites && make frontmatter && make pip-audit
make lock && git diff --exit-code requirements.lock        # lock is fresh (one run)
make docker-smoke                                          # from P0-B onward
PROJECT_ID=x LLM_MODEL=y make deploy-render ENVIRONMENT=dev   # and qa; inspect refs
python -m pytest tests/tooling tests/deploy -q
git log origin/feat/initial-release..HEAD --format=%B | grep BREAKING-CHANGE
```
