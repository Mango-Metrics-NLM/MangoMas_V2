---
name: mango-release
description: >
  Releasing changes in Mango-Mas V2. Use when: preparing a CHANGELOG
  entry, drafting a PR description, choosing a conventional-commit type,
  bumping the project version, or running the pre-release checklist
  (ruff, mypy, pytest with coverage gate, frontmatter linter). Covers the
  CHANGELOG layout, version-bump rules in pyproject.toml, the PR
  template, and the ADR template.
argument-hint: "Describe the change being released (e.g. 'new Vertex AI adapter') or paste your draft commit message"
---

# Mango-Mas Release Skill

## When to Use

- Draft a CHANGELOG entry for a new feature, fix, or breaking change
- Compose a conventional commit message
- Write a PR description using the project template
- Run the pre-release / pre-merge checklist
- Decide whether a change needs an ADR
- Bump the version in `pyproject.toml`

---

## Quick Commands

```powershell
# Pre-merge checklist (must all pass)
pre-commit run --all-files  # ruff + ruff-format + mypy(src) + file hygiene
python -m ruff check src tests scripts eval_harness_bridge/src
python -m ruff format --check src tests scripts eval_harness_bridge/src
python -m mypy --strict src tests scripts eval_harness_bridge/src
python -m pytest -q  # addopts supply --cov + the global --cov-fail-under=95
python scripts/check_coverage.py
python scripts/lint_agent_frontmatter.py
```

```bash
pre-commit run --all-files  # ruff + ruff-format + mypy(src) + file hygiene
python -m ruff check src tests scripts eval_harness_bridge/src
python -m ruff format --check src tests scripts eval_harness_bridge/src
python -m mypy --strict src tests scripts eval_harness_bridge/src
python -m pytest -q  # addopts supply --cov + the global --cov-fail-under=95
python scripts/check_coverage.py
python scripts/lint_agent_frontmatter.py
```

---

## Release Rules

| Rule | Detail |
|------|--------|
| Conventional commits | `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `ci:`. Breaking changes prefixed with `!` (e.g. `feat!:`). |
| CHANGELOG-first | Every user-visible change has a CHANGELOG entry under `## [Unreleased]` before merge. |
| Section ordering | `### Added`, `### Changed`, `### Fixed`, `### Breaking Changes`, `### Deprecated`, `### Removed`. |
| ADR for architecture | Any change that introduces a new boundary, swaps a provider, or alters the composition root needs an ADR in `docs/adr/`. |
| Pre-commit parity | `pre-commit run --all-files` is the cheapest way to reproduce CI's lint/format/type gates locally. `ruff` and `mypy` are **exact-pinned** in `pyproject.toml`'s dev extra in lockstep with the hook `rev:`s in `.pre-commit-config.yaml` — bump both together, or the hook and CI disagree. The mypy hook is scoped to `src/`; CI type-checks `src tests scripts eval_harness_bridge/src`. |
| Lint/type surface | CI runs ruff and mypy over `src tests scripts eval_harness_bridge/src`. Omitting `eval_harness_bridge/src` locally is the usual "green locally, red in CI" cause. |
| Coverage gate | `scripts/check_coverage.py` is the single source of truth: global 95 % plus per-package floors ranging from 85 % (adapters) to 100 % (`errors.py`, `registry.py`, `core/*`). The pytest `--cov-fail-under=95` addopt in `pyproject.toml` mirrors the global floor. |
| Frontmatter lint | `scripts/lint_agent_frontmatter.py` validates every `.agent.md` and `SKILL.md` (and gates protected-core paths on a `BREAKING-CHANGE` marker). Run before pushing. |
| Sub-agent review checkboxes | PR template lists each parent agent (architect, backend, test-engineer, api-dev); tick the ones whose domain you touched. |

---

## Reference

| File | Role |
|------|------|
| `CHANGELOG.md` | Top section is always `## [Unreleased]`; release cuts move it under a dated heading |
| `pyproject.toml` | `version = "..."` field; `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]` |
| `.github/workflows/ci.yml` | Authoritative CI gates (ruff, mypy, pytest with coverage, secret scan) over `src tests scripts eval_harness_bridge/src` |
| `.pre-commit-config.yaml` | ruff / ruff-format / mypy / file-hygiene hooks; `rev:`s kept in lockstep with the exact `ruff==` / `mypy==` pins in `pyproject.toml` |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR description skeleton |
| `docs/adr/_template.md` | ADR skeleton |
| `docs/adr/0001-cloud-targets.md` | Reference ADR — current cloud-target matrix |
| `scripts/check_coverage.py` | Per-package coverage floors (errors/registry/core 100%, agents 95%, etc.) |

---

## CHANGELOG Entry Template

```markdown
## [Unreleased]

### Added
- `<feature description>`. References: `<file paths>`.

### Changed
- ...

### Fixed
- ...

### Breaking Changes
- `<what broke>`. Migration: `<one-line guide>`. See ADR-NNN.
```

---

## PR Description Template (mirrors `.github/PULL_REQUEST_TEMPLATE.md`)

```markdown
## Summary
<1-3 bullets>

## Changes
<grouped by area>

## Test plan
- [ ] `python -m pytest --tb=short -q`
- [ ] `pre-commit run --all-files`
- [ ] `python -m ruff check src tests scripts eval_harness_bridge/src`
- [ ] `python -m mypy --strict src tests scripts eval_harness_bridge/src`
- [ ] `python scripts/check_coverage.py`
- [ ] `python scripts/lint_agent_frontmatter.py`
- [ ] Manual smoke (describe)

## ADR
<link to docs/adr/NNN-... or "n/a">

## CHANGELOG
<link to the new entry>

## Sub-agent reviews
- [ ] architect — reviewed
- [ ] backend — reviewed
- [ ] test-engineer — reviewed
- [ ] api-dev — reviewed
```

---

## Workflow

1. Stage changes; run the full pre-merge checklist (commands above).
2. Add a `## [Unreleased]` entry to `CHANGELOG.md` under the appropriate section.
3. Commit with a conventional-commit message (one-line subject, multi-line body if needed).
4. Push to the feature branch.
5. Open a draft PR using the template; fill every section.
6. If architectural: open an ADR under `docs/adr/<NNN>-<slug>.md` using `_template.md`.
7. Request reviews from the sub-agents whose domain the PR touches.
8. Mark the PR ready when all checks are green.

---

## Constraints

- DO NOT skip the CHANGELOG entry — CI doesn't enforce it, but the architect sub-agent does on review.
- DO NOT bump the version without an explicit `chore(release): vX.Y.Z` commit.
- DO NOT use `--no-verify` or `-c commit.gpgsign=false` to push past hooks.
- DO NOT merge with the coverage gate failing — fix the gap, don't lower the floor.
- DO NOT release breaking changes without an ADR and a `### Breaking Changes` block.

---

## Diagnosing Failures

1. Coverage gate fails per-package → run `pytest --cov-report=term-missing` and read the missing-lines column for the failing package.
2. `ruff format --check` fails in CI but not locally → you probably omitted a path; run `python -m ruff format src tests scripts eval_harness_bridge/src` and commit the diff. If the *findings* differ rather than the paths, your local ruff drifted from the `ruff==` pin — reinstall the dev extra so it matches the `.pre-commit-config.yaml` rev.
3. CHANGELOG conflict on merge → take both sides, re-group entries under the correct sections.
4. ADR number collision → run `ls docs/adr/` and pick the next free number.
5. PR template not auto-applied → confirm `.github/PULL_REQUEST_TEMPLATE.md` exists on the default branch; GitHub picks it up from `main` only.
