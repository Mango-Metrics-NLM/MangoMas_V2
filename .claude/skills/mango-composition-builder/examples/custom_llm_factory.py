"""Custom LLM factory example for the mango-composition-builder skill.

Demonstrates how to register a custom LLM adapter that conforms to the
``LLMClient`` protocol, so ``build_orchestrator`` can wire it in without
any code changes to the core.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.core.agent import Message

logger = logging.getLogger(__name__)


class StubLLMClient:
    """Protocol-conforming stub useful for test or offline scenarios.

    Implements the ``LLMClient`` contract (``complete`` + ``aclose``).
    Replace the ``complete`` body with your real HTTP call.
    """

    def __init__(self, model: str = "default-model-v1", temperature: float = 0.7) -> None:
        self.model = model
        self.temperature = temperature

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Return a placeholder response (swap for a real backend call)."""
        temp = temperature if temperature is not None else self.temperature
        logger.info("StubLLMClient.complete model=%s temp=%s", self.model, temp)
        return f"[stub] echoed {len(messages)} messages"

    async def aclose(self) -> None:
        """Release resources (no-op for this stub)."""


def custom_llm_factory(config: dict[str, Any] | None = None) -> StubLLMClient:
    """Build a ``StubLLMClient`` from free-form config.

    Register this factory with ``_llm_registry`` in ``composition/llm.py``
    to make it available under a custom provider name.
    """
    config = config or {}
    return StubLLMClient(
        model=config.get("model_name", "default-model-v1"),
        temperature=float(config.get("temperature", 0.7)),
    )
