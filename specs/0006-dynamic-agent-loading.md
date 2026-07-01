# Spec-0006: Dynamic agent loading via entry points

- **Status:** Draft (implemented in Milestone B)
- **Linked ADR:** ADR-0008 (to be authored with the implementation)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`NEXT_STEPS.md` › "Long term" › *Agent marketplace / dynamic loading*: third
parties should be able to `pip install` a package that registers new agents into
`agent_registry` **without editing `composition.py`**. The eval harness already
solved the equivalent problem for scorers/sinks/targets/sources in
`src/mangomas/eval/discovery.py`, gated by `MANGOMAS_DISCOVERY_ENABLED`.

## Requirements

- Discover external agent factories via `importlib.metadata.entry_points`
  (proposed group: `mangomas.agents`), mirroring `eval/discovery.py`.
- **Reuse** the existing `MANGOMAS_DISCOVERY_ENABLED` flag — no new flag.
- Each discovered factory is registered into the existing `agent_registry` in
  `composition.py`; name collisions with built-ins are rejected with a clear,
  typed error (or logged + skipped — decide in ADR-0008).
- Must remain **additive & default-OFF**: discovery disabled → only built-in
  agents (`chat`, `tool`, `planner`, `reviewer`, `summarize`) are registered.

## Config / env additions

Reuses `MANGOMAS_DISCOVERY_ENABLED` (already documented). No new env var.

## Protocol / contract impact

- No change to the `Agent` protocol. New helper `discover_agents()` alongside
  the existing eval discovery, or a shared generic discovery utility if the two
  can be de-duplicated without coupling layers.
- Discovered factories must satisfy the same `Agent` factory signature used by
  built-in registrations in `composition.py`.

## Backwards-compatibility

- Default off → registry contents byte-identical to today.
- Enabling discovery with no third-party packages installed → no change.

## Test plan

- Fake entry-point group (monkeypatched `entry_points`) registers a `FakeLLM`
  backed agent; assert it becomes dispatchable.
- Disabled-by-default path asserts only built-ins are present.
- Collision test: a discovered agent reusing a built-in name is handled per the
  ADR-0008 decision (reject or skip) — asserted explicitly.

## Acceptance criteria

- [ ] With `MANGOMAS_DISCOVERY_ENABLED=true` and a fake entry point, the agent
      is dispatchable without touching `composition.py`.
- [ ] Default off → identical registry; 95% coverage maintained.
- [ ] No layering violation (discovery lives at composition level, not in `core/`).
