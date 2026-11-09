<!--
Mango-Mas V2 pull-request template.
Fill every section; the mango-architect agent reviews on these headings.
-->

## Summary

<!-- 1-3 bullets: what changed and why. -->
-

## Changes

<!-- Group by area. Cite file paths. -->
-

## Test plan

- [ ] `python -m ruff check src tests scripts`
- [ ] `python -m ruff format --check src tests scripts`
- [ ] `python -m mypy --strict src tests scripts`
- [ ] `python -m pytest -q` <!-- addopts supply --cov + the global --cov-fail-under=98 -->
- [ ] `python scripts/check_coverage.py`
- [ ] `python scripts/lint_agent_frontmatter.py`
- [ ] Manual smoke (describe): <!-- e.g. ran `mangomas chat "hello"` against LM Studio -->

## ADR

<!-- Link to docs/adr/NNNN-<slug>.md, or write "n/a" for non-architectural changes.
     Required for: new boundaries, provider swaps, composition-root changes,
     breaking contract changes. Template: docs/adr/_template.md -->

n/a

## CHANGELOG

<!-- Link to the new entry under `## [Unreleased]`, or write "n/a" for
     internal-only changes. -->

## Sub-agent reviews

<!-- Tick the routers whose domain this PR touches. The mango-architect agent
     will use these as the canonical review surfaces. -->

- [ ] architect — protocol/layering/ADR audit
- [ ] backend — adapters / orchestrator / errors
- [ ] test-engineer — fakes / hypothesis / integration
- [ ] api-dev — HTTP surface / streaming / schema

## Protected paths

<!-- If this PR touches a path in pyproject.toml [tool.mangomas.governance]
     protected_paths, the trailer `BREAKING-CHANGE: <path> — <rationale>` must
     be in a commit message AND copied here, and the PR merges as a merge
     commit (a squash drops the trailer out of the range the gate walks).
     The <path> must be the bare protected path as the first token. Do not
     write a placeholder such as a bare "none": a first token that is not a
     protected path is an *unscoped* marker and approves every protected path.
     Write "n/a" below when no protected path is touched. -->

n/a

## Notes

<!-- Anything reviewers should know: known risks, follow-ups, deferred items. -->
