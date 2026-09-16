# ADR-0033: Names must not promise controls the code does not implement

- **Status:** Accepted
- **Date:** 2026-09-16
- **Revisits:** ADR-0014 (application auth seam), ADR-0012 (workflow HTTP endpoint)
- **Spec:** spec-0035 (to be written)
- **Source:** `docs/analysis/20260916-workflow-governance-audit.md` §3b, §4b, §4c, §9

## Context

Four places in this repository carried a name that a reasonable reader would
take as a stronger guarantee than the code provides. None was a bug; each was a
gap between what a label implies and what it does.

1. **`policy_snapshot_hash`** is `sha256("policy_id:policy_version")` — a
   checksum of two environment variables, not a digest of a policy document.
   It proves nothing about policy content, cannot detect a changed rule, and
   the emitting process computes it itself. `MANGOMAS_SIGNAL__POLICY_SNAPSHOT_HASH`
   is settable to any 64-hex value.
2. **`MANGOMAS_TENANCY__ENABLED`** reads as an isolation boundary. The tenant
   comes from a client-supplied header with nothing binding it to a credential,
   and API auth is a single shared bearer with no principal — so any holder of
   that token can name any tenant and read its history.
3. **`MANGOMAS_WORKFLOW__ENABLED=false`** reads as "workflows are off". It is
   not: `POST /workflows/run` executes a caller-supplied inline `definition`
   regardless, as its own docstring says.
4. **`model_override`** was an unconstrained `str | None`. Nothing declared
   which models a deployment approved, and nothing rejected one that was not.

## Decision

**Where the gap is a documentation gap, close it at the point of belief.**
`policy_snapshot_hash_for` and `mangomas.tenancy` now carry the caveat in their
own docstrings, and `CLAUDE.md`'s config table repeats it where an operator
reads the flag. A caveat in an audit document nobody opens is not a control.

**Where the gap is a missing switch, add the switch and default it to today's
behaviour.**

- `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION` (default `true`) lets a
  deployment require server-configured graphs only.
- `MANGOMAS_LLM__ALLOWED_MODELS` (default empty = unconstrained) is an
  approved-model roster. A non-empty list **must include the base `model`**:
  exempting the default would make the allowlist a loophole rather than a
  control, since the one model guaranteed to be built is the one it would not
  govern. Validated at `Settings` parse, so a misconfiguration surfaces at
  startup rather than at the first agent that happens to opt in.

**An unapproved override raises rather than being skipped.** Silently ignoring
it would run the agent on the *base* model while its configuration claimed
otherwise — a worse outcome than refusing to start.

## Consequences

**What this buys.** A reader of `tenancy.py` or the config table no longer
forms a false belief about isolation. A deployment can now refuse
caller-supplied graphs, and can constrain model selection — which, paired with
the `model` column ADR-0031 opens the way to, is what makes model choice
*reviewable* rather than merely *central*.

**What it does not buy.** Documenting that tenancy is not access control does
not make it access control. Binding a tenant to a credential needs
per-principal auth — a subject, scopes, an audience — which revisits ADR-0014
and is deferred. Until then the honest statement is the deliverable, because a
reader who believes the wrong thing is worse off than one who knows the limit.

**Both new settings default to current behaviour**, so no existing deployment
changes. That is deliberate: a security default flip and a capability addition
in one release makes the release hard to reason about, and the audit's own
finding was that these are *unknown* limits, not unacceptable ones.

## Alternatives considered

- **Default `allow_inline_definition` to `false`.** The safer default, and
  rejected for this change: inline definitions are documented behaviour that
  deployments may rely on, and breaking them silently while claiming to improve
  honesty would be its own kind of dishonesty. Deployments that want it off now
  have one variable.
- **Make `policy_snapshot_hash` real instead of documenting it.** The right
  end state, and it needs a policy *document* to digest, which does not exist
  yet. Documenting the limit now costs nothing and stops a verifier trusting it
  in the meantime.
- **Skip an unapproved `model_override` with a warning.** Rejected; see
  Decision. A warning in a log is not a control, and the divergence between
  configured and actual model is exactly the thing an audit trail should never
  have to reconstruct.
