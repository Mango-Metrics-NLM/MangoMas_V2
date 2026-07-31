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
from threading import Lock
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from mangomas._entry_points import load_entry_point_factory
from mangomas.eval.dataset_source import dataset_source_registry
from mangomas.eval.registry import scorer_registry
from mangomas.eval.sink_registry import sink_registry
from mangomas.eval.target_registry import target_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import Settings
    from mangomas.registry import Registry

logger = logging.getLogger(__name__)

# The tracer is acquired lazily inside ``_discover`` via the raw
# ``trace.get_tracer`` (matching ``agents/discovery.py`` and
# ``core/orchestrator.py``) — never the auto-configuring
# ``mangomas.telemetry.get_tracer`` at import time. This module is imported
# by the CLI (and transitively by ``api/app.py`` via shared plugin wiring);
# configuring telemetry at import would make the FastAPI lifespan's
# ``configure_telemetry`` a no-op and silently ignore the configured exporter
# / log format.

SCORER_ENTRY_POINT_GROUP = "mangomas.eval.scorers"
SINK_ENTRY_POINT_GROUP = "mangomas.eval.sinks"
TARGET_ENTRY_POINT_GROUP = "mangomas.eval.targets"
DATASET_SOURCE_ENTRY_POINT_GROUP = "mangomas.eval.dataset_sources"

# Idempotency latch, tracked *per registry instance* (by id) rather than a
# single global boolean — mirrors ``agents/discovery.py``. A per-registry
# latch is both thread-safe (guarded by the lock) and keeps a scan of one
# registry from suppressing discovery on another (e.g. an injected registry
# in tests), which the previous single ``bool`` latch could not do.
_discovered_registries: set[int] = set()
_discovery_lock = Lock()


def _discover(group: str, registry: Registry[Any], label: str) -> list[str]:
    """Load and register every entry point in *group* into *registry*."""
    existing = set(registry.available())
    discovered: list[str] = []
    with trace.get_tracer(__name__).start_as_current_span("eval.discovery") as span:
        span.set_attribute("discovery.group", group)
        for ep in entry_points(group=group):
            # A broken plugin (missing module, import-time error, ...) must
            # never break discovery for everyone else — ``factory`` is
            # ``None`` instead of raising when ``ep.load()`` fails.
            factory, error = load_entry_point_factory(ep)
            if factory is None:
                logger.warning(
                    "Eval plugin failed to load or register; skipping",
                    extra={
                        "event": "eval_plugin_load_failed",
                        "group": group,
                        # ``name`` is a reserved LogRecord attribute — use ``plugin``.
                        "plugin": ep.name,
                        "error": error,
                    },
                )
                continue
            if not callable(factory):
                logger.warning(
                    "Eval plugin is not callable; skipping",
                    extra={
                        "event": "eval_plugin_not_callable",
                        "group": group,
                        "plugin": ep.name,
                    },
                )
                continue
            if ep.name in existing:
                logger.info(
                    "Eval plugin overrides built-in %s %r",
                    label,
                    ep.name,
                    extra={"event": "eval_plugin_override", "group": group, "plugin": ep.name},
                )
            registry.register(ep.name, factory)
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


def discover_dataset_sources(
    *,
    registry: Registry[Any] = dataset_source_registry,
    group: str = DATASET_SOURCE_ENTRY_POINT_GROUP,
) -> list[str]:
    """Discover and register third-party dataset sources. Returns names registered."""
    return _discover(group, registry, "dataset_source")


def ensure_eval_plugins(settings: Settings) -> None:
    """Run discovery once per process when ``settings.discovery_enabled``.

    Idempotent and a no-op when discovery is disabled, so it is safe to call
    from every CLI entry point. Built-in scorers/sinks are already registered
    at import time; this only layers in entry-point plugins.

    The idempotency latch is a per-registry-id set guarded by a lock (mirrors
    ``agents.discovery.ensure_agent_plugins``), not a single global ``bool`` —
    so concurrent first calls cannot both pass the check and double-scan, and
    a registry that has already been scanned is tracked independently of the
    others (matters for tests that swap one of the four module-level
    registries for a fresh instance).
    """
    if not settings.discovery_enabled:
        return
    # Resolved dynamically against the module namespace (not via the
    # discover_* functions' default arguments, which bind at *definition*
    # time) so a monkeypatched ``discovery.scorer_registry`` etc. is both
    # what the latch checks and what actually gets scanned.
    registries = (scorer_registry, sink_registry, target_registry, dataset_source_registry)
    if all(id(r) in _discovered_registries for r in registries):
        return
    with _discovery_lock:
        # Double-checked under the lock so concurrent calls scan exactly once.
        if all(id(r) in _discovered_registries for r in registries):
            return
        discover_scorers(registry=registries[0])
        discover_sinks(registry=registries[1])
        discover_targets(registry=registries[2])
        discover_dataset_sources(registry=registries[3])
        _discovered_registries.update(id(r) for r in registries)
