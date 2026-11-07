# Spec-0043: MangoMas Harness Tool-Execution Policy, Repair-Loop Sandbox, and Host Protection

- **Status:** Implemented
- **Linked ADR:** [ADR-0039](../docs/adr/0039-tool-effects-and-repair-sandbox-gate.md)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Harness tool-execution policy, repair-loop sandbox, and destructive-Bash gate (spec-0043 / ADR-0039)`

## Problem

When an LLM or coding agent runs tools or proposes code edits on an operator's
workstation, three gaps allowed unintended state mutation or host damage:

1. **Ring 1 (In-product `ToolAgent`)**: `ToolSpec.effects` (`UNDECLARED`,
   `READ_ONLY`, `MUTATES`) was advisory metadata only — `ToolAgent._dispatch_tool`
   never checked it and never validated `tool_call.arguments` against
   `ToolSpec.parameters_schema`. In addition, `tests/test_execution_bypass_inventory.py`
   scanned only `agents/` and `core/` and missed `asyncio` subprocesses,
   `os.startfile`, `shutil.rmtree`, `Path.unlink`, `ctypes`, and `winreg`.
2. **Ring 2 (Automated LLM coding via `mango-repair-loop`)**: Loopback URL
   restrictions, staging-directory containment, symlink/traversal refusal,
   protected-path refusal, and scrubbed oracle execution existed only as prose
   in `.claude/skills/mango-repair-loop/SKILL.md`.
3. **Ring 3 (Coding-agent & hook harness)**: On native Windows (`win32`), Claude
   Code runs `Bash` unsandboxed. `permissions.allow` had no set-equality test,
   `Bash(python scripts/guard_probe.py:*)` allowed custom `--manifest` flags,
   `BASH_CAPABLE_AGENT_SLUGS` was unpinned, and `PreToolUse` never emitted
   `permissionDecision: "deny"` on destructive `Bash` commands.

## Requirements

- **FR-0043-01 (`mangomas.harness.tool_policy`)** — `enforce_tool_invocation_policy`
  refuses any tool with `spec.effects != ToolEffects.READ_ONLY` unless its name
  is in `allowed_mutating_tools`, and `validate_tool_arguments` validates
  argument count (`<= 32`), string length (`<= 16384`), required keys,
  `additionalProperties: False`, and JSON-Schema primitive types before
  `Tool.execute` runs.
- **FR-0043-02 (` tests/test_execution_bypass_inventory.py`)** — Scan `agents`,
  `core`, `cognitive`, `rag`, `workflow`, and `eval` for all process-spawn
  functions (including `asyncio.create_subprocess_*` and `os.startfile`),
  banned imports (`subprocess`, `ctypes`, `winreg`), and file write/delete
  calls (`open` in `w`/`a`/`x`, `write_text`, `write_bytes`, `unlink`, `rmdir`,
  `remove`, `rename`, `replace`, `rmtree`, `move`, `copytree`, `touch`).
- **FR-0043-03 (`mangomas.harness.repair_sandbox`)** — Provide
  `validate_loopback_llm_url`, `stage_candidate_file`,
  `validate_promotion_target`, `build_scrubbed_oracle_env`, and
  `run_oracle_and_promote` enforcing loopback-only LLM endpoints, canonical
  relative staging paths without `..` or symlinks, allowlisted non-protected
  promotion targets, and timeout-bounded scrubbed oracle execution.
- **FR-0043-04 (`PreToolUse` destructive-`Bash` gate & settings roster)** —
  `scripts/lint_agent_frontmatter.py --hook pre-tool-use` emits
  `permissionDecision: "deny"` on `_DESTRUCTIVE_BASH_RULES` while keeping
  protected-path mentions on `"ask"`. Pin `EXPECTED_ALLOW_RULES` in
  `tests/tooling/test_claude_code_settings.py` and `BASH_CAPABLE_AGENT_SLUGS` +
  prompt-safety scan in `tests/tooling/test_corpus_contract.py`.
- **FR-0043-05 (`mango-sandbox` skill & `MUT-SBX` probes)** — Ship
  `.claude/skills/mango-sandbox/SKILL.md` and `MUT-SBX-01..05` in
  `scripts/guard_probe_manifest.json`.

## Scenarios (WHEN/THEN)

- WHEN `ToolAgent` dispatches a tool whose `ToolSpec.effects` is `UNDECLARED`
  or `MUTATES` and the tool is not in `allowed_mutating_tools` THEN
  `ToolAgent` raises `ToolExecutionError` without calling `tool.execute`.
- WHEN `ToolAgent` dispatches a tool with missing required arguments or a
  wrong JSON-Schema primitive type THEN `ToolAgent` raises `ToolExecutionError`
  without calling `tool.execute`.
- WHEN `stage_candidate_file` or `validate_promotion_target` is given `..`, an
  absolute/drive/UNC path, a symlink, or a path in `PROTECTED_PATHS` /
  `GOVERNANCE_SURFACE` / `.git/` / `.claude/` / `.env*` THEN it raises
  `ToolExecutionError`.
- WHEN `scripts/lint_agent_frontmatter.py --hook pre-tool-use` receives a
  `Bash` payload matching `_DESTRUCTIVE_BASH_RULES` THEN it prints
  `permissionDecision: "deny"` and exits `0`.

## Config / env additions

_None._

## Protocol / contract impact

- `ToolAgent.__init__` gains an optional keyword-only
  `allowed_mutating_tools: frozenset[str] = frozenset()` parameter (additive,
  default-safe).
- `mangomas.harness` exports `enforce_tool_invocation_policy`,
  `validate_tool_arguments`, `validate_loopback_llm_url`,
  `stage_candidate_file`, `validate_promotion_target`,
  `build_scrubbed_oracle_env`, `run_oracle_and_promote`, and `PromotionOutcome`.

## Backwards-compatibility

- Built-in `RetrievalTool` already declares `ToolEffects.READ_ONLY` and valid
  `parameters_schema`, so existing RAG tool calls are unchanged.
- Protected-path edits continue to receive `permissionDecision: "ask"`.

## Test plan

- Unit: `tests/harness/test_tool_policy.py`, `tests/harness/test_repair_sandbox.py`.
- Bypass inventory: `tests/test_execution_bypass_inventory.py`.
- Tooling & hooks: `tests/tooling/test_claude_code_settings.py`,
  `tests/tooling/test_corpus_contract.py`, `tests/test_lint_agent_frontmatter.py`.
- Mutation probes: `MUT-SBX-01..05` in `scripts/guard_probe_manifest.json`.

## Acceptance criteria

- [x] `ToolAgent` blocks `UNDECLARED` and `MUTATES` tools by default and validates arguments against `parameters_schema`.
- [x] `mangomas.harness.repair_sandbox` blocks non-loopback URLs, traversal, symlinks, protected paths, and failed-oracle promotions at 100% coverage.
- [x] `EXPECTED_ALLOW_RULES`, `EXPECTED_DENY_RULES`, and `BASH_CAPABLE_AGENT_SLUGS` are pinned by set equality, and `PreToolUse` emits `deny` on destructive `Bash` commands.
- [x] `mango-sandbox` skill passes `make frontmatter` and corpus contracts, and `MUT-SBX-01..05` pass `make guard-probe`.
