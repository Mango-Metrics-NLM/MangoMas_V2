# Spec-0045: Measured evaluation and governance mechanisation

- **Status:** Draft
- **Linked ADR:** [ADR-0041](../docs/adr/0041-open-source-eval-pattern-adoption.md)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — measured eval outcomes, deterministic eval lanes, governance probes (spec-0045 / ADR-0041)`
- **Delivery plan:** [`docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md`](../docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md)

## Problem

The eval harness is string-in/string-out. `Target.run -> str`
(`src/mangomas/eval/target.py:31`) and `AgentTarget`
(`src/mangomas/eval/targets/agent.py:28-30`) drop `AgentResponse.metadata`; no
LLM adapter reports token usage; `ToolAgent` discards which tools it called
(`src/mangomas/agents/tool_agent.py:179-183` returns only `tool_steps`). Cost is
therefore *declared by the dataset*, never measured — D15 in
`docs/analysis/20260919-council-rejection-and-replan.md:453` chose option (a)
"declared, documented, pinned" and said (b) "measured" only when a cost gate is
meant to block. One scorer per run, one float per row; `EvalReport`
(`src/mangomas/eval/runner.py:92`) carries no provenance, so `diff_reports`
(`src/mangomas/eval/baseline.py:65`) compares unknowns. No `mangomas eval`
runs in CI, and `eval-gate.yml` is manual, off by default, and drives the
external `ianshank/Agents` harness (ADR-0003/0004/0005). The reliability plan
(`docs/plans/20260916T214636Z-reliability-evidence-plan.md:144-225`) already
owns pass^k (C1), judge demotion until calibrated (C2) and a stubbed-LLM CI path
(C3); this spec defers to C1/C2 and ships the deterministic half of C3.

Governance has the mirror-image gap: the authoritative gate
`scripts/check_protected_paths.py` is not among the 51 entries of
`scripts/guard_probe_manifest.json`; `deploy.yml verify` runs only
`make test`/`coverage`/`contracts-coverage`
(`tests/deploy/test_deploy_contract.py:206`); `core/tools.py:55-58` still calls
`ToolEffects` "advisory metadata, not enforcement" although ADR-0039 enforces
it; `ci.yml:95` says 95 % (floor is 98) and `ci.yml:235` says 27 probe entries
(51); `AGENTS.md:535-536` describes the pre-ADR-0021 hook. Two historical
commits carry `BREAKING-CHANGE: none`, which `find_marker_scopes`
(`scripts/_governance.py:171-175`) reads as *unscoped* — an approval of every
protected path (ADR-0033: a name must not promise a control it does not
implement). `NEXT_STEPS.md:87` item A5 (eval targets select an LLM profile) is
the prerequisite for a local-vs-cloud baseline and is folded in here.

## Requirements

Every requirement is additive and default-OFF unless marked **mechanism**
(a CI/test change that alters no runtime behaviour).

- **R1 — Measured outcome seam.** `Target.run` returns
  `TargetOutcome(content: str, metadata: Mapping[str, Any])`. All four in-repo
  targets (`agent`, `pipeline`, `fan_out`, `echo`) return it. `discovery.py`
  wraps a `str`-returning entry-point target into `TargetOutcome` and logs one
  `WARNING`. `DEFAULT_EVAL_SCHEMA_VERSION` 1→2 (`src/mangomas/config/evaluation.py:120`)
  marks the evolution; a configured `schema_version` of 1 still parses.
  Default-OFF reading: with no new scorer selected, every existing scorer's
  verdict is byte-identical because scorers still receive `prediction: str`.
- **R2 — Usage capture.** `adapters/llm/_usage.py`: a ContextVar-held
  `UsageTotals` with `usage_scope()` and `record_usage()`. `LMStudioClient`
  records `body["usage"]` on `complete` and on the stream's final chunk. Three
  conditions (plan A3): the scope closes **before** scoring (a judge's tokens
  are never billed to the row); `llm_calls` is always recorded so "3 calls, 0
  tokens" is distinguishable from "0 calls"; every usage figure carries
  `source: measured|declared`. `cost_budget` reports `measured_tokens` only when
  `llm_calls > 0`. Outside `usage_scope()` the ContextVar is unset and
  `record_usage` is a no-op — adapters behave as today. Vertex usage is Tier 2.
- **R3 — Tool trace.** `ToolAgent` records
  `metadata["tool_calls"] = [{tool, arguments, ok}]` under a per-agent mode
  `MANGOMAS_AGENTS__<NAME>__TOOL_TRACE_ARGUMENTS = none|keys|full`, default
  `keys` (argument **names** plus `sha256` of each value — truncation bounds
  size, not secrecy). Because the pipeline forwarding site is protected
  (`src/mangomas/core/orchestrator/_client.py:497-500`), `AgentTarget` strips
  `tool_calls` from the outcome metadata forwarded to reports when the mode is
  `full`; the in-pipeline forward into the next stage's `request.metadata` is
  recorded as Tier 2. `tool_steps` is unchanged.
- **R4 — `tool_trajectory` scorer.** Emits `recall`, `extra_tools`,
  `missing_tools`, `policy_violations` (count of `ok=False` from
  `harness.tool_policy`), `steps`; `passed = recall == 1.0 and not extra_tools
  and not policy_violations`; option `allow_extra`. No set-similarity score:
  argument checks belong to `json_field` predicates over the recorded trace.
- **R5 — `expected_error` on `DatasetRow`.** A row may name an error type
  (e.g. `ToolExecutionError`). The runner counts a matching raise as a
  *pass* instead of an errored row (`runner.py:168-188` today). Absent → today's
  behaviour.
- **R6 — Structured scoring.** `scorers/json_field.py` ports the ADR-0034
  semantics from `workflow/predicate/_json_field.py` verbatim: dotted path,
  exactly one of `equals`/`at_least`/`at_most`, strict whole-text parse via
  `core.structured.parse_llm_json_object`, booleans excluded from numeric
  comparison, unparseable → `score 0.0`, never raises. `composite` scorer with
  `aggregate: min` and `pass: all` **only**; children recorded first-class under
  `metadata["scores"]`. `-s/--scorer` becomes repeatable (one value = today).
- **R7 — Judge boundary.** The only `llm_judge` change: a `WARNING` when
  `context.llm is` the agent's client (self-grading). Rubric, `include_input`,
  samples, profile and lenient parse wait for the C2 calibration record.
- **R8 — Provenance and slices.** `eval/provenance.py::RunProvenance`
  (`run_id`, `started_at`, `git_sha | None`, `dataset_sha256`, `scorer`, `target`,
  scorer/target option hashes, `llm_provider`, `llm_model`, `llm_profile`,
  `mangomas_version`, `schema_version`) on `EvalReport.run`; `EvalReport.slices`
  keyed by `MANGOMAS_EVAL__SLICE_KEY` (default `category`, reporting only);
  `EvalRowResult.correlation_id` / `response_metadata`. Console sink prints a
  Wilson interval on pass rate, labelled informational, **never gated**; no
  bootstrap. `diff_reports` ignores `run`; `_report_from_dict`
  (`baseline.py:135`) reads every new field with `.get`, so a v1 baseline still
  loads. `sqlite_results` adds columns via `ALTER TABLE … ADD COLUMN`
  (including the already-missing `mean_cost_usd`).
- **R9 — Profile selection (A5).** `target_options["agent"]["llm_profile"]` is
  written into `AgentRequest.metadata["llm_profile"]`, which spec-0040 already
  honours. Absent → the shared client, as today.
- **R10 — Committed datasets and baselines.** `evals/README.md`,
  `evals/datasets/{selfcheck_echo,chat,planner,reviewer,safety_toolagent}_v1.jsonl`,
  `evals/baselines/*.json`. `safety_toolagent_v1` ports garak/OWASP LLM
  categories as **data with attribution** (ungranted mutating tool; indirect
  injection via tool output; oversize / `additionalProperties` / bool-for-int
  arguments; planner output smuggling `allowed_tools` / `confidence`;
  system-prompt leak) scored by `tool_trajectory` + `expected_error` +
  inverted `contains`.
- **R11 — `make eval-selfcheck` (mechanism).** Runs `echo`/`exact_match` over
  `selfcheck_echo_v1` against its committed baseline with
  `--no-allow-new-failures`; added to `gate` and the `ci.yml test` job. It
  proves exit codes and baseline diffing and says exactly that in
  `evals/README.md` — not model quality (ADR-0033; plan A7).
- **R12 — `make eval-lanes` (mechanism).** `tests/evals/test_dataset_lanes.py`
  runs `EvalRunner` with scripted `FakeLLM` orchestrators over the committed
  datasets and asserts `diff_reports(baseline, current).regressed_rows == []`.
  This is the reliability plan's headline acceptance ("a regressed agent prompt
  must fail the gate") in deterministic form; the model-backed lane stays
  manual (`make eval-live`, Tier 2). `tests/cognitive/test_adversarial_emit.py`
  pins the INV-16 refusal path under adversarial transcripts.
- **R13 — Governance probes (mechanism).** `MUT-GOV-01..03` in the guard-probe
  manifest target ADR-0030's named weaknesses: dropping the `protected-paths`
  CI job, silencing the base-ref fallback `WARNING`
  (`scripts/check_protected_paths.py:165-169`), and the `candidate in
  known_paths` branch (`scripts/_governance.py:172`). `MUT-EVAL-01`, `MUT-EVAL-03`,
  `MUT-SEC-01` cover R11, R12, R3.
- **R14 — Supply chain and deploy verify (mechanism).** `scorecard.yml`,
  `codeql.yml` (skipped on a private repo), SBOM artifact in `nightly.yml`,
  `SECURITY.md`, each with an explicit `permissions:` block;
  `tests/deploy/test_workflow_hardening.py` updated first (`_EXPECTED_WORKFLOW_COUNT`
  4→6, third-party action set, failure reporter, concurrency, timeouts).
  `deploy.yml verify` adds `make lint format-check typecheck lint-imports
  frontmatter`; `protected-paths` is out of scope there (no base ref) and the
  job says so.
- **R15 — Hardened `qa`/`prod` pins (mechanism).** `deploy/environments.yaml`
  sets `MANGOMAS_REPAIR__ORACLE_ISOLATION=required`,
  `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION=false`, and non-empty
  `MANGOMAS_LLM__ALLOWED_MODELS` / `PROVIDER_ALLOWLIST` for `qa` and `prod`.
  `local`/`dev` are unchanged.
- **R16 — Prose honesty (mechanism).** `README.md:9` visibility statement,
  `ci.yml:95,235`, `AGENTS.md:535-536`, `docs/eval/harness.md:280-291`,
  `docs/tooling/claude-code-ecosystem.md:17`; PR template note plus a
  `tests/tooling` test forbidding a bare `BREAKING-CHANGE: none`. The
  `core/tools.py:55-58` docstring is the single protected-path edit and lands
  alone in PR-C with a scoped trailer.

## Scenarios (WHEN/THEN)

Every gate is stated in both directions; the "absent" direction is what keeps a
green result from being vacuous.

- **Selfcheck.** WHEN `evals/datasets/selfcheck_echo_v1.jsonl` has one row's
  `expected` changed from `"ok"` to `"nope"` THEN `make eval-selfcheck` exits
  `3` (`MUT-EVAL-01`). WHEN the dataset and baseline are unchanged THEN it exits
  `0` and the console sink shows `regressed_rows: []`.
- **Selfcheck target parity.** WHEN the Makefile target loses
  `--no-allow-new-failures` THEN `tests/deploy/test_ci_make_parity.py` fails
  (replaces v1's `MUT-EVAL-02`, which would have made the gate *greener* and so
  cannot be a red-first probe). WHEN the flag is present THEN it passes.
- **Agent lanes.** WHEN the planner fixture reply in
  `tests/evals/test_dataset_lanes.py` is edited so `steps` is empty THEN
  `make eval-lanes` is red with `planner_v1` rows in `regressed_rows`
  (`MUT-EVAL-03`). WHEN the fixtures match the committed baselines THEN it is
  green with `regressed_rows == []` **and** at least one row per dataset
  executed (a zero-row run fails, never passes).
- **MUT-GOV-01.** WHEN the `protected-paths` job is deleted from
  `.github/workflows/ci.yml` THEN
  `tests/deploy/test_ci_make_parity.py::test_protected_paths_job_delegates_to_make`
  fails. WHEN the job is present and delegates to `make protected-paths` THEN it
  passes.
- **MUT-GOV-02.** WHEN the base-ref fallback `WARNING` at
  `scripts/check_protected_paths.py:165-169` is removed THEN
  `tests/test_check_protected_paths.py::test_a_base_ref_without_a_policy_file_falls_back_loudly`
  fails. WHEN the warning is emitted on stderr THEN it passes.
- **MUT-GOV-03.** WHEN `scripts/_governance.py:172` is changed so an unknown
  first token is treated as scoped THEN
  `tests/test_check_protected_paths.py::test_a_marker_naming_a_path_the_base_does_not_protect_reads_as_unscoped`
  fails. WHEN an unknown path yields `has_unscoped=True` THEN it passes.
- **MUT-SEC-01.** WHEN a `FakeTool` call carries `{"token": "ghp_…"}` under the
  default trace mode THEN the literal token appears in neither
  `FakeRepository._turns` nor `report_payload(...)` (only the argument name and
  a `sha256:` digest do). WHEN the trace mode is `none` THEN `tool_calls` is
  absent entirely. WHEN the probe's redaction is mutated to pass the value
  through THEN the guard fails.
- **Expected error.** WHEN a `safety_toolagent_v1` row declares
  `expected_error: ToolExecutionError` and the ungranted mutating tool is
  refused THEN the row passes. WHEN the tool is *executed* THEN the row fails
  with `policy_violations >= 1`.
- **Judge boundary.** WHEN `llm_judge` scores with `context.llm` identical to
  the agent client THEN one `WARNING` (`event="eval_judge_is_agent"`) is logged
  and the score is still reported. WHEN the clients differ THEN no warning.
- **Deploy verify.** WHEN `deploy.yml verify` omits any of
  `make lint`, `format-check`, `typecheck`, `lint-imports`, `frontmatter` THEN
  `tests/deploy/test_deploy_contract.py` (the pinned command list at line 206,
  extended first) fails. WHEN all are present and offline THEN it passes.
- **Hardened env pins.** WHEN `deploy/environments.yaml` `qa` or `prod` lacks
  `ORACLE_ISOLATION=required`, `ALLOW_INLINE_DEFINITION=false`, or has an empty
  `ALLOWED_MODELS` / `PROVIDER_ALLOWLIST` THEN
  `tests/deploy/test_env_profiles_hardened.py` fails. WHEN `local`/`dev` carry
  today's values THEN the test does not constrain them.
- **Workflow hardening.** WHEN `scorecard.yml` or `codeql.yml` lacks an explicit
  `permissions:` block, `concurrency`, or `timeout-minutes` THEN
  `test_workflow_hardening.py` fails. WHEN the count is 6 and every third-party
  action is in `_EXPECTED_THIRD_PARTY_ACTIONS` THEN it passes.
- **Blanket marker.** WHEN the PR template or a `tests/tooling` fixture
  contains a bare `BREAKING-CHANGE: none` THEN the tooling test fails. WHEN the
  template carries the scoped form `BREAKING-CHANGE: <path> — <rationale>` THEN
  it passes.
- **Schema shim.** WHEN a discovered entry-point target returns `str` THEN the
  row is scored and one `WARNING` names the plugin. WHEN it returns
  `TargetOutcome` THEN no warning.
- **Usage source.** WHEN `FakeLLM(usage=...)` is wired THEN the row reports
  `llm_calls >= 1`, `source: measured`. WHEN `FakeLLM()` is wired THEN
  `llm_calls` counts the calls and tokens are `0`, `source: measured` — never
  silently `declared`. WHEN `RUN_LMSTUDIO=1` THEN
  `tests/lmstudio/test_usage_emitted.py` asserts the real server emits `usage`.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_AGENTS__<NAME>__TOOL_TRACE_ARGUMENTS` | `keys` | `ToolAgent` trace mode for `metadata["tool_calls"]`: `none` (no trace), `keys` (argument names + `sha256` of each value), `full` (verbatim; stripped by `AgentTarget` before reporting) |
| `MANGOMAS_EVAL__SLICE_KEY` | `category` | `DatasetRow.metadata` key used to group `EvalReport.slices` (reporting only, never gated) |
| `MANGOMAS_EVAL__SCHEMA_VERSION` | `2` (was `1`) | `DEFAULT_EVAL_SCHEMA_VERSION` bump marking the `TargetOutcome` seam; `1` still parses |

Deliberately **no** env var for the Wilson interval, for bootstrap (not shipped),
for `RunProvenance`, or for `eval-selfcheck` / `eval-lanes`: those are report
fields and `make` targets, not operator tunables. All three constants above are
`DEFAULT_*` module constants surfaced through `AgentSettings` / `EvalSettings`
and re-exported in `tests.constants`.

## Protocol / contract impact

- **Changed (unprotected, `eval/`):** `Target.run -> TargetOutcome`
  (`src/mangomas/eval/target.py:31`). Not an `adapters/*/base.py` protocol; the
  entry-point group `mangomas.eval.targets` has no known external users and
  `discovery.py` shims a `str` return.
- **Additive dataclass fields:** `ScorerContext.response_metadata`,
  `ScorerContext.messages` (`src/mangomas/eval/protocol.py:42`);
  `DatasetRow.expected_error` (`src/mangomas/eval/dataset.py:46`);
  `EvalRowResult.correlation_id` / `response_metadata` and
  `EvalReport.run` / `slices` (`src/mangomas/eval/runner.py:78,92`). All default
  to `None` / empty.
- **New error types:** _none_. Refusals surface as existing
  `ToolExecutionError` / `LLMBadResponse`; `expected_error` matches by class name.
- **Registry additions:** `scorer_registry` gains `tool_trajectory`,
  `json_field`, `composite`. No new registry.
- **Protected paths:** none in PR-A or PR-B. The only protected edit is the
  `src/mangomas/core/tools.py:55-58` docstring in PR-C (≤10 lines, no code
  change, trailer `BREAKING-CHANGE: src/mangomas/core/tools.py — …`).
  `AgentRequest` / `AgentResponse` / `errors.py` / `registry.py` are untouched.

## Backwards-compatibility

- With no new scorer selected, no `tool_trajectory`/`json_field`/`composite` in
  use, and `TOOL_TRACE_ARGUMENTS` left at `keys`, every existing eval row's
  `score`/`passed` is byte-identical: scorers still receive `prediction: str`
  and `expected: str`. `ToolAgent.metadata` gains a key; `tool_steps` is
  unchanged. Deployments that never run `ToolAgent` see no metadata change.
- A v1 `json_file` baseline loads via `.get`; `diff_reports` ignores `run`, so
  an old baseline diffs against a new report. `sqlite_results` migrates by
  `ALTER TABLE`, never by drop.
- `schema_version: 1` in an existing `.env` parses; only `> 2` is refused, as
  `> 1` is today (`config/evaluation.py:207`).
- Third-party `str`-returning targets keep working with one `WARNING`;
  migration is to return `TargetOutcome(content=...)`.
- `make gate` grows two targets (`eval-selfcheck`, `eval-lanes`); neither needs
  an LLM or network.
- `qa`/`prod` pins change deployed behaviour for those two environments only;
  neither has been deployed yet (ADR-0036 branches do not exist).

## Test plan

- Unit (`tests/eval/`): `test_target_outcome.py`, `test_discovery_shim.py`,
  rewritten `test_cost_measurement_basis.py` pins (measured vs declared),
  `test_tool_trajectory.py`, `test_json_field_scorer.py`, `test_composite.py`,
  `test_provenance.py`, `test_expected_error.py`, `test_judge_warning.py`,
  `test_profile_target_option.py`; `tests/adapters/llm/test_usage.py`;
  `tests/agents/test_tool_trace.py`; `tests/cognitive/test_adversarial_emit.py`.
- Fakes: `FakeLLM(usage=...)` in `tests/fakes.py`; `FakeTool` unchanged.
- Lanes: `tests/evals/test_dataset_lanes.py` (outside the `tests/eval` unit
  suite), run by `make eval-lanes`.
- Governance (`tests/deploy/`, `tests/tooling/`, `tests/`):
  `test_ci_make_parity.py` (extended first), `test_deploy_contract.py:206`
  (extended first), `test_workflow_hardening.py`,
  `test_env_profiles_hardened.py`, `test_check_protected_paths.py` (two new
  tests), the `BREAKING-CHANGE: none` tooling test,
  `test_env_example_contract.py`.
- Gated: `RUN_LMSTUDIO=1` → `tests/lmstudio/test_usage_emitted.py`.
- Probes: `MUT-GOV-01..03`, `MUT-EVAL-01`, `MUT-EVAL-03`, `MUT-SEC-01` in
  `scripts/guard_probe_manifest.json`; each red→green under `make guard-probe`
  (`mango-mutation-proof`). `ci.yml:235` count updated.
- Coverage: global 98 %; `eval`/`harness`/`cli`/`config` 100 %;
  `agents`/`adapters` ≥ 98 %; scripts ≥ 94 %. No new `scripts/*.py`, so
  `SCRIPTS_TESTS` is unchanged.

## Acceptance criteria

Persona outcomes, copied from the plan's Verification section:

- [ ] A planner fixture whose reply loses `steps` turns CI red (`make eval-lanes`, `MUT-EVAL-03`).
- [ ] A developer copies `evals/datasets/chat_v1.jsonl` and runs `make eval-lanes` in under ten minutes.
- [ ] The `json_file` report shows run id, model, profile, per-slice pass rate and *measured* cost.
- [ ] `deploy.yml verify` runs the same offline checks CI does.

Mechanical:

- [ ] Feature off by default → no behaviour change (`test_cost_measurement_basis.py`, `test_tool_trace.py` prove it).
- [ ] `make gate` + `make guard-probe` green locally and in CI.
- [ ] `test_env_example_contract.py` and `test_ci_make_parity.py` pass.
- [ ] CHANGELOG block names the locking test for each guard; ADR-0041 Accepted on merge of PR-B; this spec flipped to Implemented in PR-B's last commit.
