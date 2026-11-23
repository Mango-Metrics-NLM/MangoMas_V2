# ADR-0042: Governed self-improvement loop

## Status

Proposed (2026-11-23). Analysis:
[deep dive](../analysis/20261123-self-improvement-loop-deep-dive.md); sequencing:
[delivery plan](../plans/20261123T000000Z-governed-self-improvement-loop-plan.md);
specs [0052](../../specs/0052-loop-trusted-base-scope-check.md),
[0053](../../specs/0053-machine-readable-backlog.md),
[0054](../../specs/0054-architecture-proposal-pipeline.md) and
[0055](../../specs/0055-loop-evidence-ledger.md).

## Context

A proposal asks MangoMas to run a loop that improves its own repository.
Verified at `dev@4e116b9`: loop acceptance reads only text the agent wrote; the
protected set is 13 exact paths that exclude CI workflows, the `Makefile`, gate
scripts, `tests/` and `evals/`; a `pull_request` run executes the PR's own copy
of all of these; the repository is public, so no committed eval is held out;
and `claude/*` PRs carry the maintainer's identity. The literature finds that
agents game visible checks, that score-based gates erode redundant safeguards,
and that most measured gain from self-edited harnesses is overfitting or extra
compute.

## Decision

1. **Authority split.** Claude Code proposes and implements; code from the
   base branch and checks outside this repository verify; the maintainer
   approves proposals and merges. No guarantee may depend on an agent behaving
   well.
2. **Trusted base.** A glob-capable, loop-only policy with two branch classes:
   proposal branches may touch only design records, the backlog and contract
   tests; implementation branches only an approved item's `allowed_paths`. A
   scope check enforces it from the default branch, never executes PR code,
   fails closed, and is a required check (spec-0052).
3. **Pre-registered acceptance.** Work starts only from an approved backlog
   item (spec-0053) whose contract tests merged first and are hash-pinned.
   Changed lines must be killed by those tests under mutation; held-out checks
   live outside this repository; LLM reviewers are reported, not gated.
4. **Propose stage.** Architecture requests run intake, evidence retrieval,
   requirements / existing-decision / architecture-evidence analysts, design,
   then critic and security review, producing a proposal that code assembles
   and citation-checks before human approval (spec-0054). First substrate: a
   saved Claude Code workflow.
5. **Evidence-gated stages.** Shadow, L0 and L1 are promoted by the maintainer
   on an audited false-accept bound and an escaped-defect rate no worse than
   the human baseline, from a ledger derived from GitHub and CI (spec-0055).
   L2 is an experiment with a kill criterion.

## Consequences

### Positive

- The loop can start in Shadow with no trust placed in any agent.
- The propose stage pays off on its own, for every architecture request.
- Reuses base-ref policy reads (ADR-0030), the guard probe (spec-0033),
  `json_field` (ADR-0034), `pass^k` (spec-0046) and reported-not-gated
  judges (spec-0047).

### Negative / Trade-offs

- Maintainer audit time limits throughput.
- `scripts/`, `tests/` and the eval and harness instruments stay out of loop
  reach; widening that needs an amendment to this ADR.
- Needs GitHub admin action (required checks) and, from M7, a private
  repository for held-out checks.
- A second policy beside `protected_paths`, accepted to keep ADR-0030's marker
  economy.

### Neutral

- `protected_paths`, the trailer rules and ADR-0036 promotion are unchanged.
- Composite `loop` bodies and a MangoMas port of the propose stage become an
  optional product track.

## Alternatives Considered

- **Close the loop on `make gate` now:** rejected because the loop would write
  its own tests and a PR can rewrite the gate.
- **Widen `protected_paths` to CI, tests and evals:** rejected because of
  marker fatigue (ADR-0030), and a trailer is no machine boundary for an agent
  writing under the maintainer's identity.
- **MangoMas on local models as implementer:** rejected for now because weaker
  models game visible tests more, and it first needs composite loops and
  multi-file staging.
- **An LLM reviewer as the gate:** rejected because monitors missed 35–58% of
  cheats on realistic tasks.

## References

- Code: `pyproject.toml:147-167`, `scripts/check_protected_paths.py:165-171`
  and `:191`, `scripts/_governance.py:170-175`,
  `src/mangomas/workflow/graph.py:51-57`, `.github/workflows/ci.yml:243-244`
- Related ADRs: ADR-0011, ADR-0021, ADR-0029, ADR-0030, ADR-0034, ADR-0036,
  ADR-0039, ADR-0040, ADR-0041
- External links: the bibliography in section 14 of the analysis
