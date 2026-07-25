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
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy --strict src tests scripts
python -m pytest -q  # addopts supply --cov + the global --cov-fail-under=95
python scripts/check_coverage.py
python scripts/lint_agent_frontmatter.py  # added in Phase 3
```

```bash
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy --strict src tests scripts
python -m pytest -q  # addopts supply --cov + the global --cov-fail-under=95
python scripts/check_coverage.py
python scripts/lint_agent_frontmatter.py  # added in Phase 3
```

---

## Release Rules

| Rule | Detail |
|------|--------|
| Conventional commits | `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `ci:`. Breaking changes prefixed with `!` (e.g. `feat!:`). |
| CHANGELOG-first | Every user-visible change has a CHANGELOG entry under `## [Unreleased]` before merge. |
| Section ordering | `### Added`, `### Changed`, `### Fixed`, `### Breaking Changes`, `### Deprecated`, `### Removed`. |
| ADR for architecture | Any change that introduces a new boundary, swaps a provider, or alters the composition root needs an ADR in `docs/adr/`. |
| Coverage gate | `scripts/check_coverage.py` is the single source of truth: global 95 % plus per-package floors ranging from 85 % (adapters) to 100 % (`errors.py`, `registry.py`, `core/*`). The pytest `--cov-fail-under=95` addopt in `pyproject.toml` mirrors the global floor. |
| Frontmatter lint | Once Phase 3 lands, `scripts/lint_agent_frontmatter.py` validates every `.agent.md` and `SKILL.md`. Run before pushing. |
| Sub-agent review checkboxes | PR template lists each parent agent (architect, backend, test-engineer, api-dev); tick the ones whose domain you touched. |

---

## Reference

| File | Role |
|------|------|
| `CHANGELOG.md` | Top section is always `## [Unreleased]`; release cuts move it under a dated heading |
| `pyproject.toml` | `version = "..."` field; `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]` |
| `.github/workflows/ci.yml` | Authoritative CI gates (ruff, mypy, pytest with coverage, secret scan) |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR description skeleton (added in Phase 4) |
| `docs/adr/_template.md` | ADR skeleton (added in Phase 4) |
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

## PR Description Template (mirrors `.github/PULL_REQUEST_TEMPLATE.md` once Phase 4 lands)

```markdown
## Summary
<1-3 bullets>

## Changes
<grouped by area>

## Test plan
- [ ] `python -m pytest --tb=short -q`
- [ ] `python -m ruff check src tests scripts`
- [ ] `python -m mypy --strict src tests scripts`
- [ ] `python scripts/check_coverage.py`
- [ ] `python scripts/lint_agent_frontmatter.py`  <!-- added in Phase 3 -->
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
2. `ruff format --check` fails in CI but not locally → run `python -m ruff format src tests scripts` and commit the diff.
3. CHANGELOG conflict on merge → take both sides, re-group entries under the correct sections.
4. ADR number collision → run `ls docs/adr/` and pick the next free number.
5. PR template not auto-applied → confirm `.github/PULL_REQUEST_TEMPLATE.md` exists on the default branch; GitHub picks it up from `main` only.
