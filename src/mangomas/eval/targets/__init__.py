"""Built-in eval targets.

Importing this package registers every built-in target in
:data:`~mangomas.eval.target_registry.target_registry`. External plugins follow
the same pattern (and may be auto-discovered via :mod:`mangomas.eval.discovery`).
"""

from __future__ import annotations

from mangomas.eval.targets.agent import AgentTarget
from mangomas.eval.targets.echo import EchoTarget
from mangomas.eval.targets.fan_out import FanOutTarget
from mangomas.eval.targets.pipeline import PipelineTarget

__all__ = ["AgentTarget", "EchoTarget", "FanOutTarget", "PipelineTarget"]
