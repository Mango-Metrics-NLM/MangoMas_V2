"""Persist a terminal record when a dispatch fails (ADR-0031).

``Orchestrator.dispatch`` reaches ``ctx.repo.save_turn`` only after its loop
returns normally, so every failure path — ``MaxStepsExceeded``,
``StepTimeout``, ``ToolExecutionError``, an LLM error — propagates past it and
writes nothing. The durable log of the system's behaviour therefore recorded
successes and only successes, which is the one shape an audit trail must not
have: "no record" and "never happened" become indistinguishable.

This lives in ``composition/`` rather than in ``core/orchestrator.py`` because
that module is a protected path. The same subclass-to-extend seam
``composition/harness.py`` and ``composition/llm.py`` already use applies here:
``_HarnessOrchestrator.dispatch`` delegates through ``super().dispatch(...)``,
so a mixin ahead of it in the MRO wraps both the harness-enabled and
harness-disabled orchestrators without editing the contract. See ADR-0028 for
the precedent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mangomas.errors import MangomasError

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core.agent import AgentRequest, AgentResponse
    from mangomas.core.loop import AcceptanceFn

logger = logging.getLogger(__name__)

# ``MangomasError.code`` is the typed vocabulary; anything else that escapes a
# dispatch is a bug rather than a modelled outcome, and is recorded under this
# code so it is greppable without pretending it was expected.
UNTYPED_ERROR_CODE: str = "unhandled_error"

# The repository method this mixin needs. Probed with ``hasattr`` rather than
# ``isinstance`` against the Protocol: a runtime_checkable Protocol check is
# method-name-based anyway, and this keeps a third-party backend that satisfies
# only the bare TurnRepository working unchanged instead of erroring.
_FAILURE_WRITER = "save_failed_turn"


class _FailureRecordingMixin:
    """Wrap ``dispatch`` so a raising dispatch still leaves a durable row.

    Deliberately re-raises: this records, it never swallows. A caller that
    handled ``StepTimeout`` before must keep seeing ``StepTimeout``.
    """

    async def dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
        *,
        acceptance_fn: AcceptanceFn | None = None,
        max_steps: int | None = None,
    ) -> AgentResponse:
        """Dispatch, recording a terminal row if the call raises.

        Mirrors ``Orchestrator.dispatch``'s signature exactly rather than
        taking ``**kwargs``: a looser signature is an incompatible override of
        the subclass that sits above it in the MRO, and it would silently drop
        a keyword a future dispatch grows.
        """
        try:
            response: AgentResponse = await super().dispatch(  # type: ignore[misc]
                agent_name,
                request,
                acceptance_fn=acceptance_fn,
                max_steps=max_steps,
            )
        except MangomasError as exc:
            await self._record_failure(agent_name, request, exc.code, str(exc))
            raise
        except Exception as exc:
            await self._record_failure(agent_name, request, UNTYPED_ERROR_CODE, str(exc))
            raise
        return response

    async def _record_failure(
        self,
        agent_name: str,
        request: AgentRequest,
        error_code: str,
        detail: str,
    ) -> None:
        """Best-effort write of the failure row. Never raises.

        A repository that is itself broken must not replace the caller's real
        error with a persistence error — the original exception is the one the
        operator needs, and losing it to a secondary failure while *recording*
        a failure would be a particularly unhelpful trade.
        """
        repo = getattr(self, "context", None) and self.context.repo  # type: ignore[attr-defined]
        writer = getattr(repo, _FAILURE_WRITER, None)
        if not callable(writer):
            logger.debug(
                "No failure-recording repository wired; dispatch failure not persisted",
                extra={"event": "turn_failure_unrecorded", "agent": agent_name},
            )
            return
        try:
            await writer(agent_name, request, error_code=error_code, error=detail)
        except Exception:
            logger.exception(
                "Failed to persist the dispatch failure; re-raising the original error",
                extra={"event": "turn_failure_record_failed", "agent": agent_name},
            )


__all__ = ["UNTYPED_ERROR_CODE", "_FailureRecordingMixin"]
