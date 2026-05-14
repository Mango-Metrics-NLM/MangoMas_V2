"""Generic in-process registry for named providers.

Keeps provider look-up decoupled from concrete adapter imports.
Call ``registry.register(name, factory)`` during app wiring; call
``registry.get(name)`` at request time.
"""

from __future__ import annotations

import logging
from typing import Generic, TypeVar

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
