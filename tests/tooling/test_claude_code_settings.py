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
    ANCHORED_RULE_PATH_PREFIX,
    BASH_RULE_PREFIX,
    BASH_RULE_WILDCARD_SUFFIX,
    CLAUDE_SETTINGS_LOCAL_EXAMPLE_RELPATH,
    CLAUDE_SETTINGS_RELPATH,
    CREDENTIALED_MCP_SERVERS,
    ENV_FLAG_ON,
    ENV_INTERPOLATION_PREFIX,
    EXPECTED_DENY_RULES,
    HARNESS_CONFIG_AUDIT_MODE_ENV,
    INERT_FILE_RULE_PREFIXES,
    MCP_CONFIG_RELPATH,
    MCP_DENY_RULE_PREFIX,
    MCP_PROJECT_DIR_SCOPE,
    PATH_SCOPED_DENY_RULE_PREFIX,
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
    # The PreToolUse/Bash matcher now also carries the protected-path
    # advisory (spec-0022 R11), so filter to the rtk command rather than
    # asserting the pair holds exactly one hook.
    commands = [
        command
        for command in _hook_commands(RTK_HOOK_EVENT, RTK_HOOK_MATCHER)
        if RTK_HOOK_COMMAND_FRAGMENT in command
    ]
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


# ── Bash protected-path advisory (spec-0022 R11) ──────────────────────────────


def test_mcp_deny_rules_name_adopted_servers() -> None:
    """A deny rule naming a nonexistent server is silently inert (spec-0022 R4).

    Claude Code matches MCP rules by exact tool-name string; a typo'd server
    segment produces a dead control that reads like a live one — the same
    defect class as the interior-`*` Bash rule documented in constants. The
    tool names themselves are only as real as the (unpinned) npx server
    version, so at minimum the server segment must resolve.
    """
    mcp_rules = [
        rule for rule in _settings()["permissions"]["deny"] if rule.startswith(MCP_DENY_RULE_PREFIX)
    ]
    assert mcp_rules, "expected MCP deny rules in permissions.deny"
    for rule in mcp_rules:
        server = rule.split("__")[1]
        assert server in ADOPTED_MCP_SERVERS, rule


# ── .claude/settings.local.json.example (ADR-0021 / spec-0017) ────────────────


def test_settings_local_example_is_valid_json_with_the_documented_opt_outs() -> None:
    """The committed template for a personal, gitignored
    ``.claude/settings.local.json`` must actually work — a stale/broken
    example would silently mislead the exact contributors it exists to
    help. Every key it documents must be a real, current opt-out."""
    example = _load_json(CLAUDE_SETTINGS_LOCAL_EXAMPLE_RELPATH)
    assert RTK_DISABLE_ENV in example["env"]
    assert HARNESS_CONFIG_AUDIT_MODE_ENV in example["env"]


# ── permissions (ADR-0020 / ADR-0024) ─────────────────────────────────────────


def _permission_rules(section: str) -> list[str]:
    return list(_settings()["permissions"][section])


def test_deny_rules_match_the_expected_set() -> None:
    """Nothing asserted anything about ``permissions`` before this, so all three
    deny rules could have been dropped by an unrelated edit without a single
    test going red. Set equality names what appeared or vanished."""
    assert set(_permission_rules("deny")) == set(EXPECTED_DENY_RULES)


@pytest.mark.parametrize("section", ["allow", "deny"])
def test_bash_rules_use_only_the_trailing_wildcard(section: str) -> None:
    """An interior ``*`` in a Bash rule is a literal character, not a wildcard.

    Regression guard for ``Bash(python -m ruff *:*)``, which sat in ``allow``
    looking like a working grant while never matching ``python -m ruff check
    --fix`` — so the call prompted every time and the rule was pure decoration.
    """
    for rule in _permission_rules(section):
        if not rule.startswith(BASH_RULE_PREFIX):
            continue
        body = rule[len(BASH_RULE_PREFIX) : -1]
        if "*" not in body:
            continue
        assert body.endswith(BASH_RULE_WILDCARD_SUFFIX), rule
        assert body.count("*") == 1, rule


@pytest.mark.parametrize("section", ["allow", "deny"])
def test_no_rule_uses_a_head_claude_code_ignores(section: str) -> None:
    """``Write(...)``/``NotebookEdit(...)`` are accepted and then never consulted
    for a file write — Claude Code warns at startup and matches only
    ``Edit(...)``, which already covers all three. A rule with one of these
    heads is a control in appearance only."""
    offenders = [
        rule for rule in _permission_rules(section) if rule.startswith(INERT_FILE_RULE_PREFIXES)
    ]
    assert offenders == []


def test_permission_sections_are_non_empty() -> None:
    """Guards the two tests above from passing vacuously if ``permissions`` is
    ever emptied or restructured."""
    assert _permission_rules("allow")
    assert _permission_rules("deny")


# ── .mcp.json ─────────────────────────────────────────────────────────────────


def test_mcp_servers_match_the_adopted_set() -> None:
    """Exactly the servers ADR-0020 adopted — no upstream extras, and nothing
    left over from local experimentation."""
    assert set(_mcp_servers()) == set(ADOPTED_MCP_SERVERS)


@pytest.mark.parametrize("server", PATH_SCOPED_MCP_SERVERS)
def test_path_scoped_server_is_pinned_to_the_project_directory(server: str) -> None:
    """Unscoped filesystem/git MCP access would reach beyond this repo."""
    assert MCP_PROJECT_DIR_SCOPE in _mcp_servers()[server]["args"]


def test_only_the_declared_servers_carry_credentials() -> None:
    """Superseded the old "no server declares an API key" assertion, which the
    github server retires. The property worth keeping is not "no secrets" but
    "no *unreviewed* secrets": an ``env`` block appearing on a sixth server
    should fail until someone adds it to the constant deliberately."""
    with_env = {name for name, cfg in _mcp_servers().items() if cfg.get("env")}
    assert with_env == set(CREDENTIALED_MCP_SERVERS), (
        f"gained credentials: {sorted(with_env - set(CREDENTIALED_MCP_SERVERS))}; "
        f"lost them: {sorted(set(CREDENTIALED_MCP_SERVERS) - with_env)}"
    )


def test_no_mcp_server_hardcodes_a_secret_value() -> None:
    """The replacement for the blanket ban, and the part that actually protects
    anything: every credential must arrive as a ``${VAR}`` interpolation. A
    literal here would be a committed secret in a file cloud sessions load with
    no approval prompt."""
    literals = [
        f"{name}.{key}"
        for name, cfg in _mcp_servers().items()
        for key, value in cfg.get("env", {}).items()
        if not str(value).startswith(ENV_INTERPOLATION_PREFIX)
    ]
    assert literals == []


def test_path_scoped_deny_rules_are_anchored_at_the_project_root() -> None:
    """A leading ``/`` anchors a rule at the settings file's directory. Without
    it the rule is cwd-relative, so it silently stops matching the moment a
    session starts from a subdirectory — a control that looks present and is
    not."""
    unanchored = [
        rule
        for rule in _permission_rules("deny")
        if rule.startswith(PATH_SCOPED_DENY_RULE_PREFIX)
        and not rule[len(PATH_SCOPED_DENY_RULE_PREFIX) :].startswith(ANCHORED_RULE_PATH_PREFIX)
    ]
    assert unanchored == []
