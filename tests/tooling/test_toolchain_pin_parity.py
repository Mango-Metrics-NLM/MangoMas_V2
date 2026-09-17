"""The exact-pinned lint/type toolchain stays in lockstep with pre-commit.

``pyproject.toml`` pins ``ruff`` and ``mypy`` exactly and its comments claim
they are "kept in lockstep with the ruff-pre-commit rev in
``.pre-commit-config.yaml``". Nothing enforced that claim, so the two could
drift silently — and they *would* have: a Dependabot PR bumping the pip pin
is green today, because the pip ecosystem never touches
``.pre-commit-config.yaml``. Merging one alone leaves contributors running
pre-commit on a different ruff than CI runs, which shows up as a formatting
diff nobody can reproduce.

This is the spec-0022 R15 pattern — a constraint written as a mechanism
survives agent turnover, a constraint written as a comment does not.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import NamedTuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PRECOMMIT = _REPO_ROOT / ".pre-commit-config.yaml"


class ToolPin(NamedTuple):
    """One exact-pinned tool and the pre-commit repo that must match it."""

    distribution: str
    precommit_repo: str


#: Every tool pinned exactly in ``[project.optional-dependencies].dev`` that also
#: runs as a pre-commit hook. Adding a tool here is how the contract grows — the
#: test body carries no tool names.
TOOL_PINS: tuple[ToolPin, ...] = (
    ToolPin("ruff", "https://github.com/astral-sh/ruff-pre-commit"),
    ToolPin("mypy", "https://github.com/pre-commit/mirrors-mypy"),
)


def pyproject_exact_pin(distribution: str, *, text: str | None = None) -> str | None:
    """Return the exact-pinned version of *distribution*, or ``None``.

    Reads the parsed ``dev`` extra rather than regexing the file, so a
    requirement written with extras or whitespace still resolves.
    """
    raw = _PYPROJECT.read_text(encoding="utf-8") if text is None else text
    data = tomllib.loads(raw)
    dev: list[str] = data["project"]["optional-dependencies"]["dev"]
    for requirement in dev:
        match = re.fullmatch(rf"\s*{re.escape(distribution)}\s*==\s*([^\s;]+)\s*", requirement)
        if match:
            return match.group(1)
    return None


def precommit_rev(repo_url: str, *, text: str | None = None) -> str | None:
    """Return the ``rev:`` for *repo_url*, leading ``v`` stripped, or ``None``.

    Deliberately a small line scan rather than a YAML dependency: this test must
    keep working in the stdlib-only situations the harness scripts also target,
    and the contract is "this repo's rev", not a full loader.
    """
    raw = _PRECOMMIT.read_text(encoding="utf-8") if text is None else text
    pending = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- repo:"):
            pending = stripped.split(":", 1)[1].strip() == repo_url
            continue
        if pending and stripped.startswith("rev:"):
            return stripped.split(":", 1)[1].strip().lstrip("v")
    return None


@pytest.mark.parametrize("pin", TOOL_PINS, ids=lambda p: p.distribution)
def test_pyproject_pin_matches_precommit_rev(pin: ToolPin) -> None:
    """The pip pin and the pre-commit rev name the same version."""
    pinned = pyproject_exact_pin(pin.distribution)
    rev = precommit_rev(pin.precommit_repo)
    assert pinned is not None, f"{pin.distribution} is not exact-pinned in the dev extra"
    assert rev is not None, f"{pin.precommit_repo} has no rev in {_PRECOMMIT.name}"
    assert pinned == rev, (
        f"{pin.distribution} desync: pyproject pins {pinned!r} but "
        f"{_PRECOMMIT.name} pins {rev!r}. Bump both together — a pip-only "
        f"Dependabot PR will not touch the pre-commit rev."
    )


@pytest.mark.parametrize("pin", TOOL_PINS, ids=lambda p: p.distribution)
def test_parity_check_detects_a_desync(pin: ToolPin) -> None:
    """Prove the guard fires (mango-mutation-proof): desync it and it must differ.

    Without this the assertion above could pass vacuously — for instance if both
    readers returned ``None`` and a future edit relaxed the not-None guards.
    """
    real = precommit_rev(pin.precommit_repo)
    assert real is not None
    mutated = _PRECOMMIT.read_text(encoding="utf-8").replace(
        f"rev: v{real}", "rev: v0.0.0-desynced", 1
    )
    assert precommit_rev(pin.precommit_repo, text=mutated) != pyproject_exact_pin(pin.distribution)


def test_unknown_distribution_reads_as_absent() -> None:
    """A tool not pinned in the dev extra yields ``None``, not a false match."""
    assert pyproject_exact_pin("definitely-not-a-real-distribution") is None


def test_unknown_precommit_repo_reads_as_absent() -> None:
    assert precommit_rev("https://example.invalid/not-a-repo") is None
