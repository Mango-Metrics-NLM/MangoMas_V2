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
protected set is 13 exact paths that exclude CI workflows, the `Makefile`, most
gate scripts, `tests/` and `evals/`; a `pull_request` run executes the PR's own
copy of all of these; the repository is public, so no committed eval is held
out; and Claude Code sessions act with the maintainer's GitHub identity and
permissions. Recent studies report that agents game checks they can see or
edit, that score-based gates erode redundant safeguards, and that much of the
measured gain from self-edited harnesses is overfitting or extra compute.

## Decision

1. **Authority split.** Claude Code proposes and implements; code from the
   base branch and checks outside this repository verify; the maintainer
   approves proposals and merges. No guarantee may depend on an agent behaving
   well: every safety property is enforced by the scope check, so whatever
   starts sessions matters only for liveness.
2. **Trusted base and scope check.** A loop-only policy names the files the
   loop may never touch, including every tool-configuration file at any depth
   (spec-0052). A scope check enforces it from the default branch, never
   executes PR code, fails closed, and is a required check pinned to its
   source. A PR's class comes from something the session cannot choose, and
   the implementer's credential cannot write workflows, statuses, checks,
   variables or administration. Proposal branches may only add records;
   implementation branches may only change an approved item's
   `allowed_paths`, within limits on size, concurrency, dependencies and
   stage.
3. **Pre-registered acceptance.** Work starts only from an approved backlog
   item (spec-0053) whose acceptance was fixed first: hash-pinned contract
   tests, or the unchanged suite for a behaviour-preserving item. From L1,
   changed lines must also be killed under diff-scoped mutation. Held-out
   checks live outside this repository. LLM reviewers never gate, which is
   stricter than spec-0047's "reported, not gated, until calibrated".
4. **Propose stage.** Architecture requests run intake, evidence retrieval,
   requirements / existing-decision / architecture-evidence analysts, design,
   then critic and security review. Code assembles and citation-checks the
   proposal before human approval (spec-0054). First substrate: a saved Claude
   Code workflow.
5. **Evidence-gated stages.** The maintainer promotes Shadow, L0 and L1 on an
   audited false-accept bound and, from L0 on, an escaped-defect rate no worse
   than the current-process baseline, using a ledger derived from GitHub and CI
   (spec-0055). L2 is an experiment with a kill criterion.

## Consequences

### Positive

- The loop can start in Shadow with no trust placed in any agent.
- The propose stage pays off on its own, for every architecture request.
- Reuses base-ref policy reads (ADR-0030), the guard probe (spec-0033),
  parsed-JSON acceptance (spec-0032, spec-0034), `pass^k` (spec-0046) and the
  judge-demotion pattern (spec-0047).

### Negative / Trade-offs

- Maintainer audit time limits throughput.
- The trusted base is broad (`scripts/`, `tests/`, `core/`, both instruments,
  every tool-configuration file), so the first work area is narrow, and items
  that need companion edits in the trusted base stay human work.
- The loop cannot run until a dedicated identity or a verified branch
  assignment exists and the credential checks pass. That needs GitHub admin
  work and, from M7, a private repository.
- A second policy beside `protected_paths`, accepted to keep ADR-0030's marker
  economy; the loop checker's own two files join the protected set.

### Neutral

- The trailer rules and ADR-0036 promotion are unchanged.
- Composite `loop` bodies and a MangoMas port of the propose stage become an
  optional product track.

## Alternatives Considered

- **Close the loop on `make gate` now:** rejected because the loop would write
  its own tests, and a PR can rewrite the gate.
- **Widen `protected_paths` to CI, tests and evals:** rejected because of
  marker fatigue (ADR-0030), and a trailer is no machine boundary for an agent
  writing with the maintainer's identity.
- **Classify loop PRs by branch name alone:** rejected because the session
  chooses its own branch name.
- **MangoMas on local models as implementer:** rejected for now because it
  first needs composite loops and multi-file staging, and this repo records a
  local model that does not reliably emit the tool-call schema
  (`NEXT_STEPS.md`).
- **An LLM reviewer as the gate:** rejected because monitors missed 35–58% of
  cheats on realistic tasks.

## References

- Code: `pyproject.toml:147-167`, `scripts/check_protected_paths.py:165-171`
  and `:191`, `scripts/_governance.py:170-175`,
  `src/mangomas/workflow/graph.py:51-57` and `:82-87`,
  `.github/workflows/ci.yml:243-244`
- Related ADRs: ADR-0011, ADR-0019, ADR-0021, ADR-0029, ADR-0030, ADR-0033,
  ADR-0034, ADR-0035, ADR-0036, ADR-0039, ADR-0040, ADR-0041
- External links: the bibliography in section 14 of the analysis
