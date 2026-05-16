"""Generic in-process registry for named providers.

Keeps provider look-up decoupled from concrete adapter imports.
Call ``registry.register(name, factory)`` during app wiring; call
``registry.get(name)`` at request time.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Generic, TypeVar, cast

from mangomas.errors import UnknownProvider

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Registry(Generic[T]):
    """A typed *name → value* store with sorted enumeration."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._store: dict[str, T] = {}

    def register(self, name: str, value: T) -> None:
        """Register *value* under *name*.  Last call wins."""
        self._store[name] = value
        logger.debug("Registered %s provider: %r", self._kind, name)

    def get(self, name: str) -> T:
        """Return the value registered for *name*.

        Raises :class:`~mangomas.errors.UnknownProvider` if not found.
        """
        if name not in self._store:
            logger.error(
                "Unknown %s provider %r requested (available: %s)",
                self._kind,
                name,
                self.available(),
            )
            raise UnknownProvider(name, self.available())
        return self._store[name]

    def available(self) -> list[str]:
        """Return a sorted list of registered provider names."""
        return sorted(self._store)

    @contextmanager
    def scoped(self, name: str, value: T) -> Iterator[None]:
        """Temporarily register *value* under *name* for the duration of a block.

        Restores the prior binding (or deletes the entry if absent before) on
        exit, even when the wrapped block raises. Intended for tests that need
        to swap a single provider without mutating global state permanently.

        Example:
            with llm_registry.scoped("lmstudio", non_streaming_factory):
                orch = build_orchestrator(settings)
                ...
        """
        had_prior = name in self._store
        prior_value = self._store.get(name)
        self._store[name] = value
        try:
            yield
        finally:
            if had_prior:
                self._store[name] = cast("T", prior_value)
            else:
                self._store.pop(name, None)
