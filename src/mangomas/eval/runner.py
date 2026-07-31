"""EvalRunner — drives a dataset through the orchestrator and aggregates scores.

The runner reuses :class:`~mangomas.core.Orchestrator` for every dispatch —
no parallel control loop. A fresh correlation id is set per row so the
orchestrator's structured logs are correlatable to a specific eval row.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mangomas.core import AgentRequest
from mangomas.correlation import (
    generate_correlation_id,
    set_correlation_id,
)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator
    from mangomas.eval.dataset import DatasetRow
    from mangomas.eval.protocol import Scorer
    from mangomas.eval.target import Target

logger = logging.getLogger(__name__)

# Truncation for the persisted per-row ``error`` field. Longer than a transient
# log-detail truncation because this value is surfaced in the report artifact.
_ROW_ERROR_TRUNCATE: int = 500


@dataclass(frozen=True)
class EvalRowResult:
    """Per-row eval outcome."""

    row_id: str
    score: float
    passed: bool
    duration_ms: float
    prediction: str
    expected: str
    metadata: dict[str, object] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class EvalReport:
    """Aggregate report for an entire dataset run."""

    scorer: str
    agent_name: str
    dataset_size: int
    passed: int
    failed: int
    errored: int
    mean_score: float
    duration_ms: float
    rows: list[EvalRowResult] = field(default_factory=list)
    # Name of the evaluated target. Defaults to "" so old constructions /
    # baseline JSON artifacts (which predate target indirection) stay valid.
    # For the default ``agent`` target this equals ``agent_name``.
    target_name: str = ""


class EvalRunner:
    """Run a :class:`Scorer` over a dataset using an existing :class:`Orchestrator`.

    Parameters
    ----------
    orch:
        Pre-built orchestrator wired through ``build_orchestrator(settings)``.
    scorer:
        Any object satisfying :class:`Scorer`.
    parallelism:
        Maximum concurrent rows (default ``1`` — sequential). Enforced via
        ``asyncio.Semaphore``.
    fail_fast:
        When ``True``, the first non-passing row cancels the remaining tasks.
        Useful for CI gates that want quick failure rather than full data.
    """

    def __init__(
        self,
        orch: Orchestrator,
        scorer: Scorer,
        *,
        parallelism: int = 1,
        fail_fast: bool = False,
    ) -> None:
        if parallelism < 1:
            raise ValueError(f"parallelism must be >= 1; got {parallelism}")
        self._orch = orch
        self._scorer = scorer
        self._parallelism = parallelism
        self._fail_fast = fail_fast

    async def _score_row(
        self,
        row: DatasetRow,
        *,
        target: Target,
    ) -> EvalRowResult:
        from mangomas.eval.protocol import ScorerContext  # noqa: PLC0415

        correlation_id = generate_correlation_id()
        set_correlation_id(correlation_id)
        started = time.monotonic()
        try:
            request = AgentRequest(
                messages=list(row.messages),
                metadata=dict(row.metadata),
            )
            prediction = await target.run(request, orch=self._orch)
            ctx = ScorerContext(
                llm=self._orch.context.llm,
                embeddings=self._orch.context.embeddings,
                row_metadata=dict(row.metadata),
                correlation_id=correlation_id,
            )
            result = await self._scorer.score(prediction, row.expected, context=ctx)
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000
            logger.error(
                "Eval row errored",
                extra={
                    "event": "eval_error",
                    "row_id": row.id,
                    "error_type": type(exc).__name__,
                    "duration_ms": duration_ms,
                },
            )
            return EvalRowResult(
                row_id=row.id,
                score=0.0,
                passed=False,
                duration_ms=duration_ms,
                prediction="",
                expected=row.expected,
                metadata={"error_type": type(exc).__name__},
                error=str(exc)[:_ROW_ERROR_TRUNCATE],
            )
        duration_ms = (time.monotonic() - started) * 1000
        logger.debug(
            "Eval row scored",
            extra={
                "event": "eval_row",
                "row_id": row.id,
                "score": result.score,
                "passed": result.passed,
                "duration_ms": duration_ms,
            },
        )
        return EvalRowResult(
            row_id=row.id,
            score=result.score,
            passed=result.passed,
            duration_ms=duration_ms,
            prediction=prediction,
            expected=row.expected,
            metadata=dict(result.metadata),
        )

    async def run(
        self,
        dataset: list[DatasetRow],
        agent_name: str | None = None,
        *,
        target: Target | None = None,
    ) -> EvalReport:
        """Score every row in ``dataset`` against a target.

        Backward compatible: callers that pass ``agent_name`` (the legacy
        signature) keep working — it is wrapped in the default ``agent`` target.
        Pass ``target`` to evaluate a pipeline / fan-out / echo baseline instead.
        Exactly one of ``agent_name`` / ``target`` must be supplied.
        """
        eff_target = self._resolve_target(agent_name, target)
        label = eff_target.name
        if not dataset:
            return EvalReport(
                scorer=self._scorer.name,
                agent_name=label,
                dataset_size=0,
                passed=0,
                failed=0,
                errored=0,
                mean_score=0.0,
                duration_ms=0.0,
                rows=[],
                target_name=label,
            )
        logger.info(
            "Eval run starting",
            extra={
                "event": "eval_start",
                "dataset_size": len(dataset),
                "scorer": self._scorer.name,
                "agent_name": label,
                "target_name": label,
                "parallelism": self._parallelism,
                "fail_fast": self._fail_fast,
            },
        )
        started = time.monotonic()
        rows = await self._dispatch_rows(dataset, eff_target)
        duration_ms = (time.monotonic() - started) * 1000
        passed = sum(1 for r in rows if r.passed and r.error is None)
        errored = sum(1 for r in rows if r.error is not None)
        scored = sum(1 for r in rows if r.error is None)
        mean_score = sum(r.score for r in rows if r.error is None) / scored if scored else 0.0
        failed = len(rows) - passed - errored
        report = EvalReport(
            scorer=self._scorer.name,
            agent_name=label,
            dataset_size=len(dataset),
            passed=passed,
            failed=failed,
            errored=errored,
            mean_score=mean_score,
            duration_ms=duration_ms,
            rows=rows,
            target_name=label,
        )
        logger.info(
            "Eval run complete",
            extra={
                "event": "eval_summary",
                "dataset_size": report.dataset_size,
                "passed": passed,
                "failed": failed,
                "errored": errored,
                "mean_score": mean_score,
                "duration_ms": duration_ms,
                "target_name": label,
            },
        )
        return report

    @staticmethod
    def _resolve_target(agent_name: str | None, target: Target | None) -> Target:
        """Pick the effective target: an explicit *target* wins over *agent_name*.

        ``agent_name`` is wrapped in the default ``agent`` target so the legacy
        ``run(dataset, agent_name=...)`` signature keeps working unchanged.
        """
        if target is not None:
            return target
        if agent_name is None:
            raise ValueError("run requires either an agent_name or a target")
        from mangomas.eval.targets.agent import AgentTarget  # noqa: PLC0415

        return AgentTarget(agent=agent_name)

    async def _dispatch_rows(
        self,
        dataset: list[DatasetRow],
        target: Target,
    ) -> list[EvalRowResult]:
        """Execute rows respecting ``parallelism`` and ``fail_fast``."""
        if self._parallelism == 1 and not self._fail_fast:
            return [await self._score_row(row, target=target) for row in dataset]
        return await self._dispatch_concurrent(dataset, target)

    async def _dispatch_concurrent(
        self,
        dataset: list[DatasetRow],
        target: Target,
    ) -> list[EvalRowResult]:
        semaphore = asyncio.Semaphore(self._parallelism)
        stop_event = asyncio.Event()

        async def _worker(row: DatasetRow) -> EvalRowResult:
            if stop_event.is_set():
                return EvalRowResult(
                    row_id=row.id,
                    score=0.0,
                    passed=False,
                    duration_ms=0.0,
                    prediction="",
                    expected=row.expected,
                    metadata={"skipped": True},
                    error="cancelled by fail_fast",
                )
            async with semaphore:
                result = await self._score_row(row, target=target)
            if self._fail_fast and not result.passed:
                stop_event.set()
            return result

        return await asyncio.gather(*(_worker(row) for row in dataset))
