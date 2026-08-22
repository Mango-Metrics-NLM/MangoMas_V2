<!--
Template for delivery plans (spec-0022 R13). Copy to
docs/plans/<UTC yyyymmddThhmmssZ>-<kebab-slug>-plan.md — the timestamp prefix
is the existing convention, so plans sort chronologically. Plans record
multi-milestone sequencing; the WHAT lives in specs/, decisions in docs/adr/.
Delete the annotation comments (like this one) on instantiation.
-->

# <Title> — delivery plan

- **Branch:** `<branch>`
- **Date:** <YYYY-MM-DD>
- **Target release:** <version or "rolling">
- **Status:** Draft | In progress | Done
- **Specs:** spec-NNNN<, spec-NNNN>
- **ADRs:** ADR-NNNN <or "none — no boundary change">

## Executive summary

<3–6 sentences: what ships, in what order, and why that order. Name the one
constraint that shaped the sequencing.>

## PR A — <theme> (spec-NNNN)

<!-- One `## PR` block per reviewable unit; one `### Milestone` per landable
     step. Mark finished milestones with a trailing ✅ so the plan doubles as
     the live progress record. -->

### Milestone A0 — <name>

- **Failing test first:** <the test that goes red before this change and green
  after — every milestone starts by proving the gap it closes.>
- **Depends on:** <milestone, or "nothing — parallel-safe". State dependency
  status honestly: "blocked on X" beats an aspirational "ready".>
- <Steps, with file paths.>

### Milestone A1 — <name>

- **Failing test first:** <…>
- **Depends on:** <…>

## Deferred / out of scope

<What was considered and consciously not done, with the recorded decision
(ADR/spec) that re-opening it must revisit — so scope grows only by decision,
never by drift.>

## Verification

```bash
make gate          # the full pre-PR chain, in CI's order
<suite-specific commands, e.g. RUN_INTEGRATION=1 make integration>
```
