"""Entry-point discovery for third-party agents.

Packages can ship agents without editing this repo by declaring entry points
under the ``mangomas.agents`` group, each pointing at an agent *factory*
callable (``Callable[[AgentSettings | None], Agent]`` — the same signature the
built-in factories use in ``composition.py``).

Discovery is **purely additive** and mirrors the eval-harness discovery in
:mod:`mangomas.eval.discovery`: built-ins register at import time, and discovery
only runs when :attr:`~mangomas.config.Settings.discovery_enabled` is ``True``
(default ``False``). A single failing plugin is logged and skipped — it must
never break orchestrator construction for everyone else.

**Collision policy** (differs deliberately from eval's last-call-wins): a
discovered agent whose name collides with a **built-in** is skipped with a
WARNING, so a third-party package can never silently replace ``chat`` /
``planner`` / etc. Collisions between two third-party agents keep last-call-wins.
The "protected" set is the registry's contents *before* discovery runs, so no
built-in names are hard-coded here.
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

# Idempotency latch so repeated build_orchestrator calls in one process scan once.
_discovered = False


def discover_agents(
    registry: Registry[Any],
    protected: frozenset[str],
    *,
    group: str = AGENT_ENTRY_POINT_GROUP,
) -> list[str]:
    """Load and register every entry point in *group* into *registry*.

    Names in *protected* (the built-ins) are skipped with a WARNING. Returns the
    list of names actually registered.
    """
    discovered: list[str] = []
    with _tracer.start_as_current_span("agents.discovery") as span:
        span.set_attribute("discovery.group", group)
        for ep in entry_points(group=group):
            try:
                if ep.name in protected:
                    logger.warning(
                        "Agent plugin %r collides with a built-in agent; skipping",
                        ep.name,
                        extra={
                            "event": "agent_plugin_collision",
                            "group": group,
                            # ``name`` is a reserved LogRecord attribute — use ``plugin``.
                            "plugin": ep.name,
                        },
                    )
                    continue
                factory = ep.load()
                registry.register(ep.name, factory)
            except Exception as exc:
                # Loading *or* registering a plugin must never break discovery
                # for everyone else — log and skip the offending entry point.
                logger.warning(
                    "Agent plugin failed to load or register; skipping",
                    extra={
                        "event": "agent_plugin_load_failed",
                        "group": group,
                        "plugin": ep.name,
                        "error": str(exc),
                    },
                )
                continue
            discovered.append(ep.name)
            logger.debug(
                "Registered discovered agent %r",
                ep.name,
                extra={"event": "agent_plugin_registered", "plugin": ep.name},
            )
        span.set_attribute("discovery.count", len(discovered))
    return discovered


def ensure_agent_plugins(settings: Settings, registry: Registry[Any]) -> None:
    """Run agent discovery once per process when ``settings.discovery_enabled``.

    Idempotent and a no-op when discovery is disabled, so it is safe to call on
    every ``build_orchestrator``. The built-in agents already registered in
    *registry* form the protected set; discovery only layers plugins on top.
    """
    global _discovered  # noqa: PLW0603 — deliberate once-per-process latch
    if not settings.discovery_enabled or _discovered:
        return
    protected = frozenset(registry.available())
    discover_agents(registry, protected)
    _discovered = True
