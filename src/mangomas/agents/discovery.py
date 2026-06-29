"""Entry-point discovery for third-party agents.

Packages can ship agents without editing this repo by declaring entry points
under the ``mangomas.agents`` group, each pointing at an *agent factory* —
``Callable[[AgentSettings | None], Agent]`` (the same shape used by the built-in
registrations in :mod:`mangomas.composition`).

Discovery is **purely additive**: built-in agents still register at import time,
and discovery only runs when :attr:`~mangomas.config.Settings.discovery_enabled`
is ``True`` (default ``False``). A single failing plugin is logged and skipped —
it must never break orchestrator construction for everyone else. A plugin that
registers under an existing name intentionally overrides the built-in
(last-call-wins); that is logged at INFO.

This mirrors :mod:`mangomas.eval.discovery` for the agent registry. The registry
is passed in by the caller (``build_orchestrator``) rather than imported here, so
that this module never imports :mod:`mangomas.composition` (which would create an
import cycle, since ``composition`` imports the concrete agents).
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from mangomas.telemetry import get_tracer

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import Settings
    from mangomas.registry import Registry

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)

AGENT_ENTRY_POINT_GROUP = "mangomas.agents"

# Idempotency latch so repeated orchestrator builds in one process scan once.
_discovered = False


def discover_agents(
    *,
    registry: Registry[Any],
    group: str = AGENT_ENTRY_POINT_GROUP,
) -> list[str]:
    """Load and register every entry point in *group* into *registry*.

    Returns the names registered. A plugin that fails to load (or whose loaded
    object is not callable) is logged and skipped; it never aborts discovery for
    the remaining entry points.
    """
    existing = set(registry.available())
    discovered: list[str] = []
    with _tracer.start_as_current_span("agent.discovery") as span:
        span.set_attribute("discovery.group", group)
        for ep in entry_points(group=group):
            try:
                factory = ep.load()
                if not callable(factory):
                    raise TypeError(
                        f"agent factory {ep.name!r} is not callable: {type(factory).__name__}"
                    )
                if ep.name in existing:
                    logger.info(
                        "Agent plugin overrides built-in agent %r",
                        ep.name,
                        extra={"event": "agent_plugin_override", "group": group, "plugin": ep.name},
                    )
                registry.register(ep.name, factory)
            except Exception as exc:
                # Loading *or* registering a plugin must never break discovery
                # for everyone else — log and skip the offending entry point.
                logger.warning(
                    "Agent plugin failed to load or register; skipping",
                    extra={
                        "event": "agent_plugin_load_failed",
                        "group": group,
                        # ``name`` is a reserved LogRecord attribute — use ``plugin``.
                        "plugin": ep.name,
                        "error": str(exc),
                    },
                )
                continue
            discovered.append(ep.name)
        span.set_attribute("discovery.count", len(discovered))
    return discovered


def ensure_agent_plugins(settings: Settings, registry: Registry[Any]) -> None:
    """Run agent discovery once per process when ``settings.discovery_enabled``.

    Idempotent and a no-op when discovery is disabled, so it is safe to call from
    every ``build_orchestrator`` invocation. Built-in agents are already
    registered at import time; this only layers in entry-point plugins.
    """
    global _discovered  # noqa: PLW0603 — deliberate once-per-process latch
    if not settings.discovery_enabled or _discovered:
        return
    discover_agents(registry=registry)
    _discovered = True


__all__ = [
    "AGENT_ENTRY_POINT_GROUP",
    "discover_agents",
    "ensure_agent_plugins",
]
