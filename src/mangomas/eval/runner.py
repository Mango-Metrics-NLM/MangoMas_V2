"""EvalRunner — drives a dataset through the orchestrator and aggregates scores.

The runner reuses :class:`~mangomas.core.Orchestrator` for every dispatch —
no parallel control loop. A fresh correlation id is set per row so the
orchestrator's structured logs are correlatable to a specific eval row.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mangomas.config import (
    DEFAULT_EVAL_PARALLELISM,
    DEFAULT_EVAL_SLICE_KEY,
    EVAL_COST_USD_METADATA_KEY,
)
from mangomas.core import AgentRequest
from mangomas.correlation import (
    generate_correlation_id,
    set_correlation_id,
)
from mangomas.eval.protocol import ScorerContext
from mangomas.eval.provenance import RunProvenance, build_provenance
from mangomas.eval.target import coerce_outcome

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator
    from mangomas.eval.dataset import DatasetRow
    from mangomas.eval.protocol import Scorer
    from mangomas.eval.target import Target

logger = logging.getLogger(__name__)

# Truncation for the persisted per-row ``error`` field. Longer than a transient
# log-detail truncation because this value is surfaced in the report artifact.
_ROW_ERROR_TRUNCATE: int = 500

# Value written into a skipped row's ``error`` field when ``fail_fast`` trips.
# Part of the report artifact's observable shape — rewording it changes what
# every downstream sink and baseline diff sees.
_FAIL_FAST_ERROR: str = "cancelled by fail_fast"


# Row metadata keys for the ``expected_error`` contract (spec-0045 B4).
EXPECTED_ERROR_METADATA_KEY: str = "expected_error"
REFUSED_METADATA_KEY: str = "refused"


def _matches_expected_error(exc: BaseException, expected: str) -> bool:
    """``expected_error`` names the exception class or a ``MangomasError.code``."""
    if type(exc).__name__ == expected:
        return True
    code = getattr(exc, "code", None)
    return isinstance(code, str) and code == expected


def slice_stats(
    dataset: list[DatasetRow], rows: list[EvalRowResult], *, slice_key: str
) -> dict[str, SliceStats]:
    """Per-slice aggregates: rows grouped by ``metadata[slice_key]`` (a string).

    Rows whose metadata lacks the key, or holds a non-string, are unsliced;
    a dataset with no sliced rows yields ``{}``. ``mean_score`` follows the
    report's rule and excludes errored rows.
    """
    by_id = {row.id: row for row in dataset}
    groups: dict[str, list[EvalRowResult]] = {}
    for result in rows:
        source = by_id.get(result.row_id)
        value = source.metadata.get(slice_key) if source is not None else None
        if isinstance(value, str) and value:
            groups.setdefault(value, []).append(result)
    stats: dict[str, SliceStats] = {}
    for name in sorted(groups):
        members = groups[name]
        passed = sum(1 for r in members if r.passed and r.error is None)
        errored = sum(1 for r in members if r.error is not None)
        scored = [r.score for r in members if r.error is None]
        stats[name] = SliceStats(
            size=len(members),
            passed=passed,
            failed=len(members) - passed - errored,
            errored=errored,
            mean_score=sum(scored) / len(scored) if scored else 0.0,
        )
    return stats


def _skipped_row(row: DatasetRow) -> EvalRowResult:
    """Placeholder result for a row ``fail_fast`` skipped before dispatch."""
    return EvalRowResult(
        row_id=row.id,
        score=0.0,
        passed=False,
        duration_ms=0.0,
        prediction="",
        expected=row.expected,
        metadata={"skipped": True},
        error=_FAIL_FAST_ERROR,
    )


def _row_cost_usd(row: EvalRowResult) -> float | None:
    """Return a numeric ``cost_usd`` from *row* metadata, or ``None``."""
    raw = row.metadata.get(EVAL_COST_USD_METADATA_KEY)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    number = float(raw)
    if not math.isfinite(number):
        return None
    return number


def _mean_cost_usd(rows: list[EvalRowResult]) -> float | None:
    """Average cost over non-errored rows that reported ``cost_usd``."""
    costs = [_row_cost_usd(row) for row in rows if row.error is None]
    present = [cost for cost in costs if cost is not None]
    if not present:
        return None
    return sum(present) / len(present)


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
    # spec-0045: the per-row correlation id the runner set (so a row joins to
    # its orchestrator / adapter log lines) and the response metadata the
    # target produced. Both default to "absent" so a baseline written before
    # this field existed still loads.
    correlation_id: str | None = None
    response_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SliceStats:
    """Aggregate for the rows sharing one value of the slice key (spec-0045 B6)."""

    size: int
    passed: int
    failed: int
    errored: int
    mean_score: float


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
    # Mean of per-row ``metadata[cost_usd]`` over non-errored rows that carry
    # a numeric cost. ``None`` when no row reported a cost (every historical
    # scorer, and any run that is not cost-aware).
    mean_cost_usd: float | None = None
    # spec-0045 B6: what produced this report, and per-slice aggregates keyed
    # by the rows' ``metadata[slice_key]``. Both default to "absent" so a
    # baseline written before them still loads; ``diff_reports`` ignores both.
    run: RunProvenance | None = None
    slices: dict[str, SliceStats] = field(default_factory=dict)


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
        parallelism: int = DEFAULT_EVAL_PARALLELISM,
        fail_fast: bool = False,
        slice_key: str = DEFAULT_EVAL_SLICE_KEY,
        provenance_labels: Mapping[str, Any] | None = None,
    ) -> None:
        if parallelism < 1:
            raise ValueError(f"parallelism must be >= 1; got {parallelism}")
        self._orch = orch
        self._scorer = scorer
        self._parallelism = parallelism
        self._fail_fast = fail_fast
        self._slice_key = slice_key
        self._provenance_labels = dict(provenance_labels or {})

    async def _score_row(
        self,
        row: DatasetRow,
        *,
        target: Target,
    ) -> EvalRowResult:
        correlation_id = generate_correlation_id()
        set_correlation_id(correlation_id)
        started = time.monotonic()
        try:
            request = AgentRequest(
                messages=list(row.messages),
                metadata=dict(row.metadata),
            )
            outcome = coerce_outcome(
                await target.run(request, orch=self._orch), target_name=target.name
            )
            prediction = outcome.content
            response_metadata = dict(outcome.metadata)
            if row.expected_error is not None:
                # It answered when it should have refused: a failing row, and
                # the scorer is not consulted — no grade of the answer can
                # redeem a missing refusal.
                duration_ms = (time.monotonic() - started) * 1000
                return EvalRowResult(
                    row_id=row.id,
                    score=0.0,
                    passed=False,
                    duration_ms=duration_ms,
                    prediction=prediction,
                    expected=row.expected,
                    metadata={
                        EXPECTED_ERROR_METADATA_KEY: row.expected_error,
                        REFUSED_METADATA_KEY: False,
                    },
                    correlation_id=correlation_id,
                    response_metadata=response_metadata,
                )
            ctx = ScorerContext(
                llm=self._orch.context.llm,
                embeddings=self._orch.context.embeddings,
                row_metadata=dict(row.metadata),
                correlation_id=correlation_id,
                response_metadata=dict(response_metadata),
                messages=list(row.messages),
            )
            result = await self._scorer.score(prediction, row.expected, context=ctx)
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000
            if row.expected_error is not None and _matches_expected_error(exc, row.expected_error):
                # The row asked for exactly this refusal: a pass, not an error.
                logger.debug(
                    "Eval row refused as expected",
                    extra={
                        "event": "eval_row_refused",
                        "row_id": row.id,
                        "error_type": type(exc).__name__,
                        "duration_ms": duration_ms,
                    },
                )
                return EvalRowResult(
                    row_id=row.id,
                    score=1.0,
                    passed=True,
                    duration_ms=duration_ms,
                    prediction="",
                    expected=row.expected,
                    metadata={
                        EXPECTED_ERROR_METADATA_KEY: row.expected_error,
                        REFUSED_METADATA_KEY: True,
                        "error_type": type(exc).__name__,
                    },
                    correlation_id=correlation_id,
                )
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
                correlation_id=correlation_id,
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
                "cost_usd": dict(result.metadata).get(EVAL_COST_USD_METADATA_KEY),
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
            correlation_id=correlation_id,
            response_metadata=response_metadata,
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
        run = await build_provenance(
            rows=dataset,
            scorer=self._scorer.name,
            target=label,
            labels=self._provenance_labels,
        )
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
                mean_cost_usd=None,
                run=run,
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
        mean_cost_usd = _mean_cost_usd(rows)
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
            mean_cost_usd=mean_cost_usd,
            run=run,
            slices=slice_stats(dataset, rows, slice_key=self._slice_key),
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
                "mean_cost_usd": mean_cost_usd,
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
        """Run rows under a ``parallelism`` semaphore, short-circuiting on ``fail_fast``.

        ``asyncio.gather`` schedules *every* worker up front, so the check
        before the semaphore only catches rows that reach it after a failure
        has already been recorded. For any target that suspends — that is, any
        real LLM call — no row does: they all clear the pre-check and then park
        on the semaphore, so the pre-check alone let a ``fail_fast`` run burn
        the whole dataset's spend. Re-checking *after* the acquire is what
        makes the guarantee hold.

        Skipping at that point rather than cancelling the pending tasks keeps
        ``gather``'s return shape intact: every row still yields exactly one
        :class:`EvalRowResult`, and a row already in flight still records the
        work it paid for.
        """
        semaphore = asyncio.Semaphore(self._parallelism)
        stop_event = asyncio.Event()
        trigger_row_id: str | None = None

        async def _worker(row: DatasetRow) -> EvalRowResult:
            nonlocal trigger_row_id
            # Cheap fast path — never queue on the semaphore at all.
            if stop_event.is_set():
                return _skipped_row(row)
            async with semaphore:
                # Authoritative check: this worker may have waited here while
                # an earlier row failed and set the event.
                if stop_event.is_set():
                    return _skipped_row(row)
                result = await self._score_row(row, target=target)
            if self._fail_fast and not result.passed:
                if not stop_event.is_set():
                    trigger_row_id = row.id
                stop_event.set()
            return result

        results = await asyncio.gather(*(_worker(row) for row in dataset))
        if stop_event.is_set():
            skipped = sum(1 for result in results if result.error == _FAIL_FAST_ERROR)
            logger.warning(
                "Eval run fail_fast tripped at row %s; %s remaining row(s) skipped",
                trigger_row_id,
                skipped,
                extra={
                    "event": "eval_fail_fast",
                    "row_id": trigger_row_id,
                    "skipped": skipped,
                    "dataset_size": len(dataset),
                },
            )
        return results
