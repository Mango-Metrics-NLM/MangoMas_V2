"""Entry-point discovery for third-party scorers and sinks.

Packages can ship eval scorers/sinks without editing this repo by declaring
entry points under the ``mangomas.eval.scorers`` / ``mangomas.eval.sinks``
groups, each pointing at a factory callable (``Callable[[dict], Scorer|Sink]``).

Discovery is **purely additive**: built-ins still register at import time, and
discovery only runs when :attr:`~mangomas.config.Settings.discovery_enabled` is
``True`` (default ``False``). A single failing plugin is logged and skipped — it
must never break the harness for everyone else. A plugin that registers under an
existing name intentionally overrides the built-in (last-call-wins); that is
logged at INFO.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from mangomas.eval.registry import scorer_registry
from mangomas.eval.sink_registry import sink_registry
from mangomas.eval.target_registry import target_registry
from mangomas.telemetry import get_tracer

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import Settings
    from mangomas.registry import Registry

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)

SCORER_ENTRY_POINT_GROUP = "mangomas.eval.scorers"
SINK_ENTRY_POINT_GROUP = "mangomas.eval.sinks"
TARGET_ENTRY_POINT_GROUP = "mangomas.eval.targets"

# Idempotency latch so repeated CLI invocations in one process scan once.
_discovered = False


def _discover(group: str, registry: Registry[Any], label: str) -> list[str]:
    """Load and register every entry point in *group* into *registry*."""
    existing = set(registry.available())
    discovered: list[str] = []
    with _tracer.start_as_current_span("eval.discovery") as span:
        span.set_attribute("discovery.group", group)
        for ep in entry_points(group=group):
            try:
                factory = ep.load()
                if ep.name in existing:
                    logger.info(
                        "Eval plugin overrides built-in %s %r",
                        label,
                        ep.name,
                        extra={"event": "eval_plugin_override", "group": group, "plugin": ep.name},
                    )
                registry.register(ep.name, factory)
            except Exception as exc:
                # Loading *or* registering a plugin must never break discovery
                # for everyone else — log and skip the offending entry point.
                logger.warning(
                    "Eval plugin failed to load or register; skipping",
                    extra={
                        "event": "eval_plugin_load_failed",
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


def discover_scorers(
    *,
    registry: Registry[Any] = scorer_registry,
    group: str = SCORER_ENTRY_POINT_GROUP,
) -> list[str]:
    """Discover and register third-party scorers. Returns the names registered."""
    return _discover(group, registry, "scorer")


def discover_sinks(
    *,
    registry: Registry[Any] = sink_registry,
    group: str = SINK_ENTRY_POINT_GROUP,
) -> list[str]:
    """Discover and register third-party sinks. Returns the names registered."""
    return _discover(group, registry, "sink")


def discover_targets(
    *,
    registry: Registry[Any] = target_registry,
    group: str = TARGET_ENTRY_POINT_GROUP,
) -> list[str]:
    """Discover and register third-party targets. Returns the names registered."""
    return _discover(group, registry, "target")


def ensure_eval_plugins(settings: Settings) -> None:
    """Run discovery once per process when ``settings.discovery_enabled``.

    Idempotent and a no-op when discovery is disabled, so it is safe to call
    from every CLI entry point. Built-in scorers/sinks are already registered
    at import time; this only layers in entry-point plugins.
    """
    global _discovered  # noqa: PLW0603 — deliberate once-per-process latch
    if not settings.discovery_enabled or _discovered:
        return
    discover_scorers()
    discover_sinks()
    discover_targets()
    _discovered = True
