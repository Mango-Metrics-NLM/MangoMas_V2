"""Target registry — reuses the generic :class:`mangomas.registry.Registry`.

Mirrors :data:`mangomas.eval.sink_registry.sink_registry`. Built-in targets
register themselves at import time (see :mod:`mangomas.eval.targets`); each
factory accepts a free-form ``dict[str, Any]`` of options (from
:attr:`~mangomas.config.EvalSettings.target_options`), so adding a target needs
no change to settings schemas.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mangomas.eval.target import Target
from mangomas.registry import Registry

TargetFactory = Callable[[dict[str, Any]], Target]
target_registry: Registry[TargetFactory] = Registry("target")
