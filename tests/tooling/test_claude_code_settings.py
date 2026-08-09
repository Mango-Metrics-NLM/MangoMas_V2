"""Contract tests for the Claude Code ecosystem-tooling config (spec 0016).

``.claude/settings.json`` and ``.mcp.json`` are shared, checked-in
configuration — every contributor's hooks and MCP servers come from these two
files. Both are parsed at runtime rather than duplicated here, so a future
edit that silently drops, reorders, or unscopes an existing entry fails loudly
instead of shipping unnoticed. See docs/adr/0020-claude-code-ecosystem-tooling.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = _REPO_ROOT / ".claude" / "settings.json"
_MCP_CONFIG = _REPO_ROOT / ".mcp.json"


def _settings() -> dict[str, Any]:
    return dict(json.loads(_SETTINGS.read_text(encoding="utf-8")))


def _mcp_config() -> dict[str, Any]:
    return dict(json.loads(_MCP_CONFIG.read_text(encoding="utf-8")))


def _hook_commands(settings: dict[str, Any], event: str, matcher: str) -> list[str]:
    """Return every hook command registered for *event*/*matcher*."""
    commands: list[str] = []
    for entry in settings["hooks"].get(event, []):
        if entry["matcher"] != matcher:
            continue
        commands.extend(hook["command"] for hook in entry["hooks"])
    return commands


def test_settings_and_mcp_config_are_valid_json() -> None:
    """Guard the parsers themselves — a malformed file would otherwise reach
    every test below as a confusing ``KeyError`` instead of failing here first."""
    assert _settings()
    assert _mcp_config()


# ── pre-existing hooks (predate this integration) — must survive verbatim ────


def test_original_session_start_hook_survives() -> None:
    assert _hook_commands(_settings(), "SessionStart", "*") == [
        "python scripts/harness_session_start.py"
    ]


def test_original_frontmatter_lint_hook_survives() -> None:
    assert _hook_commands(_settings(), "PreToolUse", "Edit|Write") == [
        'python scripts/lint_agent_frontmatter.py --check-protected-paths "$CLAUDE_TOOL_INPUT_path"'
    ]


def test_original_ruff_autofix_hook_survives() -> None:
    assert _hook_commands(_settings(), "PostToolUse", "Edit|Write") == [
        'python -m ruff check --fix "$CLAUDE_TOOL_INPUT_path" 2>/dev/null || true'
    ]


def test_original_stop_hook_survives() -> None:
    assert _hook_commands(_settings(), "Stop", "*") == ["python -m pytest -q --no-cov || true"]


# ── rtk-ai/rtk (spec 0016 / ADR-0020) ─────────────────────────────────────────


def test_rtk_hook_is_registered_and_opt_out_gated() -> None:
    commands = _hook_commands(_settings(), "PreToolUse", "Bash")
    assert len(commands) == 1
    command = commands[0]
    assert "rtk hook claude" in command
    # Claude Code has no per-hook disable (disableAllHooks kills every hook,
    # including the three above) — this env-var gate is the only supported
    # way a contributor can opt out individually. See docs/tooling/
    # claude-code-ecosystem.md.
    assert "MANGOMAS_DISABLE_RTK_HOOK" in command


def test_rtk_telemetry_disabled_by_default() -> None:
    assert _settings()["env"]["RTK_TELEMETRY_DISABLED"] == "1"


def test_rtk_opt_out_var_has_a_discoverable_default() -> None:
    """The opt-out var must appear in the shared env block with a default,
    not exist only implicitly inside the hook command string."""
    assert "MANGOMAS_DISABLE_RTK_HOOK" in _settings()["env"]


# ── .mcp.json (spec 0016 / ADR-0020) ──────────────────────────────────────────


def test_mcp_servers_match_the_adopted_set() -> None:
    """Exactly the 5 servers this integration adopted — not the full upstream
    modelcontextprotocol/servers set (``memory``/``everything``/``time`` were
    deliberately excluded, see ADR-0020) and nothing left over from local
    experimentation."""
    assert set(_mcp_config()["mcpServers"]) == {
        "filesystem",
        "git",
        "fetch",
        "sequential-thinking",
        "repomix",
    }


def test_filesystem_and_git_servers_are_scoped_to_the_project_directory() -> None:
    """Unscoped filesystem/git MCP access would reach beyond this repo — both
    must pin access to ``${CLAUDE_PROJECT_DIR:-.}``, never a bare path."""
    servers = _mcp_config()["mcpServers"]
    for name in ("filesystem", "git"):
        assert "${CLAUDE_PROJECT_DIR:-.}" in servers[name]["args"]


def test_no_mcp_server_declares_an_api_key() -> None:
    """None of the 5 adopted servers should need a secret — an ``env`` key
    appearing here would mean either an unreviewed scope change or a literal
    key pasted into checked-in config."""
    for server in _mcp_config()["mcpServers"].values():
        assert "env" not in server
