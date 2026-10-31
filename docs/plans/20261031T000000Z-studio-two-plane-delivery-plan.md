# MangoMas Studio as a two-plane consumer — delivery plan

- **Branch:** `claude/exciting-hypatia-d4hmpy` (MangoMas_V2 PR A); Studio and
  harness work in their own repositories
- **Date:** 2026-10-31
- **Target release:** rolling (pre-0.5.0)
- **Status:** In progress — PR A executing; M2–M4 are backlog pending the
  open decisions below
- **Specs:** spec-0040
- **ADRs:** ADR-0038

## Executive summary

MangoMas Studio is a React/TS front end over two services: MangoMas_V2 (the
cognitive plane) and Mango_Code_Agent-Harness (the authority plane). This
plan sequences five milestones across three repositories, but only **one
backend change in this repository is needed for Studio v1**: named LLM
provider profiles (spec-0040), so a user can draft on a local model and
review on a cloud one without two processes. Everything after gate G2 is
about closing the propose → dispose loop, which no UI can do until the
harness ingests CognitiveSignal 1.1.0 and someone owns identity. The one
constraint that shaped the order: approvals need a principal, neither plane
has one, and the harness's `human_approved` flag has zero callers by design
(DEC-065) — so Studio v1 ships **no approval inbox**, and identity is its own
decision with its own owner.

## Findings that changed the plan (verified against this tree, 2026-10-31)

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | `human_approved` is the one PDP argument that turns a high-risk DENY into ALLOW, and DEC-065 machine-checks that 0 first-party modules supply it | harness `policy_decision.py`, `broker.py`, `authority_call_sites.py`, `docs/decisions/DEC-065.md` | An approval inbox is a harness DEC, not a UI feature |
| F2 | Both planes authenticate with one shared key and no principal | `src/mangomas/api/auth.py`; harness `api_server/main.py` | Identity must be designed before any approval UI |
| F3 | CognitiveSignal wire break: this runtime emits **1.1.0** and rejects 1.0.0; the harness accepts **only 1.0.0** and has no ingest route or file reader for external signals | `config/signal.py:24` (`Literal["1.1.0"]`); `cognitive/constants.py:56` (`signals.jsonl`); harness `cognitive_signal.py` | A signals view has nothing to show until the harness bumps (D-6) |
| F4 | The harness already ships a dashboard (`api_server/static/`) | harness `api_server/` | Studio is the third UI; the README identity note must name all three |
| F6 (corrected) | No `dev` or `qa` branch exists; the default branch is `feat/initial-release` (`a98b626`); a **legacy `main` exists** at `beb01bf` and has diverged (ADR-0036 tags it `archive/main-legacy`) | `git ls-remote --heads origin`; `git remote show origin` | D-10 is a maintainer task, below; the earlier "no `main`" claim was wrong |
| F7 | `make guard-probe` landed with PR #103 (spec-0033 Implemented; `scripts/guard_probe.py` + `scripts/guard_probe_manifest.json`, not part of `make gate`) after this plan was drafted | `Makefile:232`; `specs/0033` | The fourteen spec-0040 mutation proofs are manifest entries `MUT-0040-M01…M14`, replayed on every PR ✅ |
| F8 | `resolve_llm(ctx, agent_name)` took no request; `build_llm` does not exist (the client is built inline in `builder.py`); `Settings` had no root validator; `AgentSettings` has no validator | `agents/_prompt.py`, `composition/builder.py`, `config/_root.py`, `config/agents.py` | spec-0040 R2 passes `request` as an optional third argument; R3/R4 add the first root validator |
| F9 | Pipelines forward `response.metadata` into the next stage's request | `core/orchestrator/_client.py` `_pipeline_pass` | Stamping the profile on the response carries it across a pipeline; documented as behaviour |
| F10 | Eval cost is declared in the dataset, never measured; `LLMClient.complete` returns no usage | `eval/scorers/cost_budget.py`; `adapters/llm/base.py`; 2026-09-19 replan N3 | A cost tab needs D-7 first |
| F11 | Numbering: while this PR was open, PR #103 landed `specs/0039-origin-delta-aqa.md`, so this spec is **0040** and ADR **0038** (the uploaded document said 0039/0038). The 2026-10-28 post-Tier-A plan had pencilled 0039/0040 and ADR-0038/0039 for unauthored Sprint-2 items | `specs/README.md`; `docs/plans/20261028T000000Z-*.md:7-8` | Numbers are allocated by the directory, not by prose; the Sprint-2 items take the next free numbers when authored |

## Decision register

| ID | Decision | Status | Owner | Recommendation and evidence |
|---|---|---|---|---|
| D-1 | Studio never executes code, touches a filesystem or calls GitHub through MangoMAS. Execution stays in the harness broker | Settled | SDLC | ADR-0029; INV-16; ADR-0038 §1 |
| D-2 | Provider profiles built once in `composition/`, selected per run via `AgentRequest.metadata["llm_profile"]`, recorded in `response.metadata`, root-level `PROVIDER_ALLOWLIST` fails startup on a disallowed provider | **Implemented** (spec-0040, PR A) | AI | `composition/CLAUDE.md` composition-root rule; header selection dropped |
| D-3 | Studio v1 ships **no approval inbox**. Identity for approvals is a separate decision: (a) a Studio BFF owns identity and hands the harness a signed approval, (b) the harness grows a principal, or (c) defer | Open | Product + SDLC | F1, F2. Recommend (c) for v1 and (a) for v2 |
| D-4 | UI ↔ runtime contract: extend the bespoke SSE on `POST /agents/{name}/stream`, or reopen spec-0037 (A2A) | Open | SDLC | Bespoke SSE for v1; A2A go/no-go at G3 with its two deferrals adjudicated in writing |
| D-5 | planlint adoption in MangoMAS | Open, out of Studio scope | SDLC | Harness-side pane first; MangoMAS adoption is its own spec |
| D-6 | Harness accepts CognitiveSignal 1.1.0 (a schema rewrite plus an ingest path, since none exists) | Prerequisite (harness repo) | AI | F3; ADR-0029 "ingest stays blocked until the companion bump" |
| D-7 | Eval cost is measured, not declared, before any cost tab | Prerequisite (MangoMAS) | ML | F10 |
| D-8 | Numbering: spec-0040 (not 0039, taken by PR #103), ADR-0038; 0036 stays reserved | Settled | Scrum | `specs/README.md` "next free" line; F11 |
| D-9 | Studio lives in a new repo `Mango-Metrics-NLM/MangoMas_Studio`, not in-tree | Proposed | SDLC + Product | MangoMAS gates are Python-only; ADR-0038 §2 |
| D-10 | `dev` and `qa` branches are created on MangoMas_V2 before M1 lands | Proposed | Scrum / maintainer | F6; ADR-0036. See "Maintainer tasks" |

## PR A — LLM provider profiles (spec-0040 / ADR-0038), this repository

One PR, seven commits, each green on its own. No protected path is touched.

### Milestone A0 — decision records ✅

- **Failing test first:** none — documentation only. `specs/README.md` row
  and "next free" line updated so `tests/tooling` stays truthful.
- **Depends on:** nothing — parallel-safe.
- `specs/0040-llm-provider-profiles.md`, `docs/adr/0038-studio-two-plane-consumer.md`,
  this plan.

### Milestone A1 — env-contract generalisation ✅

- **Failing test first:** `tests/deploy/test_env_example_contract.py::test_dict_model_groups_are_discovered`
  (red while the walk names `AGENTS` by hand); deleting one
  `MANGOMAS_LLM_PROFILES__<NAME>__*` row from `AGENTS.md` goes red.
- **Depends on:** nothing.
- `tests/deploy/_settings_names.py::dict_model_groups` replaces the
  hand-named `AGENTS_FIELD`; every exemption set derives from it.

### Milestone A2 — config ✅

- **Failing test first:** `tests/test_config.py::test_profile_provider_outside_allowlist_is_refused_naming_the_profile`
  and `::test_profile_model_outside_allowed_models_is_refused_naming_the_profile`.
- **Depends on:** A1 (the docs rows need the generalised walk).
- `config/llm.py` (`LLMProfileSettings`, `provider_allowlist`,
  `DEFAULT_LLM_PROVIDER_ALLOWLIST`), `config/_root.py` (first root
  validator), facade re-exports, `AGENTS.md` rows, `.env.example` block.

### Milestone A3 — agents + composition ✅

- **Failing test first:** `tests/agents/test_prompt.py::test_resolve_llm_profile_beats_agent_override`;
  `tests/composition/test_llm_profiles.py::test_dispatch_unknown_profile_raises_before_any_agent_call_and_records_no_row`.
- **Depends on:** A2.
- `agents/_prompt.py` (keys, `select_llm_profile`,
  `resolve_llm(ctx, name, request=None)`, five callers),
  `composition/llm.py` (`build_llm_profiles`, `_LLMProfileDispatchMixin`,
  close hooks), `builder.py`, `harness.py` (`llm.profile` span attribute),
  `composition/__init__.py` facade.

### Milestone A4 — API ✅

- **Failing test first:** `tests/test_streaming.py::test_stream_unknown_profile_is_400_before_sse_headers`;
  `tests/test_llm_profiles_api.py::test_get_llm_profiles_body_never_contains_secrets`.
- **Depends on:** A3.
- `api/routes/llm.py` (`GET /llm/profiles`), `api/models.py` DTOs,
  pre-dispatch validation on invoke / stream / `workflows/run`, SSE metadata
  frame, snapshot regenerated (one path + two schemas).

### Milestone A5 — CLI ✅

- **Failing test first:** `tests/test_cli_surface.py` (the pinned option
  roster) and `tests/test_cli.py::test_chat_command_profile_routes_to_the_profile_client`.
- **Depends on:** A3.
- `--profile` on `chat` and `workflow run`.

### Milestone A6 — CHANGELOG and gate ✅

- `make gate` green; mutation proofs recorded in the PR body.

## Milestones M2–M4 (other repositories; backlog)

| Milestone | Repo | Owner | Delivers | Gate (both directions) |
|---|---|---|---|---|
| M2 Studio v1 UI | Studio repo (D-9) | AI + Product | Run timeline over `POST /agents/{name}/stream`; profile picker from `GET /llm/profiles`; workflow templates validated by `POST /workflows/validate`; eval report viewer over `json_file` output; README identity note listing all three UIs | G2: two-profile e2e against one LM Studio server (`RUN_LMSTUDIO=1`); a network assertion that Studio opens zero connections to the harness; a `vertex` profile is refused at startup under `PROVIDER_ALLOWLIST=["lmstudio"]` |
| M3 Harness bump + N3 | Harness repo; MangoMas_V2 | AI (bump), ML (N3) | Harness spec + DEC for a 1.1.0 envelope module (dual-accept or replace) plus an ingest path decision (HTTP route vs file reader); eval cost measured from provider usage per row (needs `LLMClient` usage reporting, its own spec); baseline comparison of a local and a cloud profile | G3: a MangoMAS-emitted 1.1.0 fixture passes harness `validate_signal_dict` (and a 1.0.0-shaped one still does, or the DEC says why not); a deliberately expensive row trips `MAX_MEAN_COST_USD` on measured cost; A2A go/no-go written with both deferrals cited |
| M4 Identity and approvals | Harness first | Product + SDLC | Identity decision (D-3 option a or deferral); if (a), a harness DEC amending DEC-065 and a Studio BFF design | G4: a harness DEC supersedes the zero-witness invariant and `test_authority_call_sites.py` pins exactly the one approved site; or a dated deferral in both repos |

Studio v1 is shippable at G2 with only PR A behind it.

## Sprint plan and Definition of Done

Five two-week sprints run by the Scrum agent; no PR is accepted on its
author's own review; the adversarial review on day 6 is mandatory (the
reviewer tries to make the author's gate pass vacuously or fail to fire, and
files the result on the PR).

| Sprint | Goal | Author → Reviewer | Exit |
|---|---|---|---|
| S1 | Close D-3, D-4, D-9, D-10; merge spec-0040 + ADR-0038 (PR A); scaffold Studio repo; create `dev`/`qa` | AI → SDLC (PR A); Product → ML (spec); Scrum (branches) | G0/G1 |
| S2 | Studio run timeline + profile picker against `GET /llm/profiles` | AI → SDLC (UI); Product → ML (copy) | — |
| S3 | Workflow templates, eval viewer, README identity note, e2e suite | AI → SDLC; ML → Product | G2 — Studio v1 |
| S4 | Harness 1.1.0 envelope spec + DEC + ingest decision; N3 measured cost; A2A memo | AI → SDLC; ML → Product; SDLC → Product | G3 |
| S5 | Identity decision; DEC amending DEC-065 or dated deferral; retro | Product + SDLC → ML and AI | G4 |

**Definition of Done, per PR.** Spec section cited with a governing WHEN/THEN
in both directions; mutation proof attached for each new guard; `make gate`
green locally and in CI with floors untouched or raised; no protected path
without a scoped `BREAKING-CHANGE: <path> — <reason>` trailer; `AGENTS.md`
table and `.env.example` updated; CHANGELOG entry referencing spec and ADR;
OpenAPI snapshot regenerated if a DTO changed; reviewed by the designated
non-author, who filed at least one attempted break; for Studio, Playwright
e2e against LM Studio and a network assertion of zero harness connections.

## Maintainer tasks (cannot be done from a feature-branch session)

- **D-10, `dev` / `qa` branches (ADR-0036):** from a checkout with push
  rights, after PR A merges into `feat/initial-release`:

  ```bash
  git fetch origin
  git tag archive/main-legacy origin/main && git push origin archive/main-legacy
  git push origin origin/feat/initial-release:refs/heads/dev
  git push origin origin/feat/initial-release:refs/heads/qa
  # then: set `dev` as the default branch in repository settings, re-point
  # `main` per ADR-0036, and update `Makefile` BASE_REF (line 23).
  GITHUB_REF=refs/heads/dev make deploy-plan   # the proof: the dev environment resolves
  ```

## Deferred / out of scope

- Approval inbox and identity (D-3) — ADR-0038 §4; re-opening needs a harness
  DEC amending DEC-065.
- A2A adoption (D-4) — spec-0037 stays Draft; adjudicated at G3.
- planlint in MangoMAS (D-5) — its own spec after `specs/README.md` is amended.
- `GET /llm/profiles` reporting the effective model per agent — not in R6;
  would need a per-agent projection and is a separate ask.
- Measured eval cost (D-7) — needs `LLMClient` usage reporting; its own spec.
- ~~Replaying this PR's mutations through `scripts/guard_probe_manifest.json`~~ — done: `MUT-0040-M01…M14` (the follow-up PR after #104).

## Risks

| Risk | Likelihood | Impact | Fallback |
|---|---|---|---|
| Harness does not take the 1.1.0 bump | Medium | M3 and any signals view slip | Studio v1 ships at G2 without a signals view; G3 becomes a MangoMAS-side JSONL viewer of `data/cognitive-signals/` |
| Identity stalls; pressure to ship an inbox on the shared key | Medium | A `human_approved` caller with no principal breaks DEC-065 | ADR-0038 §4 makes it out of scope; Scrum escalates |
| Profile precedence vs `MODEL_OVERRIDE` confuses operators | Medium | Wrong model silently used | The shadowed override is logged (`llm_profile_shadows_override`); a test asserts the log line |
| Cross-provider profile inherits the wrong credential | Low (guarded) | Vertex given an LM Studio bearer as SA JSON | Credentials never inherit across providers (spec-0040 R1; tested both ways) |
| Studio repo drifts from MangoMAS gate standards | Medium | Lower bar on the UI | Studio `make gate` mirrors the parity list in `AGENTS.md`; SDLC owns both |
| `main` not the default upstream; `dev`/`qa` missing | Certain today | Promotion path has nowhere to run | D-10 maintainer task above; `make deploy-plan` dry run as proof |
| Estimates are agent-sized | High | Weeks mean nothing until S1 ends | Re-baseline at G0 from measured S1 velocity |

## Verification

```bash
make gate                                        # full pre-PR chain, in gate's order
python -m pytest tests/test_llm_profiles_api.py tests/composition/test_llm_profiles.py \
  tests/agents/test_prompt.py tests/test_streaming.py tests/test_workflow_api.py -q
python -m tests.test_openapi_snapshot && git diff --stat tests/snapshots/openapi.json
RUN_LMSTUDIO=1 python -m pytest tests/lmstudio --no-cov -q   # M2/G2, once a two-profile suite exists
```
