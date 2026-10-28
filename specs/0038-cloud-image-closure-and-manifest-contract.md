# Spec-0038: Cloud image closure and manifest provider contract

- **Status:** Implemented — P0-A (image closure) and P0-B (manifest contract)
- **Linked ADR:** _none — no boundary change_ (deploy/build surface only)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed — production image installs the cloud extras it needs`

## Problem

The reference manifest (`deploy/service.yaml`) selects the Vertex LLM, Cloud
SQL Postgres, Secret Manager and Cloud Trace providers, but the production
image installed the base wheel only: `requirements.lock` pinned the base
closure and the `Dockerfile` passed no extras. Every one of those adapters
lazy-imports its SDK, so the image **built and booted** and failed on the
first real request with an `ImportError`. Nothing in CI builds the image, and
nothing cross-checks the manifest's env names or the providers it selects
against `Settings` or against the extras the image carries. Two deliveries
close this: **P0-A** makes the image install what the manifest needs and makes
the lock cover it (this spec's implemented half); **P0-B** makes the manifest
self-consistent and adds the CI smoke job (pending half).

## Requirements

- R1 — The runtime image installs `mangomas[cloud]` by default
  (`ARG MANGOMAS_EXTRAS=cloud`); `--build-arg MANGOMAS_EXTRAS=` (empty)
  yields the former slim image. The install line expands the arg into the
  wheel's extras and still passes `-c requirements.lock`.
- R2 — `requirements.lock` pins the closure of `[project].dependencies` **plus**
  every extra the image installs. One lock, not a second file, so the existing
  `-c` wiring, `make pip-audit -r` and the freshness contract all cover the
  cloud SDKs with no new plumbing.
- R3 — Regeneration is a Makefile target, not a comment: `make lock` is
  **additive** (compiles with the current lock as `-c`, so base pins never move
  as a side effect of adding an extra), `make lock-upgrade` is the one
  deliberate way to move every pin. Both refuse to run off Python 3.11 (the
  image's interpreter) and prepend the tracked `requirements.lock.header`.
- R4 — `make lock` output is a fixed point: running it on a fresh lock changes
  nothing, so `make lock && git diff --exit-code requirements.lock` proves
  freshness in one run.
- R5 — The lock never names the project itself (the self-referential `cloud`
  extra makes pip-compile emit `mangomas @ file://…`; as a constraint that
  would pin the wheel to a path absent from the image).
- R6 — `opentelemetry-exporter-gcp-monitoring` has only ever published
  alpha releases, so it lives in its own `gcp-monitoring` extra with a
  pre-release floor and is **not** part of `cloud`; `METRICS_ENABLED` stays off
  in the manifest. The runtime install hint names that extra.
- R7 (P0-B) — The manifest carries every provider-required setting
  (`DB__URL` via secret ref, `LLM__MODEL`, `LLM__PROJECT_ID`, and
  `SECRETS__PROJECT_ID`, which the gcp secrets provider refuses to build
  without — found while writing the rows), enables auth and bounded
  body/concurrency by default, and a contract test over every rendered
  environment ties selected providers → image extras → required settings →
  `Settings` field names. A CI `docker-smoke` job builds the image and imports
  each selected provider's SDK. `${...}` values in the base manifest are
  render placeholders every overlay must restate (the base is never
  substituted); a rendered manifest may not contain one.
- Must remain **additive & default-OFF**: the application code path is
  untouched; a slim image is one build arg away; no `MANGOMAS_*` default
  changes in P0-A.

## Scenarios (WHEN/THEN)

- WHEN a distribution that `mangomas[cloud]` installs is absent from the lock
  THEN `test_cloud_extra_distributions_are_pinned_in_the_lockfile` fails
  (proved: dropping the `asyncpg==` line → red); WHEN every one is present THEN
  it passes and the denominator test proves the meta-extra actually expanded.
- WHEN the lock does not begin with `requirements.lock.header` THEN
  `test_lockfile_starts_with_the_tracked_header` fails (proved: delete line 1).
- WHEN a `mangomas…` line reaches the lock THEN
  `test_lockfile_never_names_the_project_itself` fails (proved: append
  `mangomas==0.4.0`).
- WHEN the Dockerfile's `ARG MANGOMAS_EXTRAS` default is emptied or the install
  line stops expanding it THEN `test_runtime_install_defaults_to_the_cloud_extras`
  fails (proved); WHEN the ARG is missing entirely THEN the parser raises
  rather than returning `""`.
- WHEN `make lock` loses its `-c $(RUNTIME_LOCKFILE)` or `LOCK_EXTRAS` no longer
  names the image's extras THEN `test_lock_target_compiles_the_cloud_extra` /
  `test_lock_extras_cover_the_image_default_extras` fail (proved, both).
- WHEN the manifest selects a provider whose required setting is absent (the
  `MANGOMAS_DB__URL` secretKeyRef deleted) THEN
  `test_each_selected_provider_has_its_required_settings` fails (proved); WHEN
  an env name is not a `Settings` field (`MANGOMAS_LLM__MODL`) THEN
  `test_every_manifest_env_name_resolves_to_a_settings_field` fails (proved);
  WHEN `AUTH__ENABLED` is `false` THEN
  `test_reference_manifest_enables_auth_and_backpressure` fails (proved); WHEN
  an overlay forgets to restate a placeholder THEN
  `test_rendered_manifests_contain_no_unfilled_placeholders` fails (proved);
  WHEN `postgres` leaves the `cloud` meta-extra THEN
  `test_each_selected_provider_has_its_extra_in_the_image` fails (proved);
  WHEN `asyncpg` leaves `IMAGE_SMOKE_IMPORTS` THEN
  `test_smoke_import_list_covers_every_selected_provider` fails (proved); WHEN
  the `docker-smoke` job loses its timeout THEN
  `test_docker_smoke_job_delegates_to_make` fails (proved); WHEN dev reuses
  production's token ref THEN
  `test_non_production_environments_use_their_own_auth_token_ref` fails
  (proved). Each passes on the shipped tree, and the denominator test proves
  the base manifest selects at least three provider rows.
- WHEN every concurrency slot is held THEN a request to any `PROBE_PATHS`
  route is still served 200 while a data route is 503
  (`test_saturated_limiter_still_serves_probe_paths`; proved: dropping the
  exemption branch → red for all four paths); WHEN the guard is built without
  `exempt_paths` THEN probes are capped like everything else (the pre-existing
  contract, pinned).
- WHEN the Postgres factory logs at DEBUG with a password-bearing DSN THEN no
  record carries the URL or the password, only `dsn_host`
  (`test_postgres_factory_never_logs_the_dsn_password`; proved: restoring
  `extra={"url": cfg.url}` → red).

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| _none_ | | Build-time knobs only: Dockerfile `ARG MANGOMAS_EXTRAS=cloud`; Makefile `LOCK_EXTRAS ?= cloud`, `PIP_TOOLS_VERSION ?= 7.6.2`, `LOCK_PYTHON ?= $(PYTHON)`, `LOCK_PYTHON_MINOR ?= 3.11`, `LOCKFILE_HEADER ?= requirements.lock.header` |

## Protocol / contract impact

- New/changed protocols: _none_.
- New error types: _none_.
- Registry additions: _none_. New packaging extra `gcp-monitoring`.

## Backwards-compatibility

- Application behaviour is byte-identical; only the image contents and the
  lockfile grow. Every pre-existing base pin is unchanged (verified: the
  regeneration diff removes no `==` line; 32 → 68 pins).
- `pip install mangomas[gcp]` no longer implies the Cloud Monitoring exporter
  (it never did — the package was in no extra); `mangomas[gcp-monitoring]` is
  the new opt-in. The install hint in `telemetry/exporters.py` says so.
- The lock's in-file regeneration instructions (a hand-typed `pip-compile`
  command) are replaced by `make lock`; the old command still produces a
  valid base lock but would drop the cloud pins and the header — both now
  caught by tests.

## Test plan

- Unit: `tests/deploy/test_lockfile_freshness.py` (cloud membership, header,
  self-reference, meta-extra expansion incl. cycle termination and
  unknown-extra rejection), `tests/deploy/test_docker_build_context.py`
  (ARG default + expansion; two mutation proofs), `tests/deploy/test_ci_make_parity.py`
  (`lock` / `lock-upgrade` shape, pip-tools pin, Python-minor guard tied to
  the Dockerfile base tag). Shared readers: `tests/deploy/_dockerfile.py` (new),
  `tests/deploy/_makefile.py`.
- Gates: every guard above was broken and observed red on the named test, then
  restored (mango-mutation-proof).
- Image proof: `docker build . && docker run --rm <img> python -c "import vertexai, asyncpg, google.cloud.secretmanager, opentelemetry.exporter.cloud_trace, mango_contracts"`
  — automated by P0-B's `docker-smoke` job; until then reproduced locally as a
  fresh-venv install of the built wheels through `-c requirements.lock`.
- Coverage: no `src/` change beyond a string; floors unaffected.

## Acceptance criteria

- [x] Slim image still buildable (`--build-arg MANGOMAS_EXTRAS=`), default is `cloud`.
- [x] `make lock` is additive, idempotent, header-prefixed, self-reference-free; `make lock-upgrade` exists.
- [x] Lock covers the `cloud` closure; pip-audit of the result is clean.
- [x] `ruff`, `mypy`, `pytest` (95 % gate), `frontmatter-lint` clean.
- [x] CHANGELOG updated; Dependabot `docker` ecosystem added.
- [x] P0-B: manifest carries DB URL / model / project ids, auth + backpressure on, provider contract test, `docker-smoke` CI job, deploy README table.
