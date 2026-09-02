# Spec-0028: Per-agent LLM model override

- **Status:** Implemented
- **Linked ADR:** ADR-0028
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`AgentSettings.model_override` (`src/mangomas/config/agents.py`) has been
declared since spec-0014 but was documented as "Reserved — not read by any
agent yet," because activating it needs a composition-layer change: today
exactly one shared `LLMClient` instance (`ctx.llm`) is built once in
`build_orchestrator` and handed to every agent via one shared
`AgentContext`. `NEXT_STEPS.md` (Phase 1, "make the advertised product
real") and `docs/analysis/20260822-next-steps-roadmap-analysis.md` (item
1.5, decision D4b) both list `MODEL_OVERRIDE` wiring as open work. This spec
activates the field: an operator sets
`MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE=<model>` and that one agent uses a
different model than the shared default, with every other agent and every
existing deployment unaffected.

## Requirements

- An agent whose `model_override` is set (and differs from
  `MANGOMAS_LLM__MODEL`) uses a distinct `LLMClient` built for that model,
  on the same provider already configured (`MANGOMAS_LLM__PROVIDER`).
- Same-provider only. There is no `provider_override` field; switching
  provider per agent is a separate, out-of-scope feature.
- Must remain **additive & default-OFF**: no agent sets `model_override`
  today, so `ctx.extras["agent_llm_overrides"]` is always `{}` and every
  agent resolves to `ctx.llm`, byte-identical to pre-change behaviour.
- Override clients are resource-safe: every client built for an override is
  closed when the orchestrator is closed, with the same fault isolation
  `ctx.llm`/`ctx.repo`/etc. already get.
- Agents that set the same override model share one client instance rather
  than opening duplicate connections.

## Scenarios (WHEN/THEN)

- WHEN an agent's `model_override` is unset THEN it uses `ctx.llm`
  unchanged (`resolve_llm` returns `ctx.llm`).
- WHEN `model_override` is set and differs from the shared default THEN a
  distinct client with that model is used for that agent's `complete`/
  `stream` calls, and no other agent's calls are affected.
- WHEN `model_override` is set but equal to the shared default model THEN no
  extra client is built (`build_agent_llm_overrides` returns `{}` for that
  agent) — proven by asserting the provider factory is invoked zero extra
  times.
- WHEN `model_override` is set to `""` or whitespace-only THEN it is treated
  as unset (pydantic-settings does not collapse `""` to `None` for a plain
  `str | None` field, so this needs an explicit guard, not `is not None`).
- WHEN two agents set the same override model THEN they share one client
  instance (`result["a"] is result["b"]`), and the provider factory is
  invoked exactly once for that model.
- WHEN `Orchestrator.aclose()` runs THEN every override client with
  `aclose` is closed exactly once, fault-isolated from the other hooks
  (proven both with and without overrides configured — the no-override case
  is the backward-compatibility regression guard).

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE` | _(none)_ | Already existed (spec-0014); this spec is what makes it read |

No new env var. `AgentSettings.model_override: str | None = None` is
unchanged in shape; only its consumption changes.

## Protocol / contract impact

- New/changed protocols: _none_. `LLMClient`/`AgentContext` are untouched —
  `core/agent.py` (protected) is not edited.
- New error types: _none_. An override naming an invalid/unreachable model
  fails the same way an invalid `MANGOMAS_LLM__MODEL` does today — at
  upstream call time (`LLMBadResponse`/`LLMUnavailable`), not at
  configuration time. This repo does not validate model names against a
  known list for the shared client either; the override follows the same
  convention rather than inventing a stricter one just for itself.
- Registry additions: _none_ new. `build_agent_llm_overrides` reuses the
  existing `llm_registry` (`composition/_registries.py`) verbatim — the same
  `_lmstudio_factory`/`_vertex_factory` callables that build the shared
  client, just invoked again with `model` swapped via
  `base_llm_cfg.model_copy(update={"model": override})`.
- New convention (not a protocol, see ADR-0028): `AgentContext.extras`
  carries a `dict[str, LLMClient]` at the key `"agent_llm_overrides"` —
  `extras`'s first real consumer, per `core/CLAUDE.md`'s documented use for
  "per-request data that does not deserve a field."

## Backwards-compatibility

- No agent sets `model_override` today → `build_agent_llm_overrides` returns
  `{}` → `ctx.extras["agent_llm_overrides"] == {}` → `resolve_llm` always
  returns `ctx.llm` → every existing deployment sees zero behaviour change.
  Proven directly by `test_build_orchestrator_no_overrides_extra_is_empty_dict`
  and `test_orchestrator_aclose_without_overrides_matches_prior_behavior`.
- `core/agent.py` and `core/orchestrator.py` (both protected paths) are
  **not edited**. Cleanup for override clients is added via a composition-
  layer subclass (`_AgentLLMOverrideCloseMixin` in `composition/llm.py`)
  extending `Orchestrator._close_hooks()` by ordinary method dispatch —
  the same pattern `composition/harness.py::_HarnessOrchestrator` already
  uses to extend `Orchestrator` without touching `core/orchestrator.py`.
  See ADR-0028 for the full trade-off record (this was the alternative
  design rejected: editing `_close_hooks()` directly).
- `api/health.py`'s `/readyz` probe keeps pinging only the shared `ctx.llm`
  — deliberately not fanned out to every override client, to avoid coupling
  probe latency to however many overrides are configured. Out of scope by
  design, not an oversight.
- `agents/_streaming.py::stream_with_buffered_fallback`'s third parameter
  changes from `ctx: AgentContext` to `llm: LLMClient` (its only use of
  `ctx` was `ctx.llm`, in three places) — an internal signature change to a
  non-public helper with exactly two callers (`chat.py`, `_structured.py`),
  both updated in the same change. Not a public contract.

## Test plan

- Unit (`tests/test_composition.py`): `build_agent_llm_overrides` — empty
  when no overrides; skips blank/whitespace; skips override equal to base
  model; builds a client for a differing override (via `llm_registry.scoped`,
  no real network/SDK); dedups clients shared across agents (factory call
  count proves it). Integration: `build_orchestrator` populates
  `ctx.extras["agent_llm_overrides"]` correctly (and is `{}` with no
  overrides); `Orchestrator.aclose()` and `_HarnessOrchestrator.aclose()`
  both close override clients alongside `ctx.llm`, with a backward-compat
  sibling test for the no-override case.
- Unit (`tests/agents/test_prompt.py` or wherever `resolve_sampling` lives):
  `resolve_llm` — no override → `ctx.llm`; override for this agent's name →
  that client; override exists but for a different name → still `ctx.llm`
  (proves per-agent keying).
- Unit (per agent — chat/tool_agent/summarize/planner/reviewer, `complete`
  and `stream` paths where applicable): two distinct `FakeLLM()` instances
  (one as `ctx.llm`, one keyed under the agent's name in
  `ctx.extras["agent_llm_overrides"]`) — assert the override instance's
  `.calls`/`.call_kwargs` got the request and the default instance's is
  empty, plus the inverse (no override → default instance gets the call).
- Unit (`tests/test_composition.py`): `MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE`
  env round-trip into `Settings().agents[<name>].model_override` — this env
  var shape had no test at all before this spec.
- Coverage: maintains `core`=100% (untouched), `composition`=95%,
  `agents`=95%.

## Acceptance criteria

- [x] Feature off by default → no behaviour change (test proves it:
      `test_build_orchestrator_no_overrides_extra_is_empty_dict`,
      `test_orchestrator_aclose_without_overrides_matches_prior_behavior`).
- [x] Feature on via env → documented behaviour (test proves it:
      `test_build_orchestrator_populates_agent_llm_overrides_extra` and the
      per-agent identity tests).
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; ADR-0028 added (composition-root change).
