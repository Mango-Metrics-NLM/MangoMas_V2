# ADR-0039: Fail-Closed Tool-Effect Policy, Repair-Loop Staging Containment, and Destructive-Bash Hook Gate

## Status

Accepted (2026-11-06). Implemented by spec-0043.

## Context

Mango-Mas V2 operates across three distinct execution rings when an LLM or a
coding agent invokes tools or generates code on an operator workstation:

1. **Ring 1 — In-product LLM tool execution (`ToolAgent`)**: ADR-0033 introduced
   `ToolEffects` (`UNDECLARED`, `READ_ONLY`, `MUTATES`) on `ToolSpec` and
   documented that a consumer should treat `UNDECLARED` as `MUTATES` and fail
   closed. However, `ToolAgent._dispatch_tool` called `await tool.execute(...)`
   unconditionally without checking `tool.spec.effects` or validating
   `tool_call.arguments` against `tool.spec.parameters_schema`. Meanwhile,
   `tests/test_execution_bypass_inventory.py` scanned only `agents/` and
   `core/` for `subprocess` and `open(..., "w")`, missing `cognitive/`, `rag/`,
   `workflow/`, `eval/`, `asyncio.create_subprocess_*`, `os.startfile`,
   `shutil.rmtree`, `Path.unlink`, `ctypes`, and `winreg`.
2. **Ring 2 — Automated LLM coding (`mango-repair-loop`)**: The
   `mango-repair-loop` skill documented a staging directory, a loopback-only
   LLM endpoint check, a per-run path allowlist, and a subprocess oracle gate,
   but no code in `src/mangomas/harness/` enforced those rules or tested path
   traversal (`..`), symlink escapes, drive/UNC switches, or protected-path
   overwrites.
3. **Ring 3 — Coding-agent, subagent, skill, and hook harness (`.claude/`)**:
   Claude Code's OS-level `bubblewrap` / Seatbelt sandbox runs on macOS, Linux,
   and WSL2, but **on native Windows (`win32`) Claude Code runs `Bash` commands
   unsandboxed**. Furthermore, `.claude/settings.json` pinned
   `permissions.deny` by set equality while `permissions.allow` had no equality
   test, `Bash(python scripts/guard_probe.py:*)` allowed overriding
   `--manifest`, `BASH_CAPABLE_AGENT_SLUGS` was unpinned, and the `PreToolUse`
   `Bash` hook emitted only `ask` on protected-path mentions and never emitted
   `deny` on host-destructive commands (`rm -rf`/`-fr`, `Remove-Item -Recurse`,
   `del /s`, `git reset --hard`, `git clean -f`, `git diff --output=`).

## Decision

1. **Enforce `ToolEffects` fail-closed and argument schema bounds before
   `Tool.execute` (`mangomas.harness.tool_policy`).**
   `ToolAgent._dispatch_tool` calls `enforce_tool_invocation_policy`, which
   refuses any tool whose `spec.effects` is not `ToolEffects.READ_ONLY` unless
   its name appears in `ToolAgent`'s explicit `allowed_mutating_tools` grant.
   Before execution, `validate_tool_arguments` enforces maximum argument count,
   maximum string length, required keys, `additionalProperties: False`, and
   JSON-Schema primitive types (rejecting `bool` where `integer` or `number` is
   required). Violations raise `ToolExecutionError`.
2. **Provide a deterministic repair-loop staging and promotion sandbox
   (`mangomas.harness.repair_sandbox`).**
   Automated LLM coding workflows must validate the LLM URL as loopback-only
   (`localhost`, `127.0.0.1`, `::1`), stage drafts inside a non-symlink
   `staging_root` (`is_relative_to`, no `..`, no drive/UNC paths, no symlinks
   in the ancestor chain), run the verification oracle with `shell=False`, a
   positive timeout, and a scrubbed environment (`RUN_*` and credentials
   stripped, `PYTEST_ADDOPTS="-p no:cacheprovider -o addopts="`), and promote
   only when the oracle exits `0` and the target is allowlisted and outside
   `PROTECTED_PATHS`, `GOVERNANCE_SURFACE`, `.git/`, `.claude/`, and `.env*`.
3. **Split `PreToolUse` between protected-path advisory (`ask`) and
   destructive-`Bash` hard block (`deny`).**
   ADR-0021 remains in force for protected-path edits (`permissionDecision:
   "ask"`, because `scripts/check_protected_paths.py` in CI is the authoritative
   gate over committed git history). For `Bash` commands matching
   `_DESTRUCTIVE_BASH_RULES` in `scripts/lint_agent_frontmatter.py`, the hook
   emits `permissionDecision: "deny"`, blocking recursive deletion, destructive
   git resets/cleans/force-pushes, `git diff/log/show --output=` file
   overwrites, and `guard_probe.py --manifest` overrides before the shell runs.
4. **Pin `EXPECTED_ALLOW_RULES` and `BASH_CAPABLE_AGENT_SLUGS` by set equality.**
   `tests/tooling/test_claude_code_settings.py` pins `permissions.allow` by set
   equality alongside `permissions.deny`, and
   `tests/tooling/test_corpus_contract.py` pins `BASH_CAPABLE_AGENT_SLUGS` and
   scans all agent/skill markdown for rogue sandbox-bypass instructions.

## Consequences

### Positive

- A newly registered `Tool` that omits `effects` (`ToolEffects.UNDECLARED`) or
  declares `ToolEffects.MUTATES` cannot be executed by `ToolAgent` unless
  explicitly granted, turning ADR-0033's vocabulary into a live runtime gate.
- `tests/test_execution_bypass_inventory.py` covers all six cognitive and
  orchestration packages (`agents`, `core`, `cognitive`, `rag`, `workflow`,
  `eval`) and detects `asyncio` subprocesses, `os.startfile`, `shutil.rmtree`,
  `Path.unlink`, `ctypes`, and `winreg`.
- Host-destructive `Bash` commands are blocked in-session on native Windows
  even without an OS-level `bubblewrap` sandbox, and five `MUT-SBX-*` entries
  in `scripts/guard_probe_manifest.json` prove every new guard is falsifiable.

### Negative / Trade-offs

- Test doubles (`FakeTool`) must declare `effects: ToolEffects =
  ToolEffects.READ_ONLY` by default so unit tests exercising normal read-only
  tool dispatch pass; tests for mutating/undeclared tools pass `effects`
  explicitly.
- Full OS-level filesystem and network sandboxing of arbitrary subprocesses on
  Windows still requires running Claude Code inside WSL2 with `bubblewrap`,
  `socat`, and `@anthropic-ai/sandbox-runtime`, as documented in the
  `mango-sandbox` skill and `docs/analysis/20261107-sandbox-hardening.md`.
- The repair-loop oracle executes the LLM-generated candidate with the
  caller's filesystem and network privileges when `MANGOMAS_REPAIR__ORACLE_ISOLATION=none`
  (the backwards-compatible default): `cwd`, environment scrubbing and
  `shell=False` bound what it *inherits*, not what it can *reach*. Post-oracle
  re-validation and `O_NOFOLLOW` stop a link-swap from redirecting promotion,
  and [ADR-0040](0040-oracle-isolation-backend.md) (spec-0044) adds pluggable
  OS-enforced isolation backends (`DockerIsolationBackend`,
  `WslBwrapIsolationBackend`) that fail closed when
  `MANGOMAS_REPAIR__ORACLE_ISOLATION=required`.
