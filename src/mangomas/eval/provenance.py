"""Run provenance — what produced a report (spec-0045 B6).

A baseline diff compares two reports; without knowing *what* each one ran
(which model, which profile, which dataset bytes, which commit) the numbers
are unknowns compared against unknowns. :class:`RunProvenance` is stamped on
every :class:`~mangomas.eval.runner.EvalReport` by the runner, serialised by
the sinks, and ignored by :func:`~mangomas.eval.baseline.diff_reports`, so a
regenerated baseline never "drifts" on a timestamp.

The runner fills what it can see — run id, start time, dataset digest, scorer
and target names, package and schema versions, git commit — and the CLI adds
caller-side ``labels`` (provider, model, profile, option digests) it alone
knows. ``git_sha`` is ``GITHUB_SHA`` when set (CI), else the commit ``.git/HEAD``
resolves to (read from the repository's files off the event loop; no process
is spawned, per spec-0043's bypass inventory), else ``None``: provenance is
best effort and must never fail a run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mangomas import __version__
from mangomas.config import DEFAULT_EVAL_SCHEMA_VERSION
from mangomas.utils import clock

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.dataset import DatasetRow

logger = logging.getLogger(__name__)

GIT_SHA_ENV = "GITHUB_SHA"


@dataclass(frozen=True)
class RunProvenance:
    """Identity of one eval run. Every field is a label, none is a verdict."""

    run_id: str
    started_at: str
    dataset_sha256: str
    scorer: str
    target: str
    mangomas_version: str
    schema_version: int
    git_sha: str | None = None
    labels: dict[str, Any] = field(default_factory=dict)


def digest_of(value: Any) -> str:
    """``sha256:`` over the canonical JSON of *value* (sorted keys, no whitespace)."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def dataset_digest(rows: Iterable[DatasetRow]) -> str:
    """A digest of the rows as *parsed* — stable across whitespace and key order in the file."""
    canonical = [
        {
            "id": row.id,
            "messages": [{"role": m.role, "content": m.content} for m in row.messages],
            "expected": row.expected,
            "metadata": row.metadata,
            "expected_error": row.expected_error,
        }
        for row in rows
    ]
    return digest_of(canonical)


def _git_dir(start: Path) -> Path | None:
    """The ``.git`` directory governing *start*, following a worktree's ``gitdir:`` file."""
    for candidate in (start, *start.parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir():
            return dot_git
        if dot_git.is_file():
            pointer = dot_git.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                target = Path(pointer.partition(":")[2].strip())
                return target if target.is_absolute() else (candidate / target).resolve()
    return None


def _git_head_sync(start: Path | None = None) -> str | None:
    """Read ``HEAD`` from the repository's own files — no process is spawned.

    spec-0043's bypass inventory keeps ``src/mangomas`` free of subprocess
    calls; a provenance label is not worth an exception to that. Handles a
    detached HEAD, a symbolic ref under ``refs/heads/``, a worktree, and
    ``packed-refs``; anything else is ``None``.
    """
    try:
        git_dir = _git_dir((start or Path.cwd()).resolve())
        if git_dir is None:
            return None
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head.partition(":")[2].strip()
        common = git_dir
        common_file = git_dir / "commondir"
        if common_file.is_file():  # a linked worktree keeps refs in the main repository
            common = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
        loose = common / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                sha, _, name = line.partition(" ")
                if name.strip() == ref:
                    return sha.strip() or None
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(
            "Unable to resolve git HEAD from repository files; returning None",
            extra={"error_type": type(exc).__name__},
        )
        return None
    return None


async def resolve_git_sha(start: Path | None = None) -> str | None:
    """``GITHUB_SHA`` when set, else the commit ``.git/HEAD`` points at, else ``None``."""
    from_env = os.environ.get(GIT_SHA_ENV, "").strip()
    if from_env:
        return from_env
    return await asyncio.to_thread(_git_head_sync, start)


async def build_provenance(
    *,
    rows: Iterable[DatasetRow],
    scorer: str,
    target: str,
    labels: Mapping[str, Any] | None = None,
) -> RunProvenance:
    """Stamp a run. Cheap, side-effect free (reads ``.git/HEAD`` off-loop)."""
    return RunProvenance(
        run_id=uuid.uuid4().hex,
        started_at=clock.now().isoformat(),
        dataset_sha256=dataset_digest(rows),
        scorer=scorer,
        target=target,
        mangomas_version=__version__,
        schema_version=DEFAULT_EVAL_SCHEMA_VERSION,
        git_sha=await resolve_git_sha(),
        labels=dict(labels or {}),
    )
