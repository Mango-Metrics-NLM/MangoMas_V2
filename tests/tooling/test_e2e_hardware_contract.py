"""Lint the hardware contract spec-0029 R2 states in prose (spec-0029 R7.1).

**What this is.** A text lint over the test files that touch real hardware —
the live-model suites and the local-compute RAG suite. It checks the four
rules of the contract that can be checked mechanically, and it exists because
a rule recorded only in a spec is a rule the next author never reads. CLAUDE.md
asks every constraint to name the mechanism that catches a violation; for this
contract, that mechanism is this file.

**What this is not.** It does not prove a test is hardware-agnostic. It
recognises the *shapes* this repository has actually written — an elapsed-time
assertion, a device spelled inline, two embeddings compared with ``==``, a
numeric client timeout — and refuses them. A test can still depend on hardware
in a way no regex sees (rule 3, "no exact text from a model", is deliberately
absent below for exactly that reason: it is a judgement, not a pattern). The
honest claim is regression prevention, not proof.

The rules, mapped to spec-0029 R2:

===== ============================================================
Rule  Refuses
===== ============================================================
R2.1  an ``assert`` mentioning elapsed time, and a numeric ``timeout=``
R2.2  ``==`` between two ``embed``/``embed_batch`` calls
R2.4  a ``cuda``/``mps``/``cpu`` literal outside ``tests/constants.py``
R2.5  (covered by R2.1: the budget must come from a name)
===== ============================================================

Each rule is proven in both directions: a planted violation must fail it, and
the real tree must pass. The planted half runs the *same* functions the real
half runs, so the two cannot drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.constants import (
    HARDWARE_CONTRACT_SCOPE,
    MIN_HARDWARE_CONTRACT_FILES,
    TORCH_DEVICE_NAMES,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ── The rules ─────────────────────────────────────────────────────────────────
#
# Each is a compiled pattern plus the message a violation prints. Kept as data
# so `_violations` stays one loop and a new rule is one tuple, not a new
# function that someone forgets to call.

# An assertion whose subject is wall-clock time. Matches the names a timing
# assertion is actually written with; a bare `assert x < 5` is not flagged
# (unknowable) — the point is to catch `assert elapsed < 5`, which this repo's
# sibling projects have all written at least once.
_ELAPSED_ASSERT_RE = re.compile(
    r"^\s*assert\b.*\b(elapsed|perf_counter|monotonic|duration_seconds)\b",
    re.MULTILINE,
)

# `timeout=` carrying a numeric literal *anywhere* in its argument expression.
# The budget must come from a name so it can be raised by env for slow hardware
# and can never invert against the adapter's own budget.
#
# The lazy middle and the lookbehind are both load-bearing. A narrower
# `timeout\s*=\s*\d` — which is what this rule was first written as — catches
# `timeout=60.0` but sails straight past `timeout=httpx.Timeout(60.0)`, i.e.
# the identical hardcoded budget wearing a constructor. That is a fail-open in
# the one guard whose entire job is stopping that shape, so the rule now looks
# for a numeral anywhere in the argument.
#
# The lookbehind excludes digits that are part of an identifier, so
# `timeout=budget_2` and `timeout=v1_budget` stay legal while
# `timeout=Timeout(60)` does not. `timeout=None` and any bare name pass.
_NUMERIC_TIMEOUT_RE = re.compile(r"\btimeout\s*=\s*[^,\n]*?(?<![A-Za-z0-9_.])\d")

# Two embedding calls compared for equality. Real backends are float32 and
# reorder reductions across devices, so this is green only on the machine it
# was written on.
_EMBED_EQUALITY_RE = re.compile(r"await\s+\w+\.embed(?:_batch)?\([^)]*\)\s*==")

# A device spelled inline rather than taken from tests/constants.py. Matched as
# a quoted whole word so `# the cpu path` in a comment and `device_cpu` in an
# identifier are both left alone.
_DEVICE_LITERAL_RE = re.compile(
    r"""["'](?:{})["']""".format("|".join(re.escape(name) for name in TORCH_DEVICE_NAMES))
)

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_ELAPSED_ASSERT_RE, "asserts on elapsed time (spec-0029 R2.1)"),
    (_NUMERIC_TIMEOUT_RE, "passes a numeric timeout= instead of a named budget (R2.1)"),
    (_EMBED_EQUALITY_RE, "compares embeddings with == instead of cosine tolerance (R2.2)"),
    (_DEVICE_LITERAL_RE, "spells a torch device inline instead of via tests/constants.py (R2.4)"),
)


def _violations(text: str, where: str) -> list[str]:
    """Return one message per rule violation in *text*, line-numbered."""
    found: list[str] = []
    for pattern, message in _RULES:
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            found.append(f"{where}:{line}: {message}")
    return found


def _scoped_files(root: Path) -> list[Path]:
    """Every file the contract covers, resolved under *root*."""
    found: list[Path] = []
    for pattern in HARDWARE_CONTRACT_SCOPE:
        directory, _, name_glob = pattern.rpartition("/")
        found.extend(sorted((root / directory).glob(name_glob)))
    return found


# ── The real tree must be clean ───────────────────────────────────────────────


def test_scope_matches_enough_files() -> None:
    """A glob matching nothing would make every assertion below vacuous.

    The same fail-open shape ``scripts/lint_agent_frontmatter.py``'s
    ``MIN_AGENT_FILES`` floor exists to stop: a corpus that moved leaves a
    green gate validating an empty set.
    """
    files = _scoped_files(_REPO_ROOT)
    assert len(files) >= MIN_HARDWARE_CONTRACT_FILES, (
        f"HARDWARE_CONTRACT_SCOPE matched only {len(files)} files "
        f"({[f.name for f in files]}) — did a suite move?"
    )


def test_hardware_contract_holds_across_the_scoped_tree() -> None:
    """No scoped file violates a mechanically checkable rule."""
    offences: list[str] = []
    for path in _scoped_files(_REPO_ROOT):
        offences.extend(
            _violations(path.read_text(encoding="utf-8"), str(path.relative_to(_REPO_ROOT)))
        )
    assert offences == [], "hardware-contract violations:\n" + "\n".join(offences)


# ── Each rule must be able to fire ────────────────────────────────────────────
#
# Non-vacuity, per rule. A lint whose regexes silently stopped matching would
# pass the test above forever while checking nothing — the exact failure mode
# spec-0022's gate-integrity work spent two specs hunting after the fact.


@pytest.mark.parametrize(
    ("snippet", "expected_fragment"),
    [
        pytest.param(
            "async def test_x() -> None:\n    assert elapsed < 5\n",
            "elapsed time",
            id="elapsed-assert",
        ),
        pytest.param(
            "resp = await client.post('/x', timeout=60.0)\n",
            "numeric timeout",
            id="numeric-timeout",
        ),
        pytest.param(
            "resp = await client.post('/x', timeout=httpx.Timeout(60.0))\n",
            "numeric timeout",
            id="numeric-timeout-in-constructor",
        ),
        pytest.param(
            "resp = await client.post('/x', timeout=Timeout(60))\n",
            "numeric timeout",
            id="numeric-timeout-bare-constructor",
        ),
        pytest.param(
            "assert await client.embed('a') == await client.embed('a')\n",
            "compares embeddings",
            id="embed-equality",
        ),
        pytest.param(
            "client = Embedder(device='cuda')\n",
            "torch device inline",
            id="device-literal",
        ),
    ],
)
def test_each_rule_flags_its_own_violation(snippet: str, expected_fragment: str) -> None:
    offences = _violations(snippet, "planted.py")
    assert offences, f"rule did not fire on: {snippet!r}"
    assert any(expected_fragment in offence for offence in offences)


def test_a_clean_snippet_is_not_flagged() -> None:
    """The other direction: the shapes the contract *wants* must pass.

    Without this, tightening a regex into something that matches everything
    would still leave every test above green.
    """
    clean = (
        "from tests.constants import DEVICE_CPU, EMBEDDING_COSINE_ATOL\n"
        "async def test_ok(client_timeout: float) -> None:\n"
        "    resp = await client.post('/x', timeout=client_timeout)\n"
        "    other = await client.post('/y', timeout=httpx.Timeout(client_timeout))\n"
        "    third = await client.post('/z', timeout=budget_2)\n"
        "    fourth = await client.post('/w', timeout=None)\n"
        "    vectors = await client.embed_batch(['a'])\n"
        "    expected = pytest.approx(1.0, abs=EMBEDDING_COSINE_ATOL)\n"
        "    assert cosine(vectors[0], vectors[0]) == expected\n"
        "    other = Embedder(device=DEVICE_CPU)\n"
    )
    assert _violations(clean, "clean.py") == []


def test_scope_resolution_is_root_relative(tmp_path: Path) -> None:
    """``_scoped_files`` resolves against the root it is given, not the cwd.

    Pins the property the planted-violation tests would otherwise have to
    trust: the lint can be pointed at a throwaway tree, which is what makes a
    both-directions proof possible at all.
    """
    (tmp_path / "tests" / "lmstudio").mkdir(parents=True)
    (tmp_path / "tests" / "lmstudio" / "test_planted.py").write_text(
        "assert elapsed < 5\n", encoding="utf-8"
    )
    files = _scoped_files(tmp_path)
    assert [f.name for f in files] == ["test_planted.py"]
    offences = _violations(files[0].read_text(encoding="utf-8"), files[0].name)
    assert offences and "elapsed time" in offences[0]
