# Analysis: "Ian's SSD Template" governance pack — applicability to Mango-Mas V2

- **Date**: 2026-08-22
- **Source**: Google Drive folder ["Ian's SSD Template"](https://drive.google.com/drive/folders/1hJa0MnR8uSpAc69X1Jemimas9TulNw-o)
  (25 files, ~90 KB), an "Agentic Development Governance Templates" pack distilled
  from a project called **Edge-AI-VII** — a control plane + engineering harness for
  keeping autonomous coding agents inside constraints.
- **Method**: every file was downloaded and read; applicability was then assessed
  by the repo's own agent corpus in parallel — the four routers
  (`mango-architect`, `mango-backend`, `mango-test-engineer`, `mango-api-dev`)
  plus the `mango-harness-dev` specialist, each against the surface it owns.
  This document synthesizes those five reviews.

---

## 1. What the pack contains

The pack implements a "Decide, then Execute" pipeline built from seven
interlocking mechanisms. Grouped by domain:

| Group | Files |
|---|---|
| Control plane | `PROJECT-CHARTER-TEMPLATE.md`, `DECISION-LOG-TEMPLATE.md`, `project-governance-SKILL-TEMPLATE.md`, `Ians-Governance-Overview` (Doc), `Agentic-Governance-Tracker` (Sheet: backlog / traceability / gates / risk register / gate-run log) |
| Change package (OpenSpec quartet) | `proposal-TEMPLATE.md`, `design-TEMPLATE.md`, `spec-delta-TEMPLATE.md` (SHALL + WHEN/THEN scenarios as test oracles), `tasks-TEMPLATE.md` |
| Scope traceability | `REQUIREMENT-TRACEABILITY-TEMPLATE.md` (linted matrix; Green rows must cite collecting pytest node ids), `single-source-projections-TEMPLATE.py` (one data module → roadmap md + Jira CSV, byte-drift gated), `jira-import-TEMPLATE.csv` |
| Subagents | `spec-implementer-TEMPLATE.md` (one task, strict TDD), `adversarial-reviewer-TEMPLATE.md` (severity enum, confidence tags, 2-fix-cycle cap, red-stage mode) |
| Hooks / guards | `settings-TEMPLATE.json`, `pretooluse-guard-TEMPLATE.sh` (fail-closed Bash guard), `pre-push-scan-TEMPLATE.sh` (remote allowlist + governed-path report), `install-hooks-TEMPLATE.sh` |
| Gates | `Makefile-TEMPLATE` (single-source gate runner, two-pass gitleaks), `ci-workflow-TEMPLATE.yml` (CI-calls-make, env-indirection, SHA-pinning), `conftest-zero-skip-TEMPLATE.py`, `pyproject-gates-TEMPLATE.toml`, `test_governance_meta-TEMPLATE.py` (four meta-test patterns) |

## 2. Overall verdict

Mango-Mas V2 already implements the pack's *central* ideas, usually in enforced
form: single-source gate runner (`make gate`), CI→Makefile parity **with a real
meta-test** (`tests/deploy/test_ci_make_parity.py`, spec-0021), protected-path
governance anchored in git history rather than an editable log (ADR-0021),
per-package coverage floors stronger than the pack's single `fail_under = 90`,
and a documented advisory-vs-authoritative hook split that restates the pack's
own "a guard header that claims to be the last word is a defect" rule.

The pack's *lock-everything control plane* (decision log that mechanically gates
work, charter budgets, tracker workbook, universal spec-implementer agent) does
**not** transfer: it conflicts with two recorded decisions — `specs/README.md`
("specs are a thinking tool, not a gate") and ADR-0024 (surface-owning
specialists, no auto-delegating implementer). What transfers is its
**mechanism-naming discipline** and roughly a dozen targeted assets, several of
which land on gaps the analysis verified in this repo.

## 3. Verified defects surfaced during the analysis

Side findings from comparing the pack against the tree — real today, regardless
of whether any template is adopted:

1. **`scripts/harness_session_start.py` cannot honor its own contract.** It
   imports `httpx` and `mangomas.*` at module scope, so on the exact machine its
   warnings exist for (fresh web session, no venv) it dies with
   `ModuleNotFoundError` / exit 1 — its "always returns `EXIT_OK`" contract is
   unmeetable. Fix: defer imports and report "probe unavailable" (the same
   degradation pattern `harness_config_audit.py` already uses).
2. **`make secret-scan` runs the deprecated, history-only `gitleaks detect`.**
   A working-tree credential (uncommitted `.env`) passes. Since gitleaks v8.19
   the supported commands are `gitleaks git` (history) and `gitleaks dir`
   (working tree); neither subsumes the other.
3. **`deploy.yml` interpolates event payload into a credentialed shell.**
   Line ~45 expands `${{ github.event.release.tag_name || github.sha }}` inside
   a `run:` body in the only job holding `id-token: write`. `eval-gate.yml`
   already practices env-indirection and says so; `ci.yml` is clean.
4. **No GitHub Action is SHA-pinned.** All `uses:` are mutable version tags,
   including third-party `codecov/codecov-action@v4` (real supply-chain
   history) and the WIF-credentialed `google-github-actions/*`.
5. **Doc/config drift**: ADR-0021 and the `mango-harness` skill describe the
   legacy `--check-protected-paths` staged-diff mode as "retained for
   pre-commit", but `.pre-commit-config.yaml` never wires it.
6. **Untested contracts found by the pack's "linted, not hoped" lens**:
   no OpenAPI / `model_json_schema()` snapshot test for the DTO surface; no
   test that a mid-stream SSE failure ends the response without a `done` frame
   (asserted in three prose places, tested nowhere); no exhaustive
   `MangomasError.__subclasses__()` → intended-status walk; no subprocess test
   proving `tests/conftest.py`'s collection gate actually skips/unskips (the
   `ids=[f"make-{t}"]` workaround in `test_ci_make_parity.py` proves this rot
   vector has already bitten once).

## 4. Consolidated adoption roadmap

### Tier 1 — small, high-value, no recorded-decision conflict

| # | Action | From | Landing spot |
|---|---|---|---|
| 1 | Two-pass gitleaks (`dir` + `git`, drop `detect`) + parity-test assertion | Makefile-TEMPLATE | `Makefile` `secret-scan`, `tests/deploy/test_ci_make_parity.py` |
| 2 | Env-indirect `release.tag_name` in `deploy.yml` + a "no `${{ github.event.* }}` in `run:`" contract test | ci-workflow-TEMPLATE | `.github/workflows/deploy.yml`, `tests/deploy/` |
| 3 | Defer module-scope imports in `harness_session_start.py` so `EXIT_OK` always holds | settings-TEMPLATE's warn-and-continue posture | `scripts/harness_session_start.py`, `tests/test_harness_session_start.py` |
| 4 | Deny MCP filesystem-write / git-mutation tools in `permissions.deny` — the only in-session-authoritative layer; partially closes ADR-0021's conceded MCP gap | settings-TEMPLATE | `.claude/settings.json` |
| 5 | SHA-pin third-party actions first (`codecov-action`, `google-github-actions/*`), with a bump strategy | ci-workflow-TEMPLATE | all three workflows |
| 6 | Exact-pin `pytest-cov` (and optionally `coverage`) with the lockstep-comment idiom — protects the denominator `test_check_coverage.py` guards | pyproject-gates-TEMPLATE | `pyproject.toml` dev extras |

### Tier 2 — adapt to this repo's conventions (each ~0.5–2 days; thin spec per convention)

| # | Action | From | Landing spot |
|---|---|---|---|
| 7 | Subprocess meta-test proving the collection gate + zero-skip guard actually fire | test_governance_meta pattern 4 | new `tests/tooling/test_collection_gate.py` |
| 8 | Zero-skip / xfail / xpass guard, escalate-only, with the nine env-gate reasons allowlisted and single-sourced (repo currently has ~25 gated skips, zero xfail — the ratchet is free now) | conftest-zero-skip-TEMPLATE | `tests/conftest.py` + reason constants |
| 9 | WHEN/THEN "Scenarios" section (optional) + the both-directions fail-closed rule in the spec template — pre-declares the fail-open defect class specs 0020/0021 hunted by hand | spec-delta-TEMPLATE | `specs/TEMPLATE.md` |
| 10 | Adversarial-review protocol: severity enum, confidence tags, 2-fix-cycle cap with human escalation, red-stage mode — folded into `mango-architect`'s output format or a new read-only agent per ADR-0024 (explicit tools, no Bash, no auto-delegation phrasing) | adversarial-reviewer-TEMPLATE | `.claude/agents/` + corpus-contract updates |
| 11 | Advisory (never exit-2) Bash-command protected-path check: extend `--hook pre-tool-use` to emit `"ask"` when `tool_input.command` mentions a protected path, TOML-sourced, stdlib-only | pretooluse-guard-TEMPLATE | `scripts/lint_agent_frontmatter.py`, `.claude/settings.json`, `tests/constants.py::PREEXISTING_HOOKS` |
| 12 | Config-table drift test: CLAUDE.md env-var rows ⊇⊆ `mangomas.config` Settings fields (escalate to a full generator only if prose drift recurs) | single-source-projections-TEMPLATE | new `tests/tooling/test_config_docs_contract.py` |
| 13 | `docs/plans/_template.md` — milestones, failing-tests-first, honest dependency statements (plans are the only artifact in the specs/ADR/plans triad without a template) | tasks-TEMPLATE | `docs/plans/` |
| 14 | Targeted contract tests from §3.6: OpenAPI/DTO schema snapshot; mid-stream SSE failure; exhaustive error-status walk | spec-delta + traceability lens | `tests/` (route to `mango-schema-evolution`, `mango-sse-streamer`, `mango-error-taxonomy-dev`) |
| 15 | "Enforced by" column in CLAUDE.md's Key Design Rules — each invariant names its test/CI job/hook; the exercise exposes which invariants are prose-only | PROJECT-CHARTER-TEMPLATE | `CLAUDE.md` |

### Tier 3 — deferred / conditional (needs a deliberate decision first)

- **Spec traceability lint** (`Verified by: <node id>` per acceptance criterion,
  linted only when cited): feasible and cheap, but making Green-cites-a-collecting-node-id
  a CI gate reverses `specs/README.md`'s "not CI-enforced" stance — **needs a new
  ADR** before implementation (forward-only, lenient variant recommended if pursued).
- **Native pre-push hook + installer** (remote allowlist + governed-path
  report): adopt only if off-canonical-remote pushes enter the threat model;
  allowlist must live in `[tool.mangomas.governance]`, never a parallel txt file;
  CI stays authoritative (`--no-verify`, fresh clones).
- **`gate-full: gate secret-scan`** target for networked pre-PR runs (keeps
  `gate` offline by design); optional local-binary reuse in `secret-scan`.
- **`pip-audit` / `.SHELLFLAGS` pipefail hardening**: cheap, adjacent, not urgent.
- **Specs-index contract test** (files ↔ `specs/README.md` index rows, derived
  "next free" counters) — keep advisory-light to respect the thin-spec policy.

### Skip — redundant here, or conflicts with a recorded decision

| Asset | Reason |
|---|---|
| Decision log with mechanical gating | Conflicts with the thin-spec stance; the repo's stronger mechanical anchor is git history (commit trailers, ADR-0021). Borrow one habit: ADR entries stating what they do **not** decide (ADR-0024 already does this). |
| `spec-implementer` subagent | Direct conflict with ADR-0024's surface-ownership model and description-phrasing rules. |
| Governance skill + phase gate table | CLAUDE.md is the constitution and is auto-loaded — a stronger delivery channel; gate mechanics already exist as CI. |
| Charter budgets / CONFIRM-FIRST / repo-privacy CI assertion | No referent: no legal gate, no budget culture, no privacy requirement recorded. |
| Tracker workbook, Jira CSV, proposal/design templates, full traceability matrix | Second hand-maintained sources of truth — the repo's named anti-pattern; specs/ADRs/plans already cover the ground. |
| Fail-closed (exit-2) PreToolUse guard | ADR-0021 explicitly rejected a hard block at this layer ("performative rather than real"); the Bash matcher would still miss MCP writes. Tier-1 #4 + Tier-2 #11 capture the value without the reversal. |
| Template coverage/mypy numbers, zero-skip as-is, CI-through-make meta-test | The repo is ahead on every one of these axes. |

## 5. Notes on sequencing

- Tier 1 items 1–3 are independent bug-class fixes; 1 and 2 could share one
  "CI/build hardening" spec (next free spec number per `specs/README.md`).
- Tier 2 items 7 + 8 share a test surface and are best landed together.
- Every hook-touching change (Tier 1 #4, Tier 2 #11) must update
  `tests/constants.py::PREEXISTING_HOOKS` and pass the `ConfigChange` audit.
- The pack's documentation discipline — dated, commit-stamped measurements and
  an explicit accepted-false-positives list on any guard — is worth borrowing
  in whatever subset lands; it matches the existing `mango-mutation-proof` ethos.
