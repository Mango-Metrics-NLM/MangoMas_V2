"""Contract tests for the Claude Code ecosystem-tooling config (spec 0016).

``.claude/settings.json`` and ``.mcp.json`` are shared, checked-in
configuration — every contributor's hooks and MCP servers come from these two
files, and in cloud/Agent-SDK sessions the MCP servers load with no approval
prompt at all. Both files are parsed at runtime rather than duplicated here,
and the expected contract lives in ``tests/constants.py``, so a future edit
that silently drops, reorders, or unscopes an entry fails loudly.

See docs/adr/0020-claude-code-ecosystem-tooling.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.constants import (
    ADOPTED_MCP_SERVERS,
    CLAUDE_SETTINGS_LOCAL_EXAMPLE_RELPATH,
    CLAUDE_SETTINGS_RELPATH,
    ENV_FLAG_ON,
    MCP_CONFIG_RELPATH,
    MCP_PROJECT_DIR_SCOPE,
    PATH_SCOPED_MCP_SERVERS,
    PREEXISTING_HOOKS,
    RTK_BINARY_GUARD_FRAGMENT,
    RTK_DISABLE_ENV,
    RTK_HOOK_COMMAND_FRAGMENT,
    RTK_HOOK_EVENT,
    RTK_HOOK_MATCHER,
    RTK_TELEMETRY_DISABLED_ENV,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(relpath: str) -> dict[str, Any]:
    return dict(json.loads((_REPO_ROOT / relpath).read_text(encoding="utf-8")))


def _settings() -> dict[str, Any]:
    return _load_json(CLAUDE_SETTINGS_RELPATH)


def _mcp_servers() -> dict[str, Any]:
    servers = _load_json(MCP_CONFIG_RELPATH)["mcpServers"]
    return dict(servers)


def _hook_commands(event: str, matcher: str) -> list[str]:
    """Return every hook command registered for *event*/*matcher*.

    Claude Code stores hooks as ``{event: [{matcher, hooks: [{command}]}]}``,
    so a single event can carry several matcher groups (``PreToolUse`` now
    holds both the frontmatter linter and rtk). Filtering by matcher keeps
    each assertion scoped to one tool.
    """
    return [
        hook["command"]
        for entry in _settings()["hooks"].get(event, [])
        if entry["matcher"] == matcher
        for hook in entry["hooks"]
    ]


def test_config_files_parse_and_are_non_empty() -> None:
    """Guard the loaders themselves — an empty or malformed file would
    otherwise surface below as a confusing ``KeyError``. ``make
    validate-config`` covers syntax in CI; this also rejects a stub ``{}``."""
    assert _settings()
    assert _mcp_servers()


# ── hooks predating this integration — every edit must stay additive ──────────


@pytest.mark.parametrize(
    ("event", "matcher", "command"),
    PREEXISTING_HOOKS,
    ids=[f"{event}:{matcher}" for event, matcher, _ in PREEXISTING_HOOKS],
)
def test_preexisting_hook_survives_verbatim(event: str, matcher: str, command: str) -> None:
    assert command in _hook_commands(event, matcher)


def test_config_change_hook_is_scoped_to_governed_sources() -> None:
    """ADR-0021 / spec-0017: the ConfigChange hook's matcher must scope it to
    only the two sources this repo governs — Claude Code cannot block
    policy_settings regardless, and user_settings/skills are out of scope."""
    commands = _hook_commands("ConfigChange", "project_settings|local_settings")
    assert commands == ["python scripts/harness_config_audit.py"]


def test_no_hook_command_references_the_dead_tool_input_env_var() -> None:
    """Regression guard (ADR-0021 / spec-0017): ``$CLAUDE_TOOL_INPUT_*`` is not
    an environment variable Claude Code defines — hook input arrives as JSON
    on stdin. Every hook command in this file must read stdin (via
    ``--hook``/``--emit-path`` or its own JSON parsing), never that dead
    interpolation, or the protected-path/ruff-autofix hooks silently stop
    firing again exactly as they did before this fix."""
    all_commands = [
        hook["command"]
        for entries in _settings()["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert all_commands, "expected at least one hook command to check"
    assert not any("CLAUDE_TOOL_INPUT" in command for command in all_commands)


# ── rtk-ai/rtk ────────────────────────────────────────────────────────────────


def _rtk_hook_command() -> str:
    commands = _hook_commands(RTK_HOOK_EVENT, RTK_HOOK_MATCHER)
    assert len(commands) == 1
    return commands[0]


def test_rtk_hook_is_registered() -> None:
    assert RTK_HOOK_COMMAND_FRAGMENT in _rtk_hook_command()


def test_rtk_hook_is_opt_out_gated() -> None:
    """The env-var gate is the only supported per-contributor opt-out; without
    it, disabling rtk would mean disabling every hook in the file."""
    assert RTK_DISABLE_ENV in _rtk_hook_command()


def test_rtk_hook_guards_on_binary_presence() -> None:
    """Without a presence guard the hook exits 127 on every Bash tool call for
    anyone who hasn't installed rtk — non-blocking, but it emits a hook-error
    notice each time."""
    assert RTK_BINARY_GUARD_FRAGMENT in _rtk_hook_command()


@pytest.mark.parametrize("env_var", [RTK_DISABLE_ENV, RTK_TELEMETRY_DISABLED_ENV])
def test_rtk_env_flag_is_declared_in_shared_settings(env_var: str) -> None:
    """Both flags must be discoverable in the shared ``env`` block rather than
    existing only implicitly inside a hook command string."""
    assert env_var in _settings()["env"]


def test_rtk_telemetry_is_disabled_by_default() -> None:
    assert _settings()["env"][RTK_TELEMETRY_DISABLED_ENV] == ENV_FLAG_ON


# ── .claude/settings.local.json.example (ADR-0021 / spec-0017) ────────────────


def test_settings_local_example_is_valid_json_with_the_documented_opt_outs() -> None:
    """The committed template for a personal, gitignored
    ``.claude/settings.local.json`` must actually work — a stale/broken
    example would silently mislead the exact contributors it exists to
    help. Every key it documents must be a real, current opt-out."""
    example = _load_json(CLAUDE_SETTINGS_LOCAL_EXAMPLE_RELPATH)
    assert RTK_DISABLE_ENV in example["env"]
    assert "MANGOMAS_HARNESS__CONFIG_AUDIT_MODE" in example["env"]


# ── .mcp.json ─────────────────────────────────────────────────────────────────


def test_mcp_servers_match_the_adopted_set() -> None:
    """Exactly the servers ADR-0020 adopted — no upstream extras, and nothing
    left over from local experimentation."""
    assert set(_mcp_servers()) == set(ADOPTED_MCP_SERVERS)


@pytest.mark.parametrize("server", PATH_SCOPED_MCP_SERVERS)
def test_path_scoped_server_is_pinned_to_the_project_directory(server: str) -> None:
    """Unscoped filesystem/git MCP access would reach beyond this repo."""
    assert MCP_PROJECT_DIR_SCOPE in _mcp_servers()[server]["args"]


def test_no_mcp_server_declares_an_api_key() -> None:
    """None of the adopted servers needs a secret — an ``env`` key here would
    mean either an unreviewed scope change or a key pasted into shared
    config."""
    assert [name for name, cfg in _mcp_servers().items() if "env" in cfg] == []
