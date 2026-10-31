## Summary

- Compared with `origin/main` (`beb01bf`), `origin/feat/initial-release` is the integration trunk (`Makefile` `BASE_REF`) and sits hundreds of commits ahead. This branch is cut from that trunk and adds the origin-delta AQA register (spec-0039) plus the falsifiability probe (spec-0033).
- Compared with `origin/feat/initial-release`, the protected-path footprint is `pyproject.toml` only (the `gpu` pytest marker and the torch mypy override). `src/mangomas/core/**` is untouched.
- Live LM Studio no longer times out on the 30s step budget, `ToolAgent` no longer rejects a fenced plan when no tools are registered, and `make gpu` fails rather than skips when CUDA is missing. `make guard-probe` kills the ten hermetic mutations and restores the tree.

## Changes

- **AQA (spec-0039):** D-1 step budget, D-2 no-registry JSON, D-3 bytes headers, D-4 nested JSON, D-5 SSE deltas, D-6 CLI settings errors. Guards live in `tests/regression/test_aqa_20261029_defects.py`.
- **Constants:** live step-timeout resolution is `tests/constants/live.py::resolve_live_step_timeout`, shared by the LM Studio and Vertex conftests.
- **Decomposition (non-protected):** `ToolAgent.handle` helpers, `eval_cmd` → `_run_eval_pipeline`, `utils/regex_flags.py`, `vertex/_sdk.py` + `_messages.py`, `predicate/_spec.py` + `_json_field.py`. Monkeypatch seams stay on `vertex/_client.py`.
- **spec-0033:** `scripts/guard_probe.py`, `scripts/guard_probe_manifest.json`, `make guard-probe`, CI job `guard-probe` (not part of `make gate`).
- **Corpus:** skills `mango-aqa` and `mango-repair-loop` (23 skills, 27 agents).

## Test plan

- [ ] `make gate`
- [ ] `python scripts/guard_probe.py --allow-dirty` (dirty checkout) or `make guard-probe` (clean)
- [ ] `make gpu` on a CUDA population
- [ ] `make lmstudio` when LM Studio is serving the configured model
- [ ] Pong oracles: `pytest E:\PongSandbox\_harness\test_oracles.py`, `node E:\PongSandbox\_harness\pong_smoke.mjs`, `node E:\PongSandbox\test.js`

## ADR

n/a — no new boundary. Vertex and predicate splits stay behind the existing package facades (ADR-0019). spec-0033 adds a script and a Make target, not a protocol.

## CHANGELOG

`## [Unreleased]` → `### Added` (spec-0033, `regex_flags`, package splits, two skills) and `### Fixed` (spec-0039 D-1..D-6, `make gpu`).

## Sub-agent reviews

- `mango-ci-dev`: `make guard-probe` is not a `gate` dependency; CI calls `make guard-probe`.
- `mango-test-engineer`: regression guards are red-first and the manifest snippets each occur once.
- `mango-rag-dev`: `RUN_GPU=1` fails when CUDA is absent.

## Notes

The `pyproject.toml` edit (gpu marker, torch `ignore_missing_imports`) needs this commit trailer, as the first token of the path and unbackticked:

```
BREAKING-CHANGE: pyproject.toml — add gpu pytest marker and torch mypy ignore_missing_imports (spec-0039)
```
