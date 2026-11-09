# Measured evaluation and governance mechanisation — delivery plan

- **Branch:** `claude/elegant-sagan-doxg0j`
- **Date:** 2026-11-09
- **Target release:** rolling
- **Status:** In progress
- **Specs:** spec-0045
- **ADRs:** ADR-0041

## Executive summary

Three logical PRs ship in order: **PR-A** mechanises governance that today
exists only as prose (probes against the authoritative protected-path gate,
Scorecard/CodeQL with explicit permissions, an offline `deploy.yml verify`,
hardened `qa`/`prod` pins, prose honesty); **PR-B** turns the eval harness from
string-in/string-out into measured outcomes (usage, tool trajectory,
provenance, structured scoring) and adds two LLM-free CI lanes over committed
datasets; **PR-C** is the single protected-path edit, a docstring. The one
constraint that shaped the sequencing: **CI runs on GitHub-hosted runners with
no LLM, and this session may push one branch.** Every gate added here is
therefore deterministic (`echo`, scripted `FakeLLM`) and the model-backed lane
stays manual. PR-A goes first because
it touches zero protected paths and its probes make PR-B's own gates
trustworthy; PR-B is walking-skeleton order so a dead session still leaves a
seam with a consumer; PR-C is independent and carries its scoped trailer in
commit and body.

Three logical PRs; this session is authorised to push only
`claude/elegant-sagan-doxg0j`, so they land as three ordered commit groups on
one draft PR unless the user grants additional branches.

Facts cited below were re-verified in the tree at `7e926c9` and again while
writing spec-0045 (the pipeline forwarding site is protected —
`src/mangomas/core/orchestrator/_client.py:497-500` — so A4's conditional
resolves to "strip in `AgentTarget`; pipeline leak is Tier 2").

## PR-A — `ci: governance mechanisation and supply chain` (spec-0045 R13–R16)

Owner: `mango-ci-dev`; `mango-harness-dev` for the probes. Zero protected paths.
A0 first; A1–A6 are independent of each other.

### Milestone A0 — repo visibility and README

- **Failing test first:** none — this is a fact check. `gh api
  repos/Mango-Metrics-NLM/MangoMas_V2 --jq .private` is search-only in this
  session; if the answer is unclear, ask the user rather than guess.
- **Depends on:** nothing.
- Fix `README.md:9-10` ("This is a private repository") to match reality. The
  CodeQL workflow in A2 is skipped while private, so this answer gates A2.

### Milestone A1 — `MUT-GOV-01..03`

- **Failing test first:** `make guard-probe` red→green per entry.
- **Depends on:** A0.
- Add three entries to `scripts/guard_probe_manifest.json`:
  `MUT-GOV-01` deletes the `protected-paths` job (`.github/workflows/ci.yml:125-159`;
  `fixed`/`defect` snippets byte-exact) →
  `tests/deploy/test_ci_make_parity.py::test_protected_paths_job_delegates_to_make`;
  `MUT-GOV-02` removes the base-ref fallback `WARNING`
  (`scripts/check_protected_paths.py:165-169`) →
  `tests/test_check_protected_paths.py::test_a_base_ref_without_a_policy_file_falls_back_loudly`;
  `MUT-GOV-03` flips the `candidate in known_paths` branch
  (`scripts/_governance.py:171-175`) →
  `tests/test_check_protected_paths.py::test_a_marker_naming_a_path_the_base_does_not_protect_reads_as_unscoped`.
- Fix the `ci.yml:235` count (27 → actual).

### Milestone A2 — Scorecard, CodeQL, SBOM artifact, SECURITY.md

- **Failing test first:** `tests/deploy/test_workflow_hardening.py` fails on
  `_EXPECTED_WORKFLOW_COUNT` (4→6) and `permissions:` before the YAML lands.
- **Depends on:** A0 (CodeQL skipped if private).
- Add `.github/workflows/scorecard.yml`, `codeql.yml` with explicit
  `permissions:` (`id-token: write` / `security-events: write` are the first
  such grants outside `deploy.yml` — say so in the workflow comment), SBOM
  artifact upload in `nightly.yml`, `SECURITY.md`. Update
  `_EXPECTED_THIRD_PARTY_ACTIONS`, failure reporter, concurrency, timeouts.

### Milestone A3 — offline `deploy.yml verify`

- **Failing test first:** `tests/deploy/test_deploy_contract.py:206` pins the
  verify command list to `["make test", "make coverage", "make contracts-coverage"]`
  — extend it first.
- **Depends on:** nothing.
- Add `make lint format-check typecheck lint-imports frontmatter` to `verify`.
  State in the job that `protected-paths` is out of scope (no base ref).

### Milestone A4 — hardened `qa`/`prod` env pins

- **Failing test first:** `tests/deploy/test_env_profiles_hardened.py` red on
  the current `deploy/environments.yaml`.
- **Depends on:** nothing.
- `qa`/`prod`: `MANGOMAS_REPAIR__ORACLE_ISOLATION=required`,
  `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION=false`, non-empty
  `MANGOMAS_LLM__ALLOWED_MODELS` / `PROVIDER_ALLOWLIST`. `local`/`dev` unchanged.

### Milestone A5 — prose honesty and the blanket-marker test

- **Failing test first:** the `tests/tooling` test forbidding a bare
  `BREAKING-CHANGE: none` is red against the current PR template.
- **Depends on:** nothing.
- Fix `ci.yml:95` (95 → 98), `AGENTS.md:535-536` (pre-ADR-0021 hook),
  `docs/eval/harness.md:280-291`, `docs/tooling/claude-code-ecosystem.md:17`.
  PR-template note on scoped trailers; keep the file name and headings
  (`tests/constants/corpus.py:228` lists it in the corpus roster).

### Milestone A6 — CHANGELOG and human actions

- **Failing test first:** none.
- **Depends on:** A1–A5.
- One `[Unreleased]` block naming each guard's locking test. List human actions
  **not scripted**: D1 — make the CI roster required status checks; create
  `dev`/`qa`/`main` (ADR-0036).

## PR-B — `eval: datasets, measured outcomes, deterministic lanes` (spec-0045 R1–R12)

Integrator: `mango-eval-dev`. Walking-skeleton order; each unit leaves
`make gate` green. B0→B1→B2→B3→B4→B5→B6→B7→B8; B3/B4/B5 may be developed in
parallel against B2's seam but land in that order.

### Milestone B0 — spec, ADR, plan

- **Failing test first:** `tests/tooling/test_decision_record_numbering.py`
  and `tests/tooling/test_doc_links.py` pass with the three new records.
- **Depends on:** nothing.
- `specs/0045-measured-eval-and-governance-mechanisation.md` (Draft),
  `docs/adr/0041-open-source-eval-pattern-adoption.md` (alternatives written
  as thesis/counter/rebuttal per project), this plan. Owner `mango-adr-author`.

### Milestone B1 — `make eval-selfcheck`

- **Failing test first:** `MUT-EVAL-01` — corrupt one row of
  `evals/datasets/selfcheck_echo_v1.jsonl` → exit `3`. The `test` job's command
  list is pinned exactly at `tests/deploy/test_ci_make_parity.py:119-133` —
  append `make eval-selfcheck` there first.
- **Depends on:** B0.
- `evals/README.md` (says exactly: proves exit codes and baseline diffing, not
  model quality), `evals/datasets/selfcheck_echo_v1.jsonl`,
  `evals/baselines/selfcheck_echo_v1.json`; target in `gate` + `ci.yml test`.
  Drop v1's `MUT-EVAL-02` (removing a flag makes the gate greener; the probe
  requires red) — replace with a parity test that the Makefile target carries
  `--no-allow-new-failures`. Owners `mango-ci-dev` + `mango-eval-dev`.

### Milestone B2 — measured outcome seam

- **Failing test first:** `tests/eval/test_target_outcome.py` — a target
  returning `TargetOutcome` has its metadata visible in `ScorerContext`;
  rewritten `tests/eval/test_cost_measurement_basis.py` pins.
- **Depends on:** B1.
- `Target.run -> TargetOutcome`; four targets; `discovery.py` `str`-shim with
  `WARNING`; `ScorerContext.response_metadata/messages`;
  `EvalRowResult.correlation_id/response_metadata`;
  `DEFAULT_EVAL_SCHEMA_VERSION=2`; `_report_from_dict` `.get` for new fields.
  Owner `mango-eval-dev`.

### Milestone B3 — usage capture

- **Failing test first:** `tests/adapters/llm/test_usage.py` — a `complete`
  inside `usage_scope()` increments `llm_calls` and records `body["usage"]`.
- **Depends on:** B2.
- `adapters/llm/_usage.py` (ContextVar `UsageTotals`, `usage_scope`,
  `record_usage`); `lmstudio.py` complete + stream-final-chunk; targets close
  the scope before scoring; `cost_budget` measured tier; `FakeLLM(usage=...)`;
  `tests/lmstudio/test_usage_emitted.py` (`RUN_LMSTUDIO=1`). Owners
  `mango-llm-adapter-dev` (adapters only), `mango-fake-builder`, `mango-eval-dev`.

### Milestone B4 — tool trace, trajectory scorer, safety data

- **Failing test first:** `MUT-SEC-01` — a `FakeTool` call carrying
  `{"token": "ghp_…"}` must not appear in persisted turns or the serialised
  report; `tests/agents/test_tool_trace.py`.
- **Depends on:** B2.
- `tool_agent.py` `tool_calls` with trace mode (`config/agents.py` constant +
  env, `.env.example`, AGENTS.md table); strip in `AgentTarget` (forwarding
  site is protected, see summary); `scorers/tool_trajectory.py`;
  `DatasetRow.expected_error`; `evals/datasets/safety_toolagent_v1.jsonl`;
  `tests/cognitive/test_adversarial_emit.py`. Owners `mango-agent-impl-dev`,
  `mango-eval-dev`, `mango-harness-dev` (probe).

### Milestone B5 — structured scoring

- **Failing test first:** `tests/eval/test_json_field_scorer.py` — the same
  spec that ADR-0034's predicate accepts/rejects, the scorer passes/fails;
  `tests/eval/test_composite.py` — one failing child fails the row.
- **Depends on:** B2.
- `scorers/json_field.py` porting `workflow/predicate/_json_field.py`
  semantics; `composite` (`min`/`all` only); `-s` repeatable;
  `evals/datasets/{planner,reviewer}_v1.jsonl` with `ExecutionPlan` /
  `ReviewResult` expectations (`agents/planner.py:23`, `reviewer.py:15`);
  judge-is-agent `WARNING`. Owners `mango-eval-dev`, `mango-cli-dev`.

### Milestone B6 — provenance, slices, profile

- **Failing test first:** `tests/eval/test_provenance.py` — two reports over
  different dataset bytes carry different `dataset_sha256`; a v1 baseline
  without `run` still loads.
- **Depends on:** B3, B4, B5.
- `eval/provenance.py`, `EvalReport.run/slices`, Wilson interval in the console
  sink (informational), `diff_reports` ignores `run`, sqlite
  `ALTER TABLE ADD COLUMN` (incl. missing `mean_cost_usd`),
  `target_options["agent"]["llm_profile"]` → request metadata
  (`NEXT_STEPS.md:87` A5); `MANGOMAS_EVAL__SLICE_KEY` (default `category`);
  regenerate `evals/baselines/*`. Owner `mango-eval-dev`.

### Milestone B7 — deterministic agent lane

- **Failing test first:** `MUT-EVAL-03` — change a planner fixture reply so
  `steps` is empty → `make eval-lanes` red. This is the reliability plan's
  headline acceptance (C3), deterministic form.
- **Depends on:** B6.
- `tests/evals/test_dataset_lanes.py` runs `EvalRunner` with scripted
  `FakeLLM` orchestrators over the committed datasets and asserts
  `diff_reports(baseline, current).regressed_rows == []`; `make eval-lanes` in
  `gate` + CI. Routing `mango-test-engineer` → `mango-eval-dev`.

### Milestone B8 — flip and document

- **Failing test first:** none.
- **Depends on:** B7.
- Flip spec-0045 to Implemented, tick this plan, rewrite `docs/eval/harness.md`
  (measured cost, new scorers, lanes), CHANGELOG block.

## PR-C — `core: ToolEffects docstring states enforcement`

Owner: `mango-harness-dev`. Independent of PR-A and PR-B.

### Milestone C0 — the one protected docstring

- **Failing test first:** none — prose only. `make protected-paths` must be
  red without the trailer and green with it.
- **Depends on:** nothing.
- `src/mangomas/core/tools.py:55-58` only, ≤10 lines, no code change. Commit
  trailer and PR body:
  `BREAKING-CHANGE: src/mangomas/core/tools.py — docstring says ToolEffects is enforced by harness.tool_policy (ADR-0039); no code change`.

## Environment and pins confirmed before coding

- Dev extras + `mango-integration-contracts` install cleanly on the session's
  Python 3.13 (`lint-imports` present; `gitleaks` absent — `make secret-scan`
  is CI/nightly only).
- Any new `scripts/*.py` must be appended to `SCRIPTS_TESTS` (`Makefile:29-35`)
  or `scripts-coverage` measures it at 0 %. PR-A adds none.
- `MUT-GOV-01` mutates `.github/workflows/ci.yml:125-159` and restores it; the
  manifest snippets must be byte-exact copies.

## Stop-loss

1. A unit red on `make gate` twice after its failing test is green → revert to last green commit, record under "Deferred" in the plan doc.
2. If B3 is not green by mid-session budget → ship PR-A, B0–B2, PR-C; defer the rest with reasons.
3. `guard-probe` exceeds its `timeout-minutes` → drop `MUT-EVAL-03`, keep `MUT-GOV-*`, `MUT-SEC-01`.
4. Any protected path appears in PR-A/PR-B diff → move the change to PR-C or defer.
5. Vertex usage, stub-server CLI lane, judge changes: never pulled forward.

## Definition of done (per PR)

`make gate` + `make guard-probe` green locally and in CI; scripts-coverage ≥ 94, `eval`/`harness`/`cli`/`config` 100 %, `agents`/`adapters` ≥ 98; every new guard named in the PR body with its red→green proof; one CHANGELOG block naming the locking test; `test_env_example_contract.py` and `test_ci_make_parity.py` pass; spec Status reflects merged reality; PR-C carries the scoped trailer in commit and body; merge-commit, not squash.

## Panel review record

Five reviewer seats, each run as an independent agent over the v1 draft and
the tree at `7e926c9`, with three exploration sub-agents feeding them. The
dialectic per decision (Thesis / Counter-argument / Rebuttal / Decision) is in
the plan of record; one-line verdicts:

| Seat | Verdict |
|---|---|
| SDLC / release engineering | Split by merge risk into three merge-commit PRs; a `continue-on-error` branch-rules report and a check-run poll in `deploy.yml` are names for controls that never block — run the offline `make` targets instead. |
| ML evaluation science | Port only deterministic semantics; change `Target.run` directly rather than a dual Protocol; no bootstrap CI at n ≤ 50; the judge is self-grading until calibrated and stays out of every gate verdict. |
| Product | ADR-0003's parity cost is real — curated datasets teach more than six unrun scorers; D1 (required checks) is a five-minute admin action cheaper than any script; `eval-smoke` must not promise model quality. |
| Scrum / delivery | Walking-skeleton order inside PR-B, hot files owned by one integrator, explicit stop-loss, pinned test lists extended *before* the YAML they pin. |
| AI safety / security | A tool trace is a secret-exfiltration channel unless redacted at source (`keys` default, `MUT-SEC-01`); a Tier 1 that never exercises prompt injection against `tool_agent` is not an evaluation of an agent platform — ship the deterministic adversarial dataset now. |

## Deferred / Tier 2 / Tier 3

Sequenced to the reliability plan
(`docs/plans/20260916T214636Z-reliability-evidence-plan.md`). Re-opening any
item revisits spec-0045 and ADR-0041.

**Tier 2 (in order):** C1 pass^k (`MANGOMAS_EVAL__TRIALS`, `pass_pow_k`,
`MIN_PASS_POW_K`) → C2 judge calibration record (`JUDGE_CALIBRATION_PATH`;
judge reported-not-gated; then `rubric`, `include_input` incl. system prompt,
`judge_profile`, lenient parse via `core.structured.parse_llm_json_object`) →
stubbed-LLM CLI lane using `scripts/_openai_compat_stub.py` so `mangomas eval`
itself runs in CI (C3) → model-backed `eval-gate.yml` job running
`mangomas eval` (inline definitions off, no second external install, judge
excluded) → `make eval-live PROFILE=` local-vs-cloud baselines → nightly
`report_branch_rules.py` filing an issue (roster in `tests/constants`, skips
missing branches, documents the classic-protection blind spot) → ruff `TC` with
`runtime-evaluated-base-classes` + import-linter `layers` (measure first) →
`gen_ai.usage.*` on `harness.agent_invoke` with pinned semconv version → garak
nightly behind `vars.REDTEAM_ENABLED` → Vertex usage → `pip-licenses` allowlist
→ mutmut nightly ratchet → image SLSA attestation → `Tool` metadata channel
then Ragas-style context recall (deterministic first) → strip `tool_calls`
before `dispatch_pipeline` forwards metadata (protected
`core/orchestrator/_client.py:497-500`; needs a trailer).

**Tier 3:** Inspect AI `ModelAPI` adapter extra; PyRIT multi-turn scenarios;
Ragas faithfulness/relevancy only with a calibration record; router eval +
correction-yield (C4); per-principal auth; take D13 (`requirements.lock`
ownership).

**Not re-proposed (already present):** pip-audit, two-pass gitleaks, Trivy SBOM
(non-blocking), SHA-pinned actions, Dependabot, import-linter (2 contracts),
ruff `S`, guard-probe. spec-0022's dated floors are not rewritten.

## Verification

```bash
make gate                        # now includes eval-selfcheck and eval-lanes
make guard-probe                 # MUT-GOV-01..03, MUT-EVAL-01/03, MUT-SEC-01 red→green
python -m pytest tests/eval tests/evals tests/adapters tests/cognitive tests/deploy tests/tooling -q
RUN_INTEGRATION=1 python -m pytest tests/integration --no-cov -q
make eval-selfcheck; sed -i 's/"expected": "ok"/"expected": "nope"/' evals/datasets/selfcheck_echo_v1.jsonl; make eval-selfcheck; echo "exit=$? (expect 3)"; git checkout evals/
git log --format=%B -1 <PR-C commit> | grep '^BREAKING-CHANGE: src/mangomas/core/tools.py'
```

Panel acceptance (outcomes): a planner fixture whose reply loses `steps` turns
CI red (B7); a developer copies `evals/datasets/chat_v1.jsonl` and runs
`make eval-lanes` in under ten minutes; the `json_file` report shows run id,
model, profile, per-slice pass rate and *measured* cost; `deploy.yml verify`
runs the same offline checks CI does.
