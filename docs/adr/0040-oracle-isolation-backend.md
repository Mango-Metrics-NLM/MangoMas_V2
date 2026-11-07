# ADR-0040: Pluggable Repair-Loop Oracle Isolation Backends (`MANGOMAS_REPAIR__*`)

## Status

Accepted (2026-11-07). Implements the open isolation decision recorded in
[ADR-0039](0039-tool-effects-and-repair-sandbox-gate.md); specified by
[spec-0044](../../specs/0044-oracle-isolation-backend.md).

## Context

In [ADR-0039](0039-tool-effects-and-repair-sandbox-gate.md) we introduced
`mangomas.harness.repair_sandbox` to contain Ring 2 automated LLM coding:
loopback-only LLM URLs, canonical staging path containment, allowlisted
promotion targets, environment scrubbing, post-oracle link re-validation, and
`O_NOFOLLOW` promotion writes. However, `run_oracle_and_promote` still executed
the verification oracle subprocess directly on the host OS. When the oracle
imports or executes an LLM-generated candidate, that code runs with the
caller's filesystem and network privileges; `cwd`, environment scrubbing, and
`shell=False` bound what the subprocess *inherits*, not what it can *reach*.

During PR #119 review and our follow-up architecture spike, we evaluated how to
close this gap on Windows 11, Linux, and macOS without breaking existing
hermetic test suites or requiring Docker/WSL2 when isolation is not enabled:

1. **Production container image (`mangomas:ci`) lacks `pytest`**: `Dockerfile`
   defaults `ARG MANGOMAS_EXTRAS=cloud`, so running a pytest oracle inside
   `mangomas:ci` fails with `No module named pytest`. Building with
   `--build-arg MANGOMAS_EXTRAS=dev` (`make oracle-image`, tagged
   `mangomas:oracle`) provides a non-root container image with `pytest` and the
   package installed, without changing `Dockerfile`.
2. **Configuration boundary**: `HarnessSettings` (`MANGOMAS_HARNESS__*`) owns
   Claude Code session hooks and OTel spans. Mixing repair-sandbox runtime
   settings into `HarnessSettings` blurs that boundary; a dedicated
   `RepairSettings` (`MANGOMAS_REPAIR__*`) group keeps the domains distinct.
3. **WSL2 + `bubblewrap` viability**: On this workstation (`Ubuntu 24.04.3 LTS`
   on WSL2 kernel `6.6.114.1-microsoft-standard-WSL2`), unprivileged
   `bwrap --unshare-all --die-with-parent` runs cleanly without AppArmor
   blocks, binds `/mnt/e/...` staging directories read-only at `/work`, and
   executes `pytest` in ~0.3s.

## Decision

1. **Introduce `RepairSettings` (`MANGOMAS_REPAIR__*`) in
   `src/mangomas/config/repair.py`.**
   - `oracle_isolation: Literal["none", "required"] = "none"` (default-off for
     backwards compatibility).
   - `oracle_backend: Literal["auto", "docker", "wsl"] = "auto"`.
   - Resource and runtime parameters: `oracle_image` (`"mangomas:oracle"`),
     `oracle_timeout_seconds` (`60.0`), `oracle_memory_limit` (`"512m"`),
     `oracle_pids_limit` (`128`), `oracle_probe_timeout_seconds` (`5.0`),
     `wsl_distro` (`"Ubuntu"`), and `wsl_python` (`"/usr/bin/python3"`).
2. **Define `@runtime_checkable class OracleIsolationBackend(Protocol)` and
   `isolation_registry` in `src/mangomas/harness/isolation/`.**
   - `NoIsolationBackend` (`"none"`): runs the host subprocess as before and
     logs `event="repair_oracle_unisolated"` at `WARNING`.
   - `DockerIsolationBackend` (`"docker"`): runs `docker run --rm --name <uuid>
     --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m
     --cap-drop ALL --security-opt no-new-privileges --pids-limit <N>
     --memory <M> --mount type=bind,src=<staging>,dst=/work,readonly -w /work`
     with only the scrubbed allowlisted environment variables (`-e`), and kills
     the named container on timeout.
   - `WslBwrapIsolationBackend` (`"wsl"`): runs `wsl -d <distro> -- bwrap
     --unshare-all --die-with-parent` with read-only system binds, `--tmpfs /tmp`,
     `--ro-bind <wslpath staging> /work`, `--clearenv`, and explicit
     `--setenv` pairs for the scrubbed allowlisted environment.
   - Isolating backends rewrite a leading `{python}` or `python` token in
     `oracle_argv` to the sandbox interpreter and refuse a bare host interpreter
     path with `ToolExecutionError`.
3. **Enforce fail-closed resolution in `composition/repair.py::build_oracle_isolation`.**
   - When `oracle_isolation == "none"`, returns `NoIsolationBackend`.
   - When `oracle_isolation == "required"`, probes the requested backend (or
     `docker` then `wsl` under `"auto"`) via `is_available()` and raises
     `ConfigError` if no isolating backend is available.
4. **Mount `staging_root` read-only (`readonly` / `--ro-bind`) inside isolating
   backends.**
   - An isolated oracle cannot modify the staged candidate at all; the host-side
     SHA-256 pre/post check (`MUT-SBX-10`) remains in `run_oracle_and_promote`
     as defence in depth and covers `NoIsolationBackend`.

## Alternatives Considered

- **Default `oracle_isolation="required"` everywhere:** rejected because unit
  tests and lightweight local workflows run on hosts where Docker Desktop may be
  stopped or `mangomas:oracle` is not yet built. Keeping `"none"` as the default
  (with a `WARNING` log) preserves backwards compatibility while allowing
  production/governed repair loops to set `MANGOMAS_REPAIR__ORACLE_ISOLATION=required`.
- **Reuse `mangomas:ci` for the Docker oracle:** rejected because `mangomas:ci`
  installs only `MANGOMAS_EXTRAS=cloud` and does not contain `pytest`. Building
  `mangomas:oracle` with `--build-arg MANGOMAS_EXTRAS=dev` reuses the existing
  `Dockerfile` without adding dev dependencies to the production image.
- **Docker-only (no WSL2/`bubblewrap` backend):** rejected because WSL2 +
  `bubblewrap` avoids container startup overhead (~0.3s vs ~1.5s) and works when
  Docker Desktop is not running.

## Consequences

### Positive

- Setting `MANGOMAS_REPAIR__ORACLE_ISOLATION=required` guarantees that
  LLM-generated candidates execute with no network namespace (`--network none` /
  `--unshare-all`), a read-only root and staging mount, dropped capabilities,
  and a scrubbed environment — or fails closed with `ConfigError`.
- Existing callers of `run_oracle_and_promote` continue to work unchanged.
- All new code in `src/mangomas/harness/isolation/` and
  `src/mangomas/composition/repair.py` is unit-tested at 100% branch coverage
  with injected subprocess runners and verified against real Docker and WSL2
  sandboxes via `make oracle-isolation-docker` and `make oracle-isolation-wsl`.

### Negative / Trade-offs

- The Docker backend requires `make oracle-image` (`mangomas:oracle`) to be
  built locally before `DockerIsolationBackend.is_available()` returns `True`.
- The WSL2 backend requires `bubblewrap` (`apt install bubblewrap`) inside the
  configured WSL distribution.
