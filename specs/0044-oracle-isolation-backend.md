# Spec-0044: Repair-Loop Oracle Isolation Backend (`MANGOMAS_REPAIR__*`)

- **Status:** Implemented (Phase A1: Docker backend & protocol; Phase A2: WSL2/bubblewrap backend)
- **Linked ADR:** [ADR-0040](../docs/adr/0040-oracle-isolation-backend.md)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Pluggable repair-loop oracle isolation backends and MANGOMAS_REPAIR__* settings (spec-0044 / ADR-0040)`

## Problem

[ADR-0039](../docs/adr/0039-tool-effects-and-repair-sandbox-gate.md) (spec-0043)
hardened the `mango-repair-loop` staging and promotion boundary
(`mangomas.harness.repair_sandbox`), but recorded an explicit open trade-off:
the verification oracle (`run_oracle_and_promote`) executed the LLM-generated
candidate with the caller's filesystem and network privileges. While `cwd`,
environment scrubbing, `shell=False`, post-oracle link re-validation, and
`O_NOFOLLOW` prevented environment inheritance and promotion-target link swaps,
an imported candidate could still read on-disk credentials or mutate live files
outside the staging directory during the oracle run.

Furthermore, a peer review against this workstation identified six constraints
that any isolation design must satisfy:

1. **Production image lacks `pytest`:** `mangomas:ci` is built with
   `MANGOMAS_EXTRAS=cloud` (`Dockerfile`), so `python -m pytest` fails inside
   it. A pytest oracle requires an image built with `MANGOMAS_EXTRAS=dev`
   (`mangomas:oracle`).
2. **Domain separation from `HarnessSettings`:** `MANGOMAS_HARNESS__*` configures
   Claude Code session governance and spans (`.env.example` line 228). Repair
   sandbox tunables belong in a dedicated `RepairSettings` group
   (`MANGOMAS_REPAIR__*`).
3. **Composition-root enforcement:** `run_oracle_and_promote(isolation=None)`
   cannot read `Settings` directly without introducing a service locator.
   Fail-closed resolution belongs in `composition/repair.py::build_oracle_isolation`.
4. **Cross-OS interpreter argv:** callers passing host `sys.executable` cannot
   run inside a Linux container or WSL2 namespace. Isolating backends must
   substitute `{python}` / `python` (matching `scripts/guard_probe.py`) and
   refuse a bare host path with a clear `ToolExecutionError`.
5. **Read-only staging mount:** mounting `staging_root` read-only (`:ro`) with a
   `tmpfs /tmp` and `PYTHONDONTWRITEBYTECODE=1` prevents the oracle from mutating
   the candidate in place while keeping the host-side SHA-256 verification
   (`MUT-SBX-10`) as defence in depth.
6. **WSL2 / `bubblewrap` spike (Phase A0 verdict — GO):** verified on this
   workstation (`Ubuntu 24.04.3 LTS`, kernel
   `6.6.114.1-microsoft-standard-WSL2`): `bubblewrap 0.9.0` executes
   unprivileged `bwrap --unshare-all --die-with-parent` without AppArmor blocks,
   binds `/mnt/e/...` read-only at `/work`, and runs `pytest 9.2.0` under
   `/usr/bin/python3` and `/opt/mangomas-oracle/bin/python`.

## Requirements

- **FR-0044-01 (`RepairSettings` / `MANGOMAS_REPAIR__*`)** — Add
  `src/mangomas/config/repair.py` with `RepairSettings` attached as
  `Settings.repair`:
  - `oracle_isolation: Literal["none", "required"] = "none"`
  - `oracle_backend: Literal["auto", "docker", "wsl"] = "auto"`
  - `oracle_image: str = "mangomas:oracle"`
  - `oracle_timeout_seconds: float = 60.0` (positive, finite)
  - `oracle_memory_limit: str = "512m"`
  - `oracle_pids_limit: int = 128` (`>= 16`)
  - `oracle_probe_timeout_seconds: float = 5.0` (positive, finite)
  - `wsl_distro: str = "Ubuntu"`
  - `wsl_python: str = "/usr/bin/python3"`
- **FR-0044-02 (`OracleIsolationBackend` protocol & `isolation_registry`)** —
  Define `@runtime_checkable class OracleIsolationBackend(Protocol)` in
  `src/mangomas/harness/isolation/base.py` with `name: str`,
  `is_available() -> bool`, and
  `run(argv, *, cwd, env, timeout_seconds) -> IsolationResult`. Register
  backends in `isolation_registry = Registry("oracle_isolation")` without
  modifying `src/mangomas/registry.py`.
- **FR-0044-03 (`NoIsolationBackend` & warning)** — When isolation is `"none"`
  (or `isolation=None` is passed directly to `run_oracle_and_promote`), execute
  the subprocess on the host as before and emit a structured `WARNING` log
  (`event="repair_oracle_unisolated"`).
- **FR-0044-04 (`DockerIsolationBackend` — Phase A1)** — Run the oracle via
  `docker run --rm --name <uuid> --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges --pids-limit <N> --memory <M> --mount type=bind,src=<staging>,dst=/work,readonly -w /work`
  passing only the scrubbed allowlisted environment variables (`PYTEST_ADDOPTS`,
  `PYTHONIOENCODING`, `PYTHONDONTWRITEBYTECODE=1`, and allowlisted
  `MANGOMAS_*`). On `TimeoutExpired`, run `docker kill <name>` before raising
  `ToolExecutionError`.
- **FR-0044-05 (`WslBwrapIsolationBackend` — Phase A2)** — Run the oracle via
  `wsl -d <distro> -- bwrap --unshare-all --die-with-parent --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind-try /lib64 /lib64 --ro-bind /bin /bin --ro-bind /etc /etc --ro-bind-try /opt /opt --proc /proc --dev /dev --tmpfs /tmp --ro-bind <wsl_staging> /work --chdir /work --clearenv`
  with explicit `--setenv` pairs for the scrubbed allowlisted environment.
- **FR-0044-06 (`composition.repair.build_oracle_isolation`)** — Resolve the
  configured backend from `RepairSettings`. When `oracle_isolation="required"`
  and the requested backend (or all candidates under `"auto"`: `docker`, then
  `wsl`) is unavailable, raise `ConfigError` naming every probed backend.

## Scenarios (WHEN/THEN)

- WHEN `run_oracle_and_promote` is called with `isolation=None` THEN it defaults
  to `NoIsolationBackend`, emits `event="repair_oracle_unisolated"` at
  `WARNING`, and preserves existing promotion semantics.
- WHEN `build_oracle_isolation` is called with `oracle_isolation="required"` and
  no isolating backend is available THEN it raises `ConfigError` (fail closed).
- WHEN an isolating backend (`docker` or `wsl`) receives an `oracle_argv` whose
  first token is a host-specific path (e.g. `C:\...\python.exe`) rather than
  `{python}` or `python` THEN it raises `ToolExecutionError` before spawning.
- WHEN `DockerIsolationBackend.run` or `WslBwrapIsolationBackend.run` executes
  an oracle candidate that attempts to read a host file outside `staging_root`,
  write to `/work`, or open an outbound network socket THEN all three attempts
  fail inside the sandbox while a benign candidate exits `0` and promotes.

## Config / env additions

| Variable | Default | Purpose |
|---|---|---|
| `MANGOMAS_REPAIR__ORACLE_ISOLATION` | `none` | Oracle isolation policy (`none` \| `required`; `required` fails closed when no backend is available) |
| `MANGOMAS_REPAIR__ORACLE_BACKEND` | `auto` | Isolation backend (`auto` \| `docker` \| `wsl`; `auto` tries `docker` then `wsl`) |
| `MANGOMAS_REPAIR__ORACLE_IMAGE` | `mangomas:oracle` | Docker image for the isolated oracle (`make oracle-image`, built with `MANGOMAS_EXTRAS=dev`) |
| `MANGOMAS_REPAIR__ORACLE_TIMEOUT_SECONDS` | `60.0` | Per-run timeout for the repair-loop oracle subprocess |
| `MANGOMAS_REPAIR__ORACLE_MEMORY_LIMIT` | `512m` | Memory ceiling passed to `docker run --memory` |
| `MANGOMAS_REPAIR__ORACLE_PIDS_LIMIT` | `128` | Process ceiling passed to `docker run --pids-limit` |
| `MANGOMAS_REPAIR__ORACLE_PROBE_TIMEOUT_SECONDS` | `5.0` | Timeout budget for `is_available()` backend probes |
| `MANGOMAS_REPAIR__WSL_DISTRO` | `Ubuntu` | WSL2 distribution name for the `wsl` `bubblewrap` backend |
| `MANGOMAS_REPAIR__WSL_PYTHON` | `/usr/bin/python3` | Python interpreter inside the WSL2 distribution |

## Protocol / contract impact

- Additive `Settings.repair: RepairSettings` group on `Settings`.
- Additive `OracleIsolationBackend`, `IsolationResult`, `NoIsolationBackend`,
  `DockerIsolationBackend`, `WslBwrapIsolationBackend`, and `isolation_registry`
  in `mangomas.harness.isolation` (re-exported on `mangomas.harness`).
- Additive keyword-only `isolation: OracleIsolationBackend | None = None` on
  `run_oracle_and_promote`.
- Additive `build_oracle_isolation` in `mangomas.composition.repair`
  (re-exported on `mangomas.composition`).

## Backwards-compatibility

- Default `MANGOMAS_REPAIR__ORACLE_ISOLATION=none` preserves existing host
  subprocess execution for all callers, adding only a structured warning log.
- Protected core files (`core/`, `errors.py`, `registry.py`, governance surface)
  are untouched.

## Test plan

- Unit: `tests/harness/test_isolation.py`, `tests/composition/test_repair.py`,
  `tests/test_config.py`, `tests/deploy/test_oracle_image_contract.py`.
- Live gated lanes: `RUN_DOCKER_ISOLATION=1` (`make oracle-isolation-docker`)
  and `RUN_WSL_ISOLATION=1` (`make oracle-isolation-wsl`).
- Mutation probes: `MUT-ISO-01..05` in `scripts/guard_probe_manifest.json`.

## Acceptance criteria

- [x] `RepairSettings` (`MANGOMAS_REPAIR__*`) is documented in `AGENTS.md` and `.env.example`, re-exported in `tests.constants`, and passes `test_env_example_contract.py`.
- [x] `mangomas.harness.isolation` and `composition.repair.build_oracle_isolation` satisfy 100% branch coverage.
- [x] `MUT-ISO-01..04` (Phase A1) and `MUT-ISO-05` (Phase A2) pass `make guard-probe`.
- [x] Live Docker (`make oracle-isolation-docker`) and WSL2 (`make oracle-isolation-wsl`) lanes prove filesystem read/write containment and network denial on this workstation.
