# Spec-NNNN: <Title>

- **Status:** Draft | In progress | Implemented | Superseded by Spec-NNNN
- **Linked ADR:** ADR-NNNN (or _none — no boundary change_)
- **Linked CHANGELOG entry:** `[Unreleased]` › `<Added|Changed|…>`

## Problem

<2–4 sentences: the need this addresses, what prompted it, the intended
outcome. Reference the roadmap item in `NEXT_STEPS.md` if applicable.>

## Requirements

- <Functional requirement 1>
- <Functional requirement 2>
- Must remain **additive & default-OFF** (state the default that preserves
  current behaviour).

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_...` | `...` | ... |

All tunables are `DEFAULT_*` module constants surfaced through a `Settings`
group — **no hard-coded values**.

## Protocol / contract impact

- New/changed protocols: <name + `adapters/*/base.py` path, or _none_>
- New error types: <name in `errors.py` + `_ERROR_STATUS` mapping, or _none_>
- Registry additions: <registry + provider key, or _none_>

## Backwards-compatibility

- <Explicit statement of what stays byte-identical when the feature is off.>
- <Any deprecations, with the migration path.>

## Test plan

- Unit: <files under `tests/…`, fakes reused/added in `tests/fakes.py`>
- Gated (if external SDK): `RUN_<X>=1`, mirroring the `RUN_VERTEX` pattern.
- Coverage: maintain the 95% global gate and any per-package floor.

## Acceptance criteria

- [ ] Feature off by default → no behaviour change (test proves it).
- [ ] Feature on via env → documented behaviour (test proves it).
- [ ] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [ ] CHANGELOG updated; ADR added if a boundary changed.
