"""``ConfigChange`` hook decision logic (ADR-0011).

Claude Code's ``ConfigChange`` event fires only for its own configuration
sources — ``user_settings``, ``project_settings`` (``.claude/settings.json``),
``local_settings`` (``.claude/settings.local.json``), ``policy_settings``
(managed policy), and ``skills`` — never for arbitrary project files such as
``pyproject.toml``. This module's decision is therefore keyed on that
event's one documented signal, the changed **source**, and never on a path or
a git diff (an earlier design that tried to guard ``pyproject.toml`` via this
event and read a staged diff for a ``BREAKING-CHANGE`` marker was dropped —
see ADR-0011's "what changed from v1" — because the event never fires for
that target and a staged diff won't reflect an in-session config change).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

ConfigChangeMode = Literal["off", "audit", "block"]
ConfigChangeAction = Literal["allow", "audit", "block"]

# Per Claude Code's ConfigChange decision table: policy-settings changes
# cannot be blocked by a hook regardless of mode. Treating this source as
# blockable would be a false promise, so it always resolves to "allow".
_UNBLOCKABLE_SOURCES: Final[frozenset[str]] = frozenset({"policy_settings"})

_UNKNOWN_SOURCE_LABEL: Final[str] = "<unknown>"


@dataclass(frozen=True)
class ConfigAuditDecision:
    """The hook's decision for one ``ConfigChange`` firing."""

    action: ConfigChangeAction
    source: str
    reason: str


def evaluate_config_change(source: str | None, mode: ConfigChangeMode) -> ConfigAuditDecision:
    """Decide the ``ConfigChange`` hook's response for *source* under *mode*.

    - ``mode == "off"`` (default): always ``allow`` — the hook is inert.
    - *source* is one of :data:`_UNBLOCKABLE_SOURCES` (currently
      ``policy_settings``): always ``allow``, since Claude Code cannot block
      those changes regardless of mode.
    - *source* is ``None``: fails open (``allow``) — the stdin field naming
      the changed source is undocumented upstream (see ADR-0011's known
      limitation); an absent or unrecognized value must never silently
      escalate to a block.
    - Otherwise: ``"audit"`` logs and allows; ``"block"`` blocks.
    """
    resolved_source = source or _UNKNOWN_SOURCE_LABEL
    if mode == "off":
        return ConfigAuditDecision("allow", resolved_source, "config_audit_mode is off")
    if source in _UNBLOCKABLE_SOURCES:
        return ConfigAuditDecision(
            "allow", resolved_source, f"{source} changes cannot be blocked by hooks"
        )
    if source is None:
        return ConfigAuditDecision(
            "allow", resolved_source, "changed source missing from hook payload; failing open"
        )
    if mode == "audit":
        return ConfigAuditDecision("audit", resolved_source, f"auditing change to {source}")
    return ConfigAuditDecision("block", resolved_source, f"blocking unmarked change to {source}")
