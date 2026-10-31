# ADR-0038: Studio is a two-plane consumer

## Status

Proposed (2026-10-31). The MangoMas-side decision (§3) is implemented by
spec-0039; §1, §2, §4 and §5 bind the Studio repository and need a reviewer
from that side before they are marked Accepted.

## Context

A three-model synthesis proposed MangoMas Studio, a React/TS app over the
FastAPI runtime, and recommended an in-repo executor interface so Claude Code
or the Code Agent Harness could "plug in" as the coder. ADR-0029 already fixes
the opposite: MangoMAS is the cognitive plane, Mango_Code_Agent-Harness is
the authority plane, and every side-effecting arrow originates at the harness
broker. The 2026-09-08 review rejected a role map from cognitive labels to
`implementer` (§3.2). Provider choice is one static `LLMSettings`; the
composition root is the only place a provider may be resolved
(`composition/CLAUDE.md`). Both planes authenticate with a single shared key
and no principal, so "a human approved" cannot record *who*; the harness's
`human_approved` flag has zero first-party callers by DEC-065. The harness
accepts CognitiveSignal 1.0.0 only, while this runtime emits 1.1.0.

Branch state, verified 2026-10-31: the default branch is still
`feat/initial-release`; no `dev` or `qa` exists; a legacy `main` exists and
has diverged (ADR-0036 tags it `archive/main-legacy`). The earlier claim that
`main` was missing was wrong.

## Decision

1. **Studio is a client of two services and owns no execution.** Reads and
   LLM-backed work (chat, plan, review, retrieval, eval reports, workflow
   validation) go to MangoMAS; anything that runs, writes or approves goes to
   the harness, and in v1 Studio does not call the harness at all.
2. **Studio lives in its own repository** (`Mango-Metrics-NLM/MangoMas_Studio`).
   MangoMAS changes are limited to spec-0039 (profiles, `GET /llm/profiles`,
   the existing CORS allow-list).
3. **Provider selection is a roster of profiles** built at startup in the
   composition root and selected by name per run through the existing
   `AgentRequest.metadata` dict. No request-time provider resolution, no
   process-global mutation, no header-driven selection, no DTO or
   protected-path change. A root-level `MANGOMAS_LLM__PROVIDER_ALLOWLIST` is
   enforced at `Settings` parse; it governs the LLM seam only and says so in
   its name (ADR-0033).
4. **Approvals and identity are out of scope for Studio v1.** The first caller
   of `human_approved` requires a harness decision record that amends
   DEC-065's zero-caller guarantee; Studio will not be that caller without it.
5. **The UI ↔ runtime contract for v1 is the existing SSE stream and JSON
   routes.** A2A (spec-0037) is re-evaluated at gate G3 of the delivery plan,
   not adopted by default.

## Consequences

### Positive

- INV-16 holds by construction: a compromised Studio can draft and read, not
  execute. Profiles inherit `ALLOWED_MODELS` and the secrets seam unchanged.
- No protected path is touched, so no `BREAKING-CHANGE` trailer.
- A pipeline carries its profile across stages for free, because the stamp
  rides `response.metadata` and `_pipeline_pass` already forwards it.

### Negative / Trade-offs

- No approval inbox in v1: a Studio user cannot close the propose → dispose
  loop from the UI until identity is decided.
- A third UI surface exists (Gradio demo, harness dashboard, Studio); the
  README identity note must name all three.
- Cross-repo coordination for the CognitiveSignal 1.1.0 bump is a dependency
  outside this repo's control.
- An unknown profile refused before dispatch emits no error metric — the same
  position rule as a pre-stream `AgentNotFound`, and stated in spec-0039.

### Neutral

- `GET /llm/profiles` is a new read-only route under `require_auth`.
- Spec-0037 remains Draft; this ADR neither reopens nor closes it.
- Cross-provider profiles never inherit credentials; an operator sets
  `SECRET_REF` / `API_KEY` / `CREDENTIALS_PATH` per profile.

## Alternatives Considered

- **In-repo executor interface with Claude Code as the first plug-in** —
  rejected: the model-selected write capability ADR-0029 and the §3.2 review
  reject; it also turns `.claude/` developer tooling into a runtime surface.
- **`PATCH /runtime/llm` global swap** — rejected: mutates process state under
  concurrent runs; resolves a provider outside the composition root.
- **`X-LLM-Profile` header modelled on tenancy** — rejected: tenancy's header
  is documented as client-asserted and unbound to any credential (ADR-0033);
  a data-egress policy cannot rest on it.
- **A new `AgentRequest.llm_profile` field** — rejected: the invoke and stream
  bodies are the core `AgentRequest`, a protected path, for exactly the shape
  of need `metadata` exists to cover (ADR-0028's own reasoning).
- **Studio in-tree under `MangoMas_V2/ui/`** — rejected for now:
  `lint-imports`, per-package floors, `frontmatter` and `scripts-coverage` are
  Python-shaped; a JS toolchain would need gate changes on the governance
  surface (ADR-0030).
- **Studio inside Mango_Code_Agent-Harness** (which already runs a Node gate)
  — rejected for v1: housing the UI beside the broker it must not call
  couples release cadence to the authority plane and invites the shortcut §4
  forbids. Revisit at G4 if the approval inbox is approved.
- **`LOCAL_ONLY` boolean with a loopback test** — rejected:
  `docker-compose.yml` uses `host.docker.internal`; a flag named local-only
  that leaves secrets, embeddings and telemetry egress untouched is the
  ADR-0033 defect class.
- **Adopt AG-UI** — deferred: no consumer need demonstrated; the SSE framing
  has a named owner and an OpenAPI snapshot gate.

## References

- Code: `src/mangomas/config/llm.py` (`LLMProfileSettings`,
  `provider_allowlist`), `src/mangomas/config/_root.py` (root validator),
  `src/mangomas/composition/llm.py` (`build_llm_profiles`,
  `_LLMProfileDispatchMixin`), `src/mangomas/agents/_prompt.py`
  (`select_llm_profile`, `resolve_llm`), `src/mangomas/api/routes/llm.py`
- Related ADRs: ADR-0028, ADR-0029, ADR-0030, ADR-0033, ADR-0036, ADR-0037
- Specs: spec-0028, spec-0037, spec-0039
- Delivery plan: `docs/plans/20261031T000000Z-studio-two-plane-delivery-plan.md`
