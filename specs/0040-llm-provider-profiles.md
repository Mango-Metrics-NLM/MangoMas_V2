# Spec-0040: LLM provider profiles and the Studio boundary

- **Status:** Implemented
- **Linked ADR:** ADR-0038 (new boundary: a second deployment surface consumes
  the API; provider selection moves from one static setting to a built roster)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — LLM provider profiles (spec-0040 / ADR-0038)`

## Problem

`LLMSettings` admits exactly one provider per process. A MangoMas Studio user
who wants a local model to draft and a cloud model to review needs two
processes. Per-agent `MODEL_OVERRIDE` (spec-0028) changes the model but not
the provider, and a header-driven switch would resolve providers outside the
composition root (`composition/CLAUDE.md` §Boundaries). Outcome: **named
profiles**, built once at startup under the existing roster control,
selectable per run through a dict that already exists on the wire, with a
fail-at-startup local-only mode. This is the one MangoMas backend change
Studio v1 needs; the UI itself needs nothing further here beyond the existing
CORS allow-list.

## Requirements

- **R1 — Profiles are built at startup, in the composition root.**
  `MANGOMAS_LLM_PROFILES__<NAME>__*` declares a profile
  (`config/llm.py::LLMProfileSettings`; `Settings.llm_profiles: dict[str,
  LLMProfileSettings]`). `provider` and `model` are required; every other
  field (`base_url`, `api_key`, `secret_ref`, `project_id`, `location`,
  `credentials_path`, `timeout_seconds`, `temperature`) is `None` by default
  and **inherits** the matching `MANGOMAS_LLM__*` value when unset. Credential
  fields (`api_key`, `secret_ref`, `credentials_path`) inherit only when the
  profile's provider equals the base provider; a profile that sets `api_key`
  without `secret_ref` also clears any inherited ref. Every profile client is
  built by `composition/llm.py::build_llm_profiles` (merge → secret
  resolution → `llm_registry` factory) and placed in
  `ctx.extras["llm_profiles"]`; a secret-free `{provider, model}` summary is
  placed in `ctx.extras["llm_profile_info"]`. No provider is resolved after
  startup. Profile names are lowercased by pydantic-settings and matched
  exactly.
- **R2 — Selection rides `AgentRequest.metadata["llm_profile"]`**, the
  free-form dict that already exists, is already persisted as JSON, and
  reaches the CLI and workflow paths as well as HTTP. No DTO change. The pure
  selector `agents/_prompt.py::select_llm_profile(extras, metadata)` returns
  the name or `None` and raises `ConfigError` on an unknown or non-string
  name. The invoke, stream and `workflows/run` route handlers call it
  **before** dispatch, so an unknown name is a 400 before the SSE headers go
  out. `resolve_llm(ctx, agent_name, request=None)` gains the request and
  applies the precedence **profile (request-scoped, explicit) >
  `MODEL_OVERRIDE` (deployment default) > `ctx.llm`**, logging
  `event="llm_profile_shadows_override"` when a profile shadows an override.
  The CLI exposes the same selector as `--profile` on `chat` and
  `workflow run`. Not a header, not a DTO field.
- **R3 — Roster.** A profile's `model` must appear in
  `MANGOMAS_LLM__ALLOWED_MODELS` when that roster is non-empty; otherwise
  `Settings` parse fails naming the profile (ADR-0033's rule, extended, in a
  root-level `Settings` validator — the first one).
- **R4 — `MANGOMAS_LLM__PROVIDER_ALLOWLIST`** (default `[]` = unconstrained)
  is enforced at `Settings` parse: when non-empty, the base `LLMSettings`
  (group validator) and every profile (root validator) must name a listed
  provider or parse fails naming the offender. `["lmstudio"]` is the
  local-only configuration. A disallowed profile is never built, so no
  request-time check exists to bypass. No loopback test: `docker-compose.yml`
  already points at `host.docker.internal`. The name is scoped to the LLM
  seam on purpose; secrets, embeddings and telemetry keep their own settings
  (ADR-0033).
- **R5 — Observability.** The selected name is written to
  `response.metadata["llm_profile"]` by `_LLMProfileDispatchMixin`
  (`composition/llm.py`), which sits **first** in the base list of both
  composition orchestrators and pre-validates before `super().dispatch`. The
  turn record already persists `metadata` as JSON (`TURN_SCHEMA_VERSION`
  stays 2). Because `_pipeline_pass` seeds each stage with
  `response.metadata`, the stamp is also what carries a profile across every
  `dispatch_pipeline` stage — a documented behaviour. Streams have no response
  object, so the SSE `metadata` frame carries `llm_profile` instead, only
  when selected. The `harness.agent_invoke` span gains `llm.profile` on both
  the dispatch and stream paths, only when selected.
- **R6 — `GET /llm/profiles`** (`api/routes/llm.py`, under `require_auth`)
  returns names, providers and models only, projected from
  `ctx.extras["llm_profile_info"]`. Never a key, a `secret_ref` value or a
  `base_url`. A hand-assembled orchestrator with no extras answers `[]`.
- **R7 — Must remain additive & default-OFF.** With no
  `MANGOMAS_LLM_PROFILES__*` set and no `metadata["llm_profile"]`, dispatch
  is byte-identical to today and the existing suite passes unmodified. The
  OpenAPI snapshot changes by exactly one added path plus its two DTOs; that
  diff is the reviewed contract change.

## Scenarios (WHEN/THEN)

Each gate is stated in both directions (`mango-mutation-proof`); the test
that transcribes it is named.

- WHEN `metadata["llm_profile"]="fast"` names a built profile THEN the run
  uses that client and `response.metadata["llm_profile"]` is `"fast"`
  (`tests/test_llm_profiles_api.py::test_invoke_with_profile_uses_profile_client_and_stamps_response_metadata`;
  `tests/composition/test_llm_profiles.py::test_build_orchestrator_dispatch_routes_to_the_profile_client`).
  WHEN it names no profile THEN the route handler returns `ConfigError` → 400
  before dispatch, and on the stream route before any `text/event-stream`
  header — the test asserts the status and JSON content type, not an SSE
  error frame
  (`tests/test_streaming.py::test_stream_unknown_profile_is_400_before_sse_headers`;
  mutation: move the check after `stream_dispatch`).
- WHEN `PROVIDER_ALLOWLIST=["lmstudio"]` and a profile has `provider="vertex"`
  THEN startup fails with a message naming the profile
  (`tests/test_config.py::test_profile_provider_outside_allowlist_is_refused_naming_the_profile`;
  env path: `tests/test_boundary_honesty.py::test_an_env_provider_allowlist_that_excludes_a_profile_is_refused`).
  WHEN every profile is `lmstudio`, including one at
  `http://host.docker.internal:1234/v1` THEN startup succeeds
  (`test_profile_provider_inside_allowlist_passes`).
- WHEN `ALLOWED_MODELS` is non-empty and a profile's `model` is absent THEN
  startup fails naming the profile
  (`test_profile_model_outside_allowed_models_is_refused_naming_the_profile`).
  WHEN present THEN the profile builds (`test_profile_model_inside_allowed_models_passes`).
- WHEN both a per-agent `MODEL_OVERRIDE` and a profile apply THEN the profile
  wins and the shadowed override is logged
  (`tests/agents/test_prompt.py::test_resolve_llm_profile_beats_agent_override`,
  `::test_resolve_llm_profile_shadow_is_logged`). WHEN only the override
  applies THEN spec-0028 behaviour is unchanged (existing tests untouched,
  plus `::test_resolve_llm_request_without_profile_falls_through_to_override`).
  WHEN the precedence is swapped THEN the first test goes red and the second
  stays green (the pair discriminates ordering, not lookup).
- WHEN `GET /llm/profiles` is called THEN the body matches the OpenAPI
  snapshot and contains no `api_key`, `secret_ref`, `base_url` or
  `credentials_path` token, and no sentinel planted on the client or in an
  extra summary field reaches the wire
  (`tests/test_llm_profiles_api.py::test_get_llm_profiles_body_never_contains_secrets`).
- WHEN an unknown profile reaches `dispatch` directly THEN `ConfigError` is
  raised before any agent runs and **no failed-turn row is recorded** — not a
  turn, by the position rule `composition/recording.py` already applies
  (`tests/composition/test_llm_profiles.py::test_dispatch_unknown_profile_raises_before_any_agent_call_and_records_no_row`;
  mutation: list the mixin after `_FailureRecordingMixin`). Consequence,
  stated: such a refusal emits no `record_agent_error` metric, consistent with
  a pre-stream `AgentNotFound`.
- WHEN no profile env is set and no `metadata["llm_profile"]` is sent THEN
  every existing dispatch test passes without modification, the streaming
  byte-identical snapshot test is untouched, and the
  `tests/test_openapi_snapshot.py` diff is exactly one added path plus
  `LLMProfileSummary` / `LLMProfilesResponse`.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_LLM_PROFILES__<NAME>__PROVIDER` | _(required)_ | Registry entry for this profile (`lmstudio` \| `vertex`) |
| `MANGOMAS_LLM_PROFILES__<NAME>__MODEL` | _(required)_ | Model id; subject to `ALLOWED_MODELS` |
| `MANGOMAS_LLM_PROFILES__<NAME>__BASE_URL` / `API_KEY` / `SECRET_REF` / `PROJECT_ID` / `LOCATION` / `CREDENTIALS_PATH` / `TIMEOUT_SECONDS` / `TEMPERATURE` | _(none)_ — inherit `MANGOMAS_LLM__*` | Same semantics as the base settings; credentials inherit only for a same-provider profile |
| `MANGOMAS_LLM__PROVIDER_ALLOWLIST` | `[]` | Root validator: when non-empty, every profile and the base LLM must name a listed provider; `["lmstudio"]` = local-only. `MANGOMAS_LLM_PROFILES` itself defaults to `{}` (feature off) |
| `MANGOMAS_API__CORS_ALLOW_ORIGINS` | `[]` (existing) | Studio origin must be listed, or Studio is served same-origin |

All tunables are `DEFAULT_*` module constants surfaced through a `Settings`
group (`DEFAULT_LLM_PROVIDER_ALLOWLIST`; the profile group has no defaults
beyond inheritance) — **no hard-coded values**.

## Protocol / contract impact

- New/changed protocols: none. `LLMClient` is unchanged; profiles are more
  instances of it.
- New error types: none. Unknown profile reuses `ConfigError` (400). Startup
  refusals reuse pydantic `ValidationError` at parse.
- Registry additions: none. `llm_registry` is reused per profile.
- DTO change: **none on the agent routes.** The invoke and stream bodies are
  the core `AgentRequest` itself (`api/routes/agents.py`), so a new field
  there would be a protected-path edit; the selector rides the existing
  `metadata` dict instead. One new route `GET /llm/profiles` under
  `require_auth` with two new api-layer DTOs (`api/models.py`). `core/` is
  not touched, so no `BREAKING-CHANGE` trailer is needed.
- `agents/_prompt.py::resolve_llm` gains an optional third argument; the
  five built-in callers pass `request`. A plugin agent that keeps the
  two-argument call keeps spec-0028 behaviour and never sees a profile.

## Backwards-compatibility

- With no profile configured and `llm_profile` absent,
  `ctx.extras["llm_profiles"]` and `["llm_profile_info"]` are `{}`,
  `resolve_llm` takes its existing path, the dispatch mixin returns the
  response untouched, the SSE metadata frame is byte-identical, the harness
  span carries no `llm.profile`, and the OpenAPI snapshot differs only by the
  added route and its DTOs.
- The `tests/deploy/_settings_names.py` walk is generalised from the single
  hand-named `AGENTS` dict to every `dict[str, BaseModel]` group on
  `Settings`, so the per-profile rows pass the env-contract in both
  directions without a new special case.
- No deprecation.

## Test plan

- Unit: `tests/test_config.py` (defaults, env round-trip, required fields,
  R3/R4 pairs), `tests/test_boundary_honesty.py` (env-path pairs),
  `tests/agents/test_prompt.py` (selector, precedence, shadow log, every
  built-in agent on the profile client, streaming agents),
  `tests/composition/test_llm_profiles.py` (build, inheritance, credential
  rule, secret resolution, cross-provider factory, extras, close path on both
  orchestrators, mixin stamp / no-stamp / position rule / MRO, pipeline
  propagation), `tests/composition/test_harness.py` (span attribute both
  ways), `tests/test_llm_profiles_api.py` (R6, auth, invoke pairs),
  `tests/test_streaming.py` (400 before headers, frame both ways),
  `tests/test_workflow_api.py`, `tests/test_cli.py`, `tests/test_workflow_cli.py`.
  Fakes: `FakeLLM` reused, one per profile; `FakeSecretsProvider`.
- Gated (not added here): `RUN_LMSTUDIO=1` two-profile run against one LM
  Studio server with two loaded models; `RUN_VERTEX=1` for a mixed
  local+cloud run — the M2/G2 e2e in the delivery plan.
- Gates: both directions per scenario, proven the way `mango-mutation-proof`
  prescribes (eleven mutations recorded on the PR). `make guard-probe`
  (spec-0033, Implemented; not part of `make gate`) can replay them: adding
  these entries to `scripts/guard_probe_manifest.json` is a follow-up, not a
  dependency.
- Coverage: `composition`, `agents`, `api`, `config` stay at their 95 %
  floors; `core` is untouched at 100 %.

## Acceptance criteria

- [x] Feature off by default → no behaviour change (snapshot + existing suite prove it).
- [x] Feature on via env → documented behaviour (the scenarios above, both directions).
- [x] `ruff`, `mypy --strict`, `pytest` (95 % gate), `frontmatter-lint`, `lint-imports` all clean; `make gate` green.
- [x] CHANGELOG updated; ADR-0038 added; `AGENTS.md` configuration table extended (`test_env_example_contract.py` enforces both directions).
