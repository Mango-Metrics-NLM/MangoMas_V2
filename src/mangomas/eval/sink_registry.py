"""Sink registry — reuses the generic :class:`mangomas.registry.Registry`.

Mirrors :data:`mangomas.eval.registry.scorer_registry`. Built-in sinks register
themselves at import time (see :mod:`mangomas.eval.sinks`); each factory accepts
a free-form ``dict[str, Any]`` of options (from
:attr:`~mangomas.config.EvalSettings.sink_options`), so adding a sink needs no
change to settings schemas.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mangomas.eval.sink import Sink
from mangomas.registry import Registry

SinkFactory = Callable[[dict[str, Any]], Sink]
sink_registry: Registry[SinkFactory] = Registry("sink")
