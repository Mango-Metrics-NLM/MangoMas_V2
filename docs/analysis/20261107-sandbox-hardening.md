# MangoMas Harness & Sandbox Hardening Analysis (2026-11-06)

Related records: [Spec-0043](../specs/0043-harness-tool-and-coding-sandbox.md),
[ADR-0039](../adr/0039-tool-effects-and-repair-sandbox-gate.md),
`mango-sandbox` skill (`.claude/skills/mango-sandbox/SKILL.md`).

## 1. Threat Model & Three Rings of Execution

Mango-Mas V2 faces three distinct untrusted or semi-trusted principals when
tools or automated coding workflows run on an operator's PC:

| Ring | Principal | Assets at Risk | Prior Control | Gap Found in Audit | Enforced Control (Spec-0043 / ADR-0039) |
|---|---|---|---|---|---|
| **Ring 1: In-product LLM tool use** | Local or cloud LLM behind `ToolAgent` | Host filesystem, subprocess execution, vector/DB stores | `ToolEffects` enum (`UNDECLARED`, `READ_ONLY`, `MUTATES`); `test_execution_bypass_inventory.py` on `agents/` + `core/` | `ToolAgent._dispatch_tool` never checked `tool.spec.effects` or `parameters_schema`; bypass scan missed `cognitive/`, `rag/`, `workflow/`, `eval/`, `asyncio` subprocesses, `os.startfile`, `shutil.rmtree`, `Path.unlink`, `ctypes`, `winreg` | `mangomas.harness.tool_policy.enforce_tool_invocation_policy` (fail-closed `READ_ONLY` gate + schema validation) wired into `ToolAgent._dispatch_tool`; widened AST bypass inventory across all 6 packages |
| **Ring 2: Automated LLM coding** | Local LLM drafting code in `mango-repair-loop` | Live repository tree, `PROTECTED_PATHS`, `GOVERNANCE_SURFACE`, `.claude/`, `.git/`, `.env`, ambient secrets | Prose instructions in `.claude/skills/mango-repair-loop/SKILL.md` | Zero runtime code or tests enforced loopback-only URLs, staging path containment, symlink refusal, or oracle-gated promotion | `mangomas.harness.repair_sandbox` (`validate_loopback_llm_url`, `stage_candidate_file`, `validate_promotion_target`, `build_scrubbed_oracle_env`, `run_oracle_and_promote`) at 100% branch coverage |
| **Ring 3: Coding-agent, subagent, skill & hook harness** | Coding agent / subagent / skill / `.mcp.json` server | Host OS (`win32`), `.claude/settings*.json`, `sitecustomize.py`, git history | `EXPECTED_DENY_RULES` equality; `WRITE_CAPABLE_AGENT_SLUGS`; advisory `PreToolUse` hook | Native Windows runs `Bash` unsandboxed; `permissions.allow` had no equality test; `guard_probe.py:*` allowed `--manifest` override; `BASH_CAPABLE_AGENT_SLUGS` unpinned; `PreToolUse` never emitted `deny` on destructive `Bash` | `EXPECTED_ALLOW_RULES` and `BASH_CAPABLE_AGENT_SLUGS` set equality; `guard_probe.py` narrowed; `Edit(/sitecustomize.py)` and `Edit(/.mcp.json)` denied; `PreToolUse` emits `permissionDecision: "deny"` on `_DESTRUCTIVE_BASH_RULES`; corpus prompt-safety scan |

## 2. Online & Vendor Documentation Findings

1. **Claude Code OS Sandboxing (`docs.anthropic.com/en/docs/claude-code/sandboxing`)**:
   - The OS-enforced `Bash` sandbox uses Seatbelt on macOS and `bubblewrap` +
     `socat` (+ optional `@anthropic-ai/sandbox-runtime` seccomp filter) on
     Linux and WSL2.
   - **On native Windows (`win32`), Claude Code runs commands unsandboxed.**
     To gain OS-level filesystem and network isolation on Windows 11, sessions
     must run inside WSL2 with `sandbox.enabled: true`,
     `sandbox.allowUnsandboxedCommands: false`, and `sandbox.failIfUnavailable: true`
     in user/managed settings (`~/.claude/settings.json`), and with the seccomp
     filter installed so WSL2 Unix sockets cannot launch `/mnt/c/*.exe` host
     binaries.
   - Repository-scoped `.claude/settings.json` cannot disable filesystem
     isolation (`sandbox.filesystem.disabled`), set `strictAllowlist`, or
     configure credential masking (`sandbox.credentials`).
2. **Claude Code `PreToolUse` Decision Control (`docs.anthropic.com/en/docs/claude-code/hooks`)**:
   - `PreToolUse` hooks return JSON on exit code `0` with
     `hookSpecificOutput.permissionDecision` set to `"allow"`, `"ask"`,
     `"deny"`, or `"defer"`.
   - Returning `"deny"` prevents the tool call from executing and feeds
     `permissionDecisionReason` back to the model.
   - Protected-path edits stay on `"ask"` (ADR-0021) because legitimate changes
     land with a `BREAKING-CHANGE` commit trailer verified by
     `scripts/check_protected_paths.py` in CI, whereas host-destructive `Bash`
     commands (`rm -rf`/`-fr`, `Remove-Item -Recurse`, `del /s`,
     `git reset --hard`, `git clean -f`, `git push --force`,
     `git diff/log/show --output=`, `guard_probe.py --manifest=`) have no
     legitimate in-session use and are hard-blocked with `"deny"`.

## 3. Gap List & Verification Matrix

| ID | Invariant | Enforcing File / Test | Mutation Proof |
|---|---|---|---|
| `SBX-01` | `permissions.allow` in `.claude/settings.json` matches `EXPECTED_ALLOW_RULES` exactly | `tests/tooling/test_claude_code_settings.py::test_allow_rules_match_the_expected_set` | `MUT-SBX-01` |
| `SBX-02` | `ToolAgent` blocks any tool whose `ToolSpec.effects` is not `READ_ONLY` unless explicitly granted | `src/mangomas/harness/tool_policy.py`, `tests/harness/test_tool_policy.py` | `MUT-SBX-02` |
| `SBX-03` | `PreToolUse` emits `permissionDecision: "deny"` on destructive `Bash` commands | `scripts/lint_agent_frontmatter.py`, `tests/test_lint_agent_frontmatter.py` | `MUT-SBX-03` |
| `SBX-04` | `repair_sandbox` rejects `..` traversal, symlinks, non-loopback URLs, and protected promotion targets | `src/mangomas/harness/repair_sandbox.py`, `tests/harness/test_repair_sandbox.py` | `MUT-SBX-04` |
| `SBX-05` | Only reviewed agents in `BASH_CAPABLE_AGENT_SLUGS` carry the `Bash` tool | `tests/tooling/test_corpus_contract.py::test_bash_capable_agents_match_the_reviewed_set` | `MUT-SBX-05` |
