# ADR-0030: The protected-path gate reads its policy from the base ref

- **Status:** Accepted
- **Date:** 2026-09-16
- **Supersedes / amends:** extends ADR-0021 (protected-path governance contract)
- **Spec:** spec-0032 (to be written alongside the remaining PR A milestones)
- **Source:** `docs/analysis/20260916-workflow-governance-audit.md` §3, §4a, N1, N2, N3

## Context

ADR-0021 established `scripts/check_protected_paths.py` as the authoritative
protected-path gate, and was explicit that the `PreToolUse` hook beside it is
advisory only. That split is sound. The gate itself had a subtler problem.

The gate loaded `[tool.mangomas.governance]` from the **working tree** — the
same commit it was judging. One branch could therefore both remove a file from
`protected_paths` and edit that file, with no marker, and pass. The verifier
validated the candidate change against a policy the candidate change
controlled.

A governance audit found three further paths to the same outcome, none of which
required touching `pyproject.toml` at all:

- `sitecustomize.py` is auto-imported by any interpreter that can see the repo
  root and writes `PYTEST_ADDOPTS`, which carries `--cov-fail-under` and `-k`.
  Its guard asserted the expected token was *present*, not that nothing else
  had been added, so a gate-disabling token rode along green.
- `eval/discovery.py` let a third-party entry point replace a **built-in**
  scorer or sink last-call-wins. Those registries feed the CI quality gate
  (exit 3) and the regression baseline. `agents/discovery.py` already refused
  the identical collision; the permissive side was the one guarding the gate.
- The `PostToolUse` hook interpolated a **model-chosen** file path into a
  double-quoted word inside a string handed to `sh -c`.

Separately, the `BREAKING-CHANGE` marker was a scope-free token: one marker
anywhere in a PR's commit range approved edits to *every* protected path in it,
including files added in later commits the approver never saw.

## Decision

**1. The gate reads the policy from the base ref.** `check_protected_paths.py`
loads `[tool.mangomas.governance]` from `git show <base-ref>:./pyproject.toml`.
The base ref is state the branch cannot rewrite, so a branch cannot shrink the
set it is judged by. When the base ref has no readable policy — a base branch
predating the table, or a clone too shallow to resolve it — the gate falls back
to the working tree rather than hard-failing, and **announces the fallback on
stderr**. A silent downgrade would reopen exactly this hole.

**2. The governance mechanism is itself protected.** `pyproject.toml`,
`scripts/check_protected_paths.py`, `scripts/_governance.py`,
`src/mangomas/harness/governance.py`, `sitecustomize.py` and `.mcp.json` join
`protected_paths`. `mangomas.harness.governance.GOVERNANCE_SURFACE` names that
set and `unprotected_governance_surface()` reports any member missing from the
policy, so the containment is asserted by a test rather than assumed.

**3. An approval marker may name the path it approves.**
`BREAKING-CHANGE: <protected-path> — <rationale>` binds to that path. A bare
`BREAKING-CHANGE: <rationale>` still approves every touched path, so the change
is additive: every marker in this repo's history keeps working. A marker is
treated as scoped only when its first token is an **exact** protected path;
anything else reads as prose and approves broadly. That asymmetry is
deliberate — a typo must not silently narrow an approval to nothing.

**4. An eval plugin may not replace a built-in by default.**
`MANGOMAS_DISCOVERY_ALLOW_BUILTIN_OVERRIDE` (default `false`) gates it. The
capability is preserved for deployments that legitimately ship a replacement;
only the default changes.

**5. Nothing model-chosen reaches a shell.** The `PostToolUse` hook drops
`sh -c` for two plain `xargs -r` pipelines, where the path is a literal argv
element that is never re-parsed. `--emit-path` additionally refuses to emit a
path containing shell metacharacters.

## Consequences

**What this buys.** Shrinking the protected set no longer helps the branch
doing the shrinking (1); changing the mechanism leaves a record in `git log`
(2); an approval says what it approves (3); the eval gate's inputs cannot be
replaced by an installed package (4); the autofix hook has no shell to inject
into (5).

**What it does not buy, stated plainly.** None of this defends against an edit
to `.github/workflows/ci.yml` or the `Makefile` that removes the gate job.
Only a GitHub-side **required status check** does, and that lives outside this
repository. Decision 2 is visibility, not prevention; decision 1 is the part
that changes outcomes.

**Deliberate exclusions from `GOVERNANCE_SURFACE`.** `Makefile`,
`.github/workflows/ci.yml` and `scripts/lint_agent_frontmatter.py` are *not*
protected. They invoke or mirror the gate but do not define it, and they change
with routine work. A marker requirement on a high-churn file devalues the
marker on the six core contracts: a trailer reviewers stop reading governs
nothing. `lint_agent_frontmatter.py`'s copy of the fallback set is already
pinned against drift by `test_scripts_fallback_matches_the_pyproject_table`.

**Behaviour change.** Decision 4 changes a default: an existing deployment
relying on a plugin overriding a built-in must now set
`MANGOMAS_DISCOVERY_ALLOW_BUILTIN_OVERRIDE=true`. This is the one non-additive
item in the set, and it is a security fix with a one-variable migration.

## Alternatives considered

- **A second marker tier** (`GOVERNANCE-CHANGE`) for the mechanism, separate
  from `BREAKING-CHANGE` for contracts. Rejected: `breaking_change_marker_aliases`
  is not per-path scoped, so a new alias becomes a universal bypass — the
  failure mode the `mango-harness` skill already warns about. Decision 3 gets
  the same precision without a second token.
- **Protecting `Makefile` and `ci.yml` too.** Rejected on marker-fatigue
  grounds; see Consequences.
- **Refusing to run when the base ref has no policy.** Rejected: it converts a
  shallow-clone misconfiguration into a hard CI failure with a confusing
  message, and the loud-fallback path keeps the gate honest without that cost.
