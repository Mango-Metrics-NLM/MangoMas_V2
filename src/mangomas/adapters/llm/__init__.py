"""LLM adapters."""

from mangomas.adapters.llm.base import LLMClient
from mangomas.adapters.llm.lmstudio import LMStudioClient

__all__ = ["LLMClient", "LMStudioClient"]
