"""Control-loop types for the iterative dispatch harness."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeAlias

from mangomas.core.agent import AgentResponse

AcceptanceFn: TypeAlias = Callable[[AgentResponse], bool]
"""Callable that returns ``True`` when a response satisfies acceptance criteria."""

logger = logging.getLogger(__name__)
