# Spec-0048: judge prompt controls, and calibration pinned to the prompt

- **Status:** Implemented
- **Linked ADR:** _none — no boundary change_ (`Scorer` unchanged; `ScorerContext` gains a default-empty `llm_profiles`; `LLM_PROFILES_EXTRAS_KEY` moves to `config/llm.py` and stays importable from `agents._prompt`)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — judge calibration record …` (spec-0047 / spec-0048 bullets)
- **Delivery plan:** reliability plan milestone C2 (second half); Tier 2 item 2 of [`docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md`](../docs/plans/20261109T000000Z-measured-eval-and-governance-plan.md)

## Problem

Spec-0047 made an uncalibrated `llm_judge` *reported, not gated*. The judge itself
was still the original fixed prompt: no rubric, no view of the question it is
grading, always the system under test's own client, and a strict parser that
turns a fenced reply from a small local model into an errored row. The Tier 2
list names four controls (`rubric`, `include_input` incl. system prompt,
`judge_profile`, lenient parse). Each changes what a calibration record vouches for.

## Decision, dialectically

- **Thesis.** Add the four knobs as scorer options; keep the default prompt
  byte-identical so an unconfigured judge is unchanged.
- **Counter-argument.** Every knob changes what the judge measures, so a record
  measured under prompt A would silently "calibrate" prompt B — the vacuous green
  C2 exists to prevent. And a `judge_profile` only reduces self-preference if the
  record is keyed to the profile's model, not the shared one.
- **Rebuttal (a third reading).** Bind the record to the judge *as configured*.
  The record gains `prompt_sha256` (digest of the system prompt and
  `include_input`); the status carries the current digest; a missing or
  different digest is a failing rule. With a profile, the CLI resolves
  `judge_model` to the profile's model, so the existing model rule does the
  rest. Lenient parsing changes formatting tolerance, not what is scored, so it
  stays outside the digest and is surfaced per row instead (`parse`).
- **Decision.** Thesis plus the rebuttal's pinning.

## Requirements

- R1 Options `rubric` (string or list of non-empty strings → a `RUBRIC` block
  before the output format), `include_input` (bool; the user message starts with
  `INPUT:` and every row message as `[role] content`, system included; `(none)`
  when the row has none), `judge_profile` (non-empty string). Bad types →
  `ConfigError`. All off by default; the default system prompt equals the
  historical `_JUDGE_SYSTEM_PROMPT` byte for byte.
- R2 `judge_prompt_sha256(rubric, include_input)` =
  `digest_of({"version": 1, "system": <prompt>, "include_input": …})`;
  `LLMJudgeScorer.prompt_sha256`; `JudgeStatus.prompt_sha256` (current) and
  `judge_profile`.
- R3 `JudgeCalibrationRecord.prompt_sha256` (optional, `sha256:<64 hex>`);
  `assess_calibration(..., prompt_sha256=, judge_profile=)` adds, after the model
  rules: record pins no prompt → `record does not pin the judge prompt
  (prompt_sha256)`; different digest → `record is for judge prompt X, the judge
  runs Y` (12-hex short forms). `summary()` names the profile and the short digest.
- R4 `ScorerContext.llm_profiles` (default empty), filled by the runner from
  `orch.context.extras[LLM_PROFILES_EXTRAS_KEY]`; the judge grades through
  `llm_profiles[judge_profile]` (absent → `ConfigError`, the row errors) and logs
  `eval_llm_judge_profile` at INFO instead of the self-grading WARNING.
- R5 `MANGOMAS_EVAL__JUDGE_PROFILE` (None). The CLI seeds it; the effective
  profile (explicit option wins) must be a configured `MANGOMAS_LLM_PROFILES__*`
  profile (else exit 2); `judge_model` = that profile's model, else
  `MANGOMAS_LLM__MODEL`.
- R6 Parsing: strict `json.loads` first (`parse: "strict"`); on failure
  `core.structured.parse_or_recover` with an envelope that requires `score`
  (`parse: "recovered"`); the numeric / finite / clamp checks apply to both.

## Scenarios (WHEN/THEN)

- WHEN a record names the default prompt's digest and the judge runs a rubric
  THEN the judge is uncalibrated with the prompt reason
  (`test_a_record_for_another_prompt_does_not_calibrate`); AND WHEN the record
  names the rubric prompt's digest THEN calibrated.
- WHEN the record pins no prompt THEN uncalibrated with its own reason.
- WHEN `JUDGE_PROFILE=cloud` and the record names the cloud model THEN the console
  says `judge=llm_judge via profile cloud calibrated (…`, the profile client judged
  and the shared client only answered the agent; WHEN the profile is unknown THEN exit 2.
- WHEN the judge replies inside a code fence THEN the score is recovered and the
  row says `parse: "recovered"`; WHEN no object with a numeric score exists THEN
  `LLMBadResponse`.
- WHEN no option is set THEN the system prompt and user message are byte-identical
  to before.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_EVAL__JUDGE_PROFILE` | _(unset)_ | LLM profile the judge grades through |

## Protocol / contract impact

- `ScorerContext.llm_profiles` — additive, default empty.
- `LLM_PROFILES_EXTRAS_KEY` canonical home moves to `config/llm.py`; `agents._prompt`
  and `composition` re-export the same object, so no import breaks.
- New error types: _none_.

## Backwards-compatibility

- Default judge: identical messages; row metadata gains `parse: "strict"`.
- Calibration records gain `prompt_sha256`; a record without it no longer
  calibrates. No record exists in the repository, and spec-0047 is unreleased.
- `JudgeStatus` gains two defaulted fields; older baselines load.

## Test plan

- `tests/eval/test_llm_judge.py` (byte-identical default, rubric, input rendering,
  digest stability and sensitivity, recovered / strict parse, profile client and
  log level, missing profile, option validation, factory pinning);
  `tests/eval/test_judge_calibration.py` (prompt rules, summary);
  `tests/eval/test_cli_eval.py` (profile end to end, unknown profile, seeding).
- Mutation: drop the prompt-digest rule → the failing-first test goes red.
- Coverage: `eval` / `cli` / `config` / `agents` floors hold.

## Acceptance criteria

- [x] A record for a different prompt never calibrates; the console shows the current digest to copy.
- [x] A judge profile grades through its own client, and the record is checked against its model.
- [x] `make gate` green with every new option unset; no baseline, dataset or lane changed.
