"""Runtime-checkable conformance of built-in targets to the Target protocol."""

from __future__ import annotations

from mangomas.eval.target import Target
from mangomas.eval.targets import AgentTarget, EchoTarget, FanOutTarget, PipelineTarget


def test_builtin_targets_satisfy_protocol() -> None:
    assert isinstance(AgentTarget("chat"), Target)
    assert isinstance(EchoTarget(), Target)
    assert isinstance(PipelineTarget(["chat"]), Target)
    assert isinstance(FanOutTarget(["chat"]), Target)
