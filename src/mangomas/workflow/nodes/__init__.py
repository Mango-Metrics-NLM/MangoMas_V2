"""Built-in workflow node executors.

Importing this package registers every built-in node executor in
:data:`mangomas.workflow.registry.node_registry`. A third-party node kind would
follow the same self-registration pattern.
"""

from __future__ import annotations

from mangomas.workflow.nodes.agent import AgentNodeExecutor
from mangomas.workflow.nodes.branch import BranchNodeExecutor
from mangomas.workflow.nodes.fan_out import FanOutNodeExecutor
from mangomas.workflow.nodes.loop import LoopNodeExecutor
from mangomas.workflow.nodes.sequence import SequenceNodeExecutor

__all__ = [
    "AgentNodeExecutor",
    "BranchNodeExecutor",
    "FanOutNodeExecutor",
    "LoopNodeExecutor",
    "SequenceNodeExecutor",
]
