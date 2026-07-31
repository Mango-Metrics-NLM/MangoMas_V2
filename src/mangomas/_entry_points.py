"""Shared entry-point loading for Mango-Mas plugin discovery.

Both :mod:`mangomas.agents.discovery` and :mod:`mangomas.eval.discovery`
iterate ``importlib.metadata.entry_points(group=...)`` and load each entry
point's factory, guarding against exactly one failure mode in common: a
broken plugin (missing module, import-time error, wrong attribute path, ...)
must never abort discovery for every other entry point in the group. This
module holds *only* that one mechanism — :func:`load_entry_point_factory`.

Everything else stays caller-side, deliberately:

* ``entry_points(group=group)`` itself is still imported and called directly
  inside each discovery module (not re-exported here) because both
  ``tests/agents/test_discovery.py`` and ``tests/eval/test_discovery.py``
  monkeypatch ``discovery.entry_points`` — the module-level name bound by
  ``from importlib.metadata import entry_points`` in *that specific module*.
  Moving the call itself into this shared module would resolve
  ``entry_points`` against this module's globals instead and silently
  defeat that monkeypatch seam.
* The non-callable guard and every log call (message text, structured
  ``event`` name, and whether the check even runs before or after loading)
  differ between the two modules — ``agents/discovery.py`` also skips a
  built-in-name collision *before* attempting to load, while
  ``eval/discovery.py`` checks a last-call-wins override *after* loading —
  so both stay entirely caller-side rather than being forced into one shape.
  Because of this, the shared helper here loads exactly one entry point per
  call (not a full ``for ep in entry_points(...)`` generator over the group);
  each caller's own loop keeps calling it at the exact point it used to call
  ``ep.load()`` directly, which preserves each module's existing check order
  byte-for-byte.
* ``registry.register(ep.name, factory)`` is no longer covered by the same
  ``try/except`` that guards the load (previously one broad ``except
  Exception`` wrapped both). ``Registry.register`` is a lock-guarded ``dict``
  assignment with no validation, so this narrows the safety net without
  narrowing anything that can realistically fail today.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import Any


def load_entry_point_factory(ep: EntryPoint) -> tuple[Any | None, str | None]:
    """Load *ep*, never letting a broken plugin raise past this call.

    Returns ``(factory, None)`` on success or ``(None, error_detail)`` when
    ``ep.load()`` raises — mirroring the ``try: factory = ep.load() / except
    Exception as exc: ...`` block duplicated verbatim (missing module,
    import-time error, ...) in both discovery modules. Callers decide how to
    log a ``None`` factory — message text and the structured ``event`` name
    differ between ``agents.discovery`` and ``eval.discovery`` — using
    ``error_detail`` (``str(exc)``) as their log's ``"error"`` field.
    """
    try:
        return ep.load(), None
    except Exception as exc:
        return None, str(exc)
