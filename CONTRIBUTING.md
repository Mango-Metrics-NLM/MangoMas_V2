# Contributing to Mango-Mas V2

This file is a map, not a manual. Everything here links to the file that
actually owns the rule, because a second copy of a rule is a rule that will
disagree with the first one within a release.

---

## Before you start

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pip install -e ./mango-integration-contracts
pre-commit install
```

`make help` lists every target. `make gate` runs the whole CI pipeline locally,
in CI's order — run it before opening a PR, not after CI tells you to.

---

## The one rule that will surprise you

Six files are **protected paths**:

| Path | Why |
|---|---|
| `src/mangomas/core/agent.py` | The `Agent` protocol and its request/response contracts |
| `src/mangomas/core/orchestrator.py` | The whole dispatch surface |
| `src/mangomas/core/structured.py` | Structured-output prompt + JSON-recovery helpers |
| `src/mangomas/core/tools.py` | Tool contracts and the call parser |
| `src/mangomas/errors.py` | The typed error hierarchy behind the HTTP status map |
| `src/mangomas/registry.py` | The generic provider store every seam uses |

Touching one requires a `BREAKING-CHANGE` trailer on at least one commit in
the PR. `make protected-paths` enforces it in CI by diffing the PR base
against its head — state you cannot rewrite from inside a session. The
`PreToolUse` hook that warns about it locally is **advisory only** and cannot
be a complete gate: `Bash` and MCP filesystem writes bypass its matcher
entirely. See [ADR-0021](docs/adr/0021-protected-path-governance-contract.md).

The set is defined once, in `pyproject.toml`'s `[tool.mangomas.governance]`
table, and read from there by both `src/mangomas/harness/governance.py` and
`scripts/check_protected_paths.py`.

---

## Workflow

1. **Spec first** for anything non-trivial. Copy `specs/TEMPLATE.md` to
   `specs/NNNN-kebab-slug.md` and fill in Problem / Requirements / Config-env /
   Protocol-contract impact / Backwards-compat / Test plan / Acceptance
   criteria. Specs are thin and deliberately not CI-enforced — see
   [`specs/README.md`](specs/README.md).
2. **ADR when a boundary moves** — a new provider, a new protocol, a new error
   type, a composition-root change. Author from `docs/adr/_template.md`.
3. **Implement**, then **run `make gate`**.
4. **CHANGELOG** — add an entry under `[Unreleased]` naming the test that locks
   the change in. An entry that names no test is a claim with no mechanism.
5. **Open a draft PR** and fill in the template.

---

## Conventions that the gate enforces

You do not need to memorise these; `make gate` will tell you. They are listed
so the failure message makes sense when it arrives.

| Rule | Enforced by |
|---|---|
| `from __future__ import annotations` in every source file | `ruff` isort `required-imports` |
| No hard-coded tunables — everything via `Settings` (`MANGOMAS_*`) | `tests/deploy/test_env_example_contract.py`, both directions plus documented defaults |
| Every adapter satisfies a `@runtime_checkable` Protocol | `mypy --strict` for signatures; layering is code review |
| Backwards-compatible wire contracts | `tests/test_openapi_snapshot.py` + the error-status walk |
| No `unittest.mock` for internal protocols — use `tests/fakes.py` | Code review |
| No domain literals in tests — use `tests.constants` | Code review; config defaults are **re-exported**, never restated |
| No module-level `mangomas.telemetry.get_tracer` | `tests/test_telemetry.py` AST scan over `src/` |
| Every source package has a write-capable owning agent | `tests/tooling/test_corpus_contract.py` |
| Docs' corpus counts, C4 model and coverage-floor table match reality | `tests/tooling/`, `tests/test_check_coverage.py` |

The full design-rule table, with the mechanism that catches each violation
(and an honest "code review (prose-only)" where none exists), is in
[`CLAUDE.md`](CLAUDE.md).

---

## Tests

`docs/testing/regression.md` is the reference. The short version:

```bash
make test              # unit suite + the global 95% floor
make coverage          # per-package floors — the authoritative gate
make gate              # everything CI runs, in CI's order
```

Three habits this repo cares about more than coverage percentage:

- **Prove the guard fails.** A new test must be shown to fail when the thing it
  guards breaks. Revert the fix, watch it go red, restore it. The
  `mango-mutation-proof` skill writes this up.
- **Never skip to get green.** Skips are sanctioned only through the env-gate
  table in `tests.constants`; an ad-hoc `pytest.skip` fails the session
  guard, and so does any `xfail`.
- **Watch the denominator.** A coverage number over the wrong file set passes
  while measuring nothing — the failure mode is fail-open. See the
  `mango-coverage-audit` skill.

Opt-in suites (`RUN_INTEGRATION`, `RUN_LMSTUDIO`, `RUN_VERTEX`, `RUN_POSTGRES`,
`RUN_RAG`, `RUN_EMBEDDINGS_LOCAL`, `RUN_LANGFUSE`, `RUN_GCP_SECRETS`,
`RUN_GCP_TRACE`, `RUN_GITLEAKS`) each have a matching `make` target.

---

## Secrets

`make secret-scan` scans the working tree **and** committed history against
`.gitleaks.toml`. It needs the network and is not part of `make gate`; CI and
the nightly workflow both run it.

If you add an allowlist entry, add the reason next to it — and note that
gitleaks `stopwords` are *substring* matches, so a `password` stopword exempts
`SuperPassword2024Xy`. That is why this repo declares none.
`make gitleaks-selftest` proves the scan can still fail.

---

## Claude Code corpus

`.claude/agents/` and `.claude/skills/` are checked-in, linted, and contract-
tested. **Skills own procedure** (the recipe for doing X); **agents own a
surface** (its boundary and invariants). An agent reaches for a skill for the
how; a skill never delegates to an agent.

Adding an agent means: a `mango-`prefixed file whose `name:` equals its stem,
an entry in `tests.constants`'s roster, and either a skill mapping or a
recorded reason no skill documents it. `make frontmatter` and
`tests/tooling/test_corpus_contract.py` will tell you what is missing.
