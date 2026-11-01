# Spec-0041: Studio-contract E2E and AQA over the spec-0040 delta

- **Status:** Implemented
- **Linked ADR:** _none — no boundary change_ (exercises the ADR-0038 boundary;
  adds no runtime code)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Studio-contract AQA lane (spec-0041)`

## Problem

Spec-0040 shipped named LLM profiles and ADR-0038 made MangoMas Studio a
separate-repo consumer of the HTTP API. Nothing in-tree proved the contract
Studio relies on end to end. The only live check of profiles was the
hermetic suite. The fourteen spec-0040 mutations were proved once by hand
and never mechanised. No test drove the HTTP surface the way a Studio screen
would: profile picker, run timeline, workflow templates, eval report viewer,
CORS. Outcome: a live two-profile scenario, the mutations replayed by
`make guard-probe`, and an in-tree consumer probe in hermetic and live modes.
Every red found on the way gets a root cause.

## Requirements

- **R1 — Live two-profile scenario.** `tests/lmstudio/test_llm_profiles.py`
  (Scenario 13) runs three profiles against one LM Studio server: `draft`,
  `review` (a second loaded model) and `broken`. `broken` targets an API
  version LM Studio does not serve (`UNSERVED_API_VERSION_SUFFIX`), because
  LM Studio answers an unknown model id from whatever model is loaded (T5).
  The scenario covers the picker, the stamp in `GET /history`, the stream
  metadata frame, an unknown-profile 400 before SSE, a pipeline carrying the
  profile, and a `vertex` profile refused at parse under a local-only
  allowlist. It skips with the sanctioned phrasing unless
  `LMSTUDIO_OVERRIDE_MODEL` is set.
- **R2 — Mutations replayed.** `scripts/guard_probe_manifest.json` carries
  `MUT-0040-M01..M14`, one per spec-0040 guard. These landed first through
  PR #106, so this branch's equivalent `MUT-PROF-01..14` set was dropped at
  merge. Each gate selects a single pytest test that goes red under the
  mutation and green after the restore.
- **R3 — Studio consumer probe.** `scripts/studio_consumer_probe.py` calls
  the real app over `httpx.ASGITransport` and reports one check per contract
  assertion. Its journeys are:
  1. Profile picker: roster listed, no credential fields.
  2. Invoke stamp: the response and the stored turn are stamped.
  3. Routing oracle: `broken` returns 502 while the default client answers.
  4. Run timeline: stream, then `GET /history`.
  5. Unknown profile: a JSON 400 before any SSE header.
  6. Workflow: validate, then run a graph through a `concat` fan_out.
  7. Eval report viewer: `echo` target written to `json_file`.
  8. CORS preflight: the listed origin gets a 200 with the origin echoed, and
     an unlisted one is refused.
  9. Allowed-hosts guard: only permitted hosts are contacted. Live mode sets
     `NO_PROXY=*` for the run, so the recorded host is the peer actually
     contacted rather than a destination behind an environment proxy.
- **R4 — Two modes.** `--mode mock` registers a scoped `lmstudio` factory
  whose client talks to `scripts/_openai_compat_stub.py` in-process. No
  port is opened, and the stub's reply names the serving model, so routing
  is observable. `--mode live` targets LM Studio and requires a review model
  (exit 2 without one). Exit codes come from `mangomas.cli.exit_codes`.
- **R5 — Network guard.** Mock mode fails if any real httpx transport is
  reached. Live mode allows only the LM Studio host. The guard wraps
  `httpx.AsyncHTTPTransport` / `httpx.HTTPTransport`, so it sees every
  component's sockets, not just the LLM client's.
- **R6 — CI.** `make studio-probe-mock` is a step of CI's `test` job.
  `make studio-probe` is opt-in. Both appear in `make help`.
- Adds **no runtime code**: `src/mangomas/` is byte-identical with and
  without this spec.

## Scenarios (WHEN/THEN)

- WHEN `ChatAgent.handle` drops `request` from `resolve_llm` THEN the probe's
  invoke-routing and broken-profile checks fail (`MUT-STUDIO-01`). WHEN it
  is restored THEN they pass.
- WHEN `ChatAgent.stream` drops `request` THEN "tokens served by the review
  model" fails (`MUT-STUDIO-02`). The live suite cannot see this regression
  because LM Studio never names the serving model (T7).
- WHEN any spec-0040 guard in `MUT-0040-M01..M14` is mutated THEN its gate
  fails, and passes after the restore.
- WHEN the app opens a real socket in mock mode THEN the network check fails.
- WHEN a streamed turn gains a stored `llm_profile` (D-12 closed) THEN the
  D-12 check fails on purpose, and this spec is updated with the fix.

## Evidence (G2)

| Lane | Result |
|------|--------|
| `make gate` (baseline) | 3670 passed, 69 sanctioned skips |
| `make guard-probe` | 10/10 baseline. 27/27 after merging PR #106: `MUT-AQA` (10), `MUT-0040-M01..M14`, `MUT-STUDIO-01..03` |
| `make gated-suites` | green |
| `make gpu` (P2 interpreter, cu130, 2 GPUs) | 3/3 |
| `make embeddings-local` (P2) | 66 passed |
| `make lmstudio` | 17/18. The remaining test passes with `LMSTUDIO_EMBEDDING_MODEL` set (T1). Scenario 13: 8/8 |
| `studio_consumer_probe.py --mode mock` | 30/30, no hosts contacted |
| `studio_consumer_probe.py --mode live` | 26/30 passed, 4 skipped, 0 failed; only `localhost` contacted |

Live replies never name the model that served them, so the four routing checks
report `SKIP` in live mode instead of an unfalsifiable `PASS`; they stay hard
assertions in mock mode, where the stub echoes the model id. A skip never fails
the run (exit 0); only a `FAIL` exits 1. `MUT-STUDIO-03` proves the skip cannot
silently revert to a pass.
| Review pass (Bugbot) | 2 findings, both fixed with red-first tests: stub clients leaked when composition failed, and the D-12 check read the newest turn rather than the streamed one |
| Secret scan (host `gitleaks` 8.30.1) | git pass clean (T2, T3) |

Live models: `draft` = `nvidia/nemotron-3-nano-omni`, `review` =
`liquid/lfm2-24b-a2b`.

## Defect ledger

No runtime code defect was found. Every red was either environmental or a
finding about a test's power:

| Id | Class | Finding | Disposition |
|----|-------|---------|-------------|
| T1 | Deployment gap | The embeddings scenario needs `LMSTUDIO_EMBEDDING_MODEL` (a chat model has no `/v1/embeddings`) | Already documented. The rerun with the env var passed |
| T2 | Deployment gap | `make secret-scan` downloads a Linux gitleaks binary (Error 193 on Windows) | Use the host binary on Windows. CI is Linux and unaffected |
| T3 | Environment artifact | The directory pass flags three third-party files under `.venv` | Not repository content |
| T4 | Upstream warning | `StarletteDeprecationWarning` | Not a defect |
| T5 | Oracle design | LM Studio serves an unknown model id from the loaded model, so a bad model id cannot prove routing | The routing oracle is a bad API-version path (R1) |
| T6 | Semantics | Under the mutation, the stamp test stays green when a request names a profile that never served it | Decision D-13 |
| T7 | Coverage gap | Stream-path profile routing is invisible to the live suite | Closed by the probe's mock mode and `MUT-STUDIO-02` |

## Decisions

- **D-11 — Profile readiness.** `/readyz` pings `ctx.llm` only. A profile
  pointing at a dead base URL is listed by `GET /llm/profiles` and fails on
  first use. Options:
  - (a) extend `/readyz` over the profile clients, so one dead profile makes
    the service unready;
  - (b) add a per-profile `ready` field to the DTO, an OpenAPI snapshot
    change;
  - (c) defer.

  **Chosen: (c).** This is a product decision, not a spec-0040 defect.
- **D-12 — Streamed-turn stamp.** A streamed run's stored record carries no
  `llm_profile`, so a timeline built from `/history` cannot label streamed
  turns. The fix sits inside protected `core/orchestrator/` and needs a
  scoped `BREAKING-CHANGE` trailer. **Deferred.** The probe pins the
  limitation, so the fix flips that check.
- **D-13 — The stamp means "requested", not "served".** `metadata.llm_profile`
  records the profile the request selected and the orchestrator validated.
  It does not attest which client produced the text. Routing is proved
  separately: the stub's model echo in mock mode, the `broken` oracle live.
  **Chosen: document, no code change.**

## Deferred

- **A5 — Eval cannot select a profile.** The `agent` / `pipeline` eval
  targets pass no request metadata, so a local-vs-cloud eval baseline is not
  yet possible. It needs its own spec, next to D-7 (measured eval cost). The
  probe's eval journey uses the hermetic `echo` target.
- Numbering note: `docs/plans/20261028T000000Z-post-tier-a-remaining-work-plan.md`
  tentatively named its P5-C "turn-record identity" work spec-0041. That
  number is now taken here, so P5-C takes the next free number when it is
  written. Dated plans are records and are not rewritten.
- Also deferred: D-11, D-12, `dev`/`qa` branch creation (D-10, maintainer),
  the Studio repository scaffold (D-9), identity/approvals (D-3), and
  measured eval cost (D-7).

## Config / env additions

None in `Settings`. The probe reads the existing test-side
`LMSTUDIO_BASE_URL`, `LMSTUDIO_MODEL`, `LMSTUDIO_OVERRIDE_MODEL` and
`LMSTUDIO_E2E_TIMEOUT_SECONDS` as defaults for its `--base-url`, `--model`,
`--review-model` and `--timeout-seconds` flags. It sets
`MANGOMAS_API__CORS_ALLOW_ORIGINS` for its own run and restores the previous
value afterwards.

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_
- Registry additions: _none_ (the probe uses `llm_registry.scoped`, which
  restores the built-in `lmstudio` factory on exit)

## Backwards-compatibility

- `src/mangomas/` is unchanged. Every addition is under `tests/`, `scripts/`,
  `docs/`, `specs/` and build configuration.
- The probe and stub are excluded from the runtime image (`.dockerignore`).

## Test plan

- Unit: `tests/test_studio_consumer_probe.py` covers the stub routes, the
  mock run, crash containment, exit codes, config resolution, the network
  guard and the CORS env restore. It is in `SCRIPTS_TESTS` (94 % scripts
  floor).
- Live: `tests/lmstudio/test_llm_profiles.py` (`RUN_LMSTUDIO=1` plus
  `LMSTUDIO_OVERRIDE_MODEL`).
- Guards: `MUT-0040-M01..M14` and `MUT-STUDIO-01..03` in `make guard-probe`.

## Acceptance criteria

- [x] No runtime behaviour change (`src/` untouched).
- [x] The probe passes in mock (CI) and live modes and fails under each
  `MUT-STUDIO` mutation.
- [x] `ruff`, `mypy`, `pytest` (95 % gate), the scripts floor and
  `frontmatter-lint` are clean.
- [x] CHANGELOG updated. No ADR needed (no boundary change).
