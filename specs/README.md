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

Note: spec and ADR numbers advance independently ("next free integer" applies
within each directory), so the two sequences do not line up. On this branch
`docs/adr/` has no `0006`/`0007`. The `main` branch allocated those numbers
differently — see the ADR-renumbering item in [`NEXT_STEPS.md`](../NEXT_STEPS.md)
before reconciling the two lines.
