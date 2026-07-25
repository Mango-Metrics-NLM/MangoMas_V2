<!--
Mango-Mas V2 pull-request template.
Fill every section; the architect sub-agent reviews on these headings.
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
- [ ] `python -m pytest -q` <!-- addopts supply --cov + the global --cov-fail-under=95 -->
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

<!-- Tick the parents whose domain this PR touches. The architect sub-agent
     will use these as the canonical review surfaces. -->

- [ ] architect — protocol/layering/ADR audit
- [ ] backend — adapters / orchestrator / errors
- [ ] test-engineer — fakes / hypothesis / integration
- [ ] api-dev — HTTP surface / streaming / schema

## Notes

<!-- Anything reviewers should know: known risks, follow-ups, deferred items. -->
