---
name: mango-sandbox
description: >
  Runtime LLM tool-execution policy, automated LLM coding staging containment,
  and Windows/WSL2 host protection in Mango-Mas V2. Use when: registering or
  auditing a Tool for ToolAgent, running a local-LLM code repair loop through
  mangomas.harness.repair_sandbox, widening the cognitive-plane bypass scan, or
  hardening destructive-command blocks on a Windows workstation.
argument-hint: "Name the tool, repair target, or host-protection rule to audit"
---

# Mango-Mas Sandbox Skill

## When to Use

- Registering a new `Tool` on `ToolRegistry` or auditing `ToolSpec.effects` and
  `parameters_schema` enforcement in `src/mangomas/harness/tool_policy.py`
- Driving an automated LLM repair loop through
  `src/mangomas/harness/repair_sandbox.py`
- Expanding the AST execution-bypass scan in
  `tests/test_execution_bypass_inventory.py`
- Auditing destructive `Bash` command blocks or setting up WSL2 OS-level
  sandboxing on Windows

For `.claude/settings.json` hook plumbing and `BREAKING-CHANGE` commit trailers,
use `mango-harness`.

---

## Three Rings of Containment

| Ring | Surface | Enforcing mechanism | Test suite |
|---|---|---|---|
| **Ring 1 — In-product LLM tool use** | `ToolAgent` -> `enforce_tool_invocation_policy` | Refuses any tool with `ToolSpec.effects != ToolEffects.READ_ONLY` unless explicitly granted in `allowed_mutating_tools`; validates argument count, string length, required keys, and JSON-Schema primitive types before `Tool.execute` | `tests/harness/test_tool_policy.py`, `tests/test_execution_bypass_inventory.py` |
| **Ring 2 — Automated LLM coding** | `mangomas.harness.repair_sandbox` | Loopback-only LLM URL check (`localhost`, `127.0.0.1`, `::1`); canonical staging path containment (`is_relative_to`, no `..`, no drive/UNC, no symlinks, no NTFS ADS, no DOS devices in any component, no null bytes); promotion target must be allowlisted and never in `PROTECTED_PATHS` / `GOVERNANCE_SURFACE` / a `.git` / `.claude` / `.venv` component (bare `.git` pointer files included) / `.env*`; scrubbed, timeout-bounded oracle subprocess with SHA-256 staged-mutation detection; staged path and target re-validated after the oracle and written with `O_NOFOLLOW` where available. The oracle itself is **not** OS-isolated — it runs with the caller's filesystem and network privileges | `tests/harness/test_repair_sandbox.py` |
| **Ring 3 — Coding-agent & hook harness** | `.claude/settings.json` + `scripts/lint_agent_frontmatter.py` | `EXPECTED_ALLOW_RULES` and `EXPECTED_DENY_RULES` set equality; `BASH_CAPABLE_AGENT_SLUGS` set equality; `PreToolUse` emits `permissionDecision: "deny"` on destructive `Bash` commands (`rm -rf`/`-fr`, `Remove-Item -Recurse`, `del /s`, `git reset --hard`, `git clean -f`, force-push incl. `+refspec`, `git diff --output=`, `guard_probe.py --manifest`), matched by `scripts/_destructive_bash.py` on tokens — position-independent options, Git global options (`git -C repo`), wrappers (`sudo`/`env`/`xargs`), and nested `bash -c` / `pwsh -Command` / `cmd /c` | `tests/tooling/test_claude_code_settings.py`, `tests/tooling/test_corpus_contract.py`, `tests/test_lint_agent_frontmatter.py`, `tests/test_destructive_bash.py` |

---

## Ring 1 Recipe: Adding or Auditing an LLM Tool

1. Declare `effects=ToolEffects.READ_ONLY` on `ToolSpec` only when the tool
   performs zero filesystem, database, or external state mutations. Leave
   mutating tools as `ToolEffects.MUTATES`.
2. Declare a complete `parameters_schema` with `"properties"`, `"required"`, and
   `"additionalProperties": False` so `validate_tool_arguments` rejects
   unexpected or mistyped arguments before `execute()` runs.
3. Verify the cognitive/orchestration packages (`agents`, `core`, `cognitive`,
   `rag`, `workflow`, `eval`) introduce no unallowlisted process spawns or file
   writes:

```powershell
python -m pytest tests/harness/test_tool_policy.py tests/test_execution_bypass_inventory.py -q
```

---

## Ring 2 Recipe: Safe Automated Coding with a Local LLM

Never give a local LLM a direct file-write tool aimed at the repository tree.
Route every candidate edit through `mangomas.harness.repair_sandbox`:

1. Call `validate_loopback_llm_url(base_url)` before dispatching prompts so code
   never leaves the workstation.
2. Write the model's candidate to a throwaway staging directory via
   `stage_candidate_file(staging_root, relative_path, content)`.
3. Run the deterministic verification oracle and promote only on exit 0 via
   `run_oracle_and_promote(...)`, passing an explicit per-run `allowed_paths`
   set and `oracle_argv` sequence (`shell=False`).

```powershell
python -m pytest tests/harness/test_repair_sandbox.py -q
```

---

## Ring 3 Recipe: Host Protection on Windows vs WSL2

Claude Code's OS-level `bubblewrap` / Seatbelt sandbox runs on macOS, Linux, and
WSL2; on native Windows (`win32`), `Bash` commands run unsandboxed.

- **On native Windows**: rely on `EXPECTED_ALLOW_RULES`, `EXPECTED_DENY_RULES`,
  and the `PreToolUse` destructive-`Bash` `"deny"` detector in
  `scripts/lint_agent_frontmatter.py`.
- **For OS-enforced isolation on Windows**: run sessions inside WSL2 with
  `bubblewrap`, `socat`, and `@anthropic-ai/sandbox-runtime` installed, and set
  `sandbox.enabled=true` and `sandbox.allowUnsandboxedCommands=false` in user
  settings (`~/.claude/settings.json`).

```powershell
python -m pytest tests/tooling/test_claude_code_settings.py tests/tooling/test_corpus_contract.py tests/test_lint_agent_frontmatter.py -q
make guard-probe
```

---

## Constraints

- DO NOT register a tool with `ToolEffects.READ_ONLY` if it writes files,
  spawns processes, or mutates external state.
- DO NOT promote a staged repair file without a zero-exit oracle run through
  `run_oracle_and_promote`.
- DO NOT add `Bash` to a router or read-only auditor agent in `.claude/agents/`.
- DO NOT widen `permissions.allow` in `.claude/settings.json` without updating
  `EXPECTED_ALLOW_RULES` in `tests/constants/corpus.py` and running
  `make validate-config`.
