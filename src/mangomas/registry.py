"""Generic in-process registry for named providers.

Keeps provider look-up decoupled from concrete adapter imports.
Call ``registry.register(name, factory)`` during app wiring; call
``registry.get(name)`` at request time.

Thread-safety
-------------
All mutations and reads are guarded by an internal ``threading.RLock`` so
the registry is safe to use from multiple threads — including the common
case of a threaded ASGI worker (``uvicorn --workers N`` with a thread
pool) populating registries during startup while request handlers read
from them. An ``RLock`` (re-entrant) is used because :meth:`scoped`
calls ``register``/``get``/``available`` under the lock; a non-reentrant
lock would deadlock in that path.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Generic, TypeVar, cast

from mangomas.errors import UnknownProvider

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Registry(Generic[T]):
    """A typed *name → value* store with sorted enumeration.

    Thread-safe: every operation acquires an internal :class:`threading.RLock`
    so concurrent ``register``/``get``/``scoped`` calls are serialised.
    """

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._store: dict[str, T] = {}
        self._lock = threading.RLock()

    def register(self, name: str, value: T) -> None:
        """Register *value* under *name*.  Last call wins."""
        with self._lock:
            self._store[name] = value
        logger.debug("Registered %s provider: %r", self._kind, name)

    def get(self, name: str) -> T:
        """Return the value registered for *name*.

        Raises :class:`~mangomas.errors.UnknownProvider` if not found.
        """
        with self._lock:
            if name not in self._store:
                available = sorted(self._store)
                logger.error(
                    "Unknown %s provider %r requested (available: %s)",
                    self._kind,
                    name,
                    available,
                )
                raise UnknownProvider(name, available)
            return self._store[name]

    def available(self) -> list[str]:
        """Return a sorted list of registered provider names."""
        with self._lock:
            return sorted(self._store)

    @contextmanager
    def scoped(self, name: str, value: T) -> Iterator[None]:
        """Temporarily register *value* under *name* for the duration of a block.

        Restores the prior binding (or deletes the entry if absent before) on
        exit, even when the wrapped block raises. Intended for tests that need
        to swap a single provider without mutating global state permanently.

        Thread-safe via the registry's internal RLock — concurrent calls to
        :meth:`scoped` serialise, and nested ``scoped`` blocks inside the same
        thread do not deadlock thanks to the re-entrant lock.

        Example:
            with llm_registry.scoped("lmstudio", non_streaming_factory):
                orch = build_orchestrator(settings)
                ...
        """
        with self._lock:
            had_prior = name in self._store
            prior_value = self._store.get(name)
            self._store[name] = value
        try:
            yield
        finally:
            with self._lock:
                if had_prior:
                    self._store[name] = cast("T", prior_value)
                else:
                    self._store.pop(name, None)
