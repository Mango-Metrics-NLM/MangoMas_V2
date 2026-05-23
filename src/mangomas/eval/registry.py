"""Scorer registry — reuses the generic :class:`mangomas.registry.Registry`.

New scorers register themselves via :func:`scorer_registry.register` at
import time. The factory accepts a free-form ``dict[str, Any]`` of options
(loaded from :class:`~mangomas.config.EvalSettings.scorer_options`) so
adding a new scorer requires no changes to settings schemas.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mangomas.eval.protocol import Scorer
from mangomas.registry import Registry

ScorerFactory = Callable[[dict[str, Any]], Scorer]
scorer_registry: Registry[ScorerFactory] = Registry("scorer")
