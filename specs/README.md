# Specs — spec-driven development for Mango-Mas V2

This directory holds **feature specifications**. A spec is a short, reviewable
design written **before** the code for any non-trivial change. It answers *what*
and *why* (and the contract/backwards-compat impact); the *how* lives in the
diff, and the *decision* — when a boundary changes — lives in an
[ADR](../docs/adr/).

## Why this exists

The project already has `docs/adr/` (decisions) and `docs/plans/` (multi-step
delivery plans). Specs sit between them: one file per feature, capturing the
requirements and acceptance criteria so implementation and tests have a fixed
target. Specs are intentionally **thin** and **not CI-enforced** — they are a
thinking tool, not a gate.

## Workflow (spec-before-code)

1. **Copy** [`TEMPLATE.md`](TEMPLATE.md) to `specs/NNNN-kebab-slug.md`
   (next free integer, zero-padded to 4 digits — mirror the ADR numbering
   convention).
2. **Fill it in** — Problem, Requirements, Config/env additions,
   Protocol/contract impact, Backwards-compat, Test plan, Acceptance criteria.
3. **Link an ADR** when the change introduces or moves a boundary (new provider,
   new protocol, new error type, composition-root change). Author the ADR from
   `docs/adr/_template.md`.
4. **Implement** against the spec; keep the spec updated if the design shifts.
5. **Release** via the `mango-release` skill — reference the spec + ADR from the
   `CHANGELOG.md` entry.

## Relationship to existing docs

| Artifact | Answers | Lives in |
|----------|---------|----------|
| Spec | What & why for one feature; acceptance criteria | `specs/` |
| ADR | The decision when a boundary changes | `docs/adr/` |
| Plan | Multi-milestone delivery sequencing | `docs/plans/` |
| CHANGELOG | What shipped, per release | `CHANGELOG.md` |

## Index

| Spec | Title | Status |
|------|-------|--------|
| 0005 | Declarative multi-agent workflow graph | In progress |
| 0006 | Dynamic agent loading via entry points | Draft (stub) |
| 0007 | Multi-tenancy | Draft (stub) |

Specs `0001`–`0004` are reserved for the telemetry-exporter, harness
metrics-exporter, secrets-strict-mode, and Cloud Run deploy features and will be
authored as those milestones begin.
