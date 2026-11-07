# Spec-0042: Origin-delta AQA for PR #111 + PR #115

- **Status:** Implemented
- **Linked ADR:** _none — no protocol, error type or registry boundary change_
  (boundary-translation bugfixes in `adapters/storage/sqlite.py` and
  `eval/sinks/sqlite_results.py` per `docs/testing/rca-20261106.md`)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Fixed — Origin-delta AQA (spec-0042)`

## Problem

`origin/feat/initial-release@6fbf689` landed PR #111 (CI hygiene, coverage
floors ratcheted to 98% / 100%, and the pragma-reason rule) and PR #115
(rejecting `sqlite:///` in `path_from_sqlite_url`, PR-only CI cancellation,
and ungrouping lock-stepped Dependabot pins). Neither PR had been validated
across the full workstation lane matrix (dual-population hermetic, dual-GPU
CUDA placement, testcontainers Postgres, live LM Studio E2E, and no-mock TCP +
CLI E2E).

In particular, PR #115 changed `path_from_sqlite_url("sqlite:///")` to raise
`ValueError("Empty sqlite URL path: 'sqlite:///'")` at the helper level, but
neither `SQLiteRepository.__init__` nor `SqliteResultsSink.__init__` caught
`ValueError` and translated it into the typed `ConfigError` (`MangomasError`)
that `mangomas.cli._runtime.build_or_exit` and `mangomas.cli.commands.eval`
expect. As a result, configuring `MANGOMAS_DB__URL=sqlite:///` or
`sqlite_results.db_path=sqlite:///` crashed every CLI command with a raw
Python traceback (exit 1) instead of `EXIT_CONFIG_ERROR` (exit 2).

## Requirements

- **FR-0042-01 (Full Lane Matrix)** — Execute hermetic P1/P2 gates, `make gpu`
  (PASS with 2 GPUs, `FAIL(GATE)` with `CUDA_VISIBLE_DEVICES=-1`),
  `make postgres`, `make lmstudio`, `make studio-probe`, and a no-mock E2E
  driver covering live `uvicorn` HTTP/SSE, CLI `chat`/`history` (SQLite +
  Postgres), `workflow validate`/`run`, `rag ingest`/`query` (multi-chunk
  document over LM Studio embeddings + Chroma), and `eval` (`json_file` +
  `sqlite_results`). Record all outcomes in `docs/testing/rca-20261106.md`.
- **FR-0042-02 (D-1 Fix)** — `SQLiteRepository.__init__` catches `ValueError`
  from `_path_from_url(url)` and raises `ConfigError("Invalid SQLite database
  URL", detail=truncated_exc_detail(exc)) from exc`.
- **FR-0042-03 (D-2 Fix)** — `SqliteResultsSink.__init__` catches `ValueError`
  from `path_from_sqlite_url(db_path)` and raises `ConfigError("sqlite_results
  sink option 'db_path' is not a valid SQLite URL or path",
  detail=truncated_exc_detail(exc)) from exc`.
- **FR-0042-04 (Red-First Guards & Mutation Proofs)** —
  `tests/regression/test_aqa_20261106_defects.py` guards both defects (16
  red on the pre-fix tree, 17 green after fix), and `MUT-AQA-1106-01..02` in
  `scripts/guard_probe_manifest.json` prove each guard fails when its fix is
  reverted.

## Scenarios (WHEN/THEN)

- WHEN `SQLiteRepository` is constructed with `sqlite:///`, `sqlite:///?mode=ro`,
  `sqlite://`, or `sqlite://[::1` THEN it raises `ConfigError` (a
  `MangomasError` subclass) with a truncated detail, and `mangomas chat` exits
  `2` (`EXIT_CONFIG_ERROR`) with `Configuration error:` and no traceback.
- WHEN `SqliteResultsSink` is constructed with an invalid SQLite URL THEN it
  raises `ConfigError`, and `mangomas eval` exits `2` (`EXIT_CONFIG_ERROR`)
  with `Eval configuration error:` and no traceback.
- WHEN either translation block is reverted THEN `make guard-probe` fails on
  `MUT-AQA-1106-01` / `MUT-AQA-1106-02` and restores the tree cleanly.

## Config / env additions

_None._

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_ (reuses `ConfigError`)
- Registry additions: _none_

## Backwards-compatibility

- Valid SQLite URLs (`:memory:`, `file::memory:...`, `sqlite:///path.db`,
  `sqlite:////abs/path.db`, bare paths) behave identically.
- Callers catching `ConfigError` / `MangomasError` at composition and CLI
  boundaries now receive the documented typed error on malformed SQLite URLs.

## Test plan

- Regression: `tests/regression/test_aqa_20261106_defects.py` (17 tests).
- Existing unit suites: `tests/test_sqlite.py`,
  `tests/test_storage_url_properties.py`,
  `tests/eval/test_sqlite_results_sink.py`.
- Mutation probe: `MUT-AQA-1106-01` and `MUT-AQA-1106-02` in
  `scripts/guard_probe_manifest.json` (total 36 entries).

## Acceptance criteria

- [x] Baseline and after-fix lane matrices recorded in `docs/testing/rca-20261106.md`.
- [x] `test_aqa_20261106_defects.py` confirmed red (16 failed) before fix and green (17 passed) after fix.
- [x] `make gate` and `make guard-probe` pass with 100.00% line+branch coverage.
