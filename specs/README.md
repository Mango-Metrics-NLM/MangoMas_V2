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

| Spec | Title |
|------|-------|
| [0001](0001-telemetry-exporter.md) | Telemetry exporter selection |
| [0002](0002-harness-metrics-exporter.md) | Harness metrics-exporter routing |
| [0003](0003-secrets-strict-mode.md) | `SecretsSettings.strict` + `SecretsResolutionError` |
| [0004](0004-cloud-run-deploy.md) | Cloud Run deployment pipeline |
| [0005](0005-declarative-agent-workflows.md) | Declarative multi-agent workflow graph |
| [0006](0006-dynamic-agent-loading.md) | Dynamic agent loading via entry points |
| [0007](0007-multi-tenancy.md) | Multi-tenancy |
| [0008](0008-workflow-http-endpoint.md) | Workflow HTTP endpoint |
| [0009](0009-otel-metrics.md) | OpenTelemetry metrics (MeterProvider) |
| [0010](0010-application-auth-seam.md) | Application authentication seam |
| [0011](0011-request-backpressure.md) | Request backpressure |
| [0012](0012-conditional-branch-node.md) | Conditional branch node |
| [0013](0013-composite-fan-out-branches.md) | Composite fan_out branches |
| [0014](0014-code-hygiene-modularity.md) | Code hygiene & modularity overhaul |
| [0015](0015-package-decomposition.md) | Package decomposition (deferred spec-0014 scope) |
| [0016](0016-claude-code-ecosystem-tooling.md) | Claude Code ecosystem tooling integration |
| [0017](0017-protected-path-governance.md) | Protected-path governance contract |
| [0018](0018-live-claude-code-corpus.md) | Live Claude Code corpus (`.github/` → `.claude/`) |

Note: spec and ADR numbers advance independently ("next free integer" applies
within each directory), so the two sequences do not line up. On this branch
`docs/adr/` has no `0006`/`0007`. The `main` branch allocated those numbers
differently (`0007` = declarative agent workflows, `0011` = harness-hook-hardening)
— reconciled by `docs/adr/0021-protected-path-governance-contract.md` (supersedes
`main`'s ADR-0011) and `docs/adr/0023-workflow-implementation-reconciliation.md`
(supersedes `main`'s ADR-0007). `docs/adr/` also has no `0022`: that number was
forward-referenced by ADR-0021 for a separate "harness governance port" ADR that
ADR-0021 ended up absorbing, so it was never written and is left as a gap rather
than reused. Next free spec on this branch: **0019**; next free ADR: **0025**.
