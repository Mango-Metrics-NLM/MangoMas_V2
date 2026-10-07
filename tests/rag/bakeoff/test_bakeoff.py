"""Docling bake-off entry point — skeleton (spec-0035 R12).

Gated by ``@pytest.mark.docling``: the collection gate in ``tests/conftest.py``
skips it unless ``RUN_DOCLING=1``, so it never runs in the default suite or in
CI (``HOSTED_RUNNER_INFEASIBLE`` records why). Run it with
``RUN_DOCLING=1 make docling-bakeoff``.

This is deliberately a skeleton. The corpus manifest, the human-labelled
questions and the harness that parses, chunks, embeds, scores each arm and
applies ``metrics.decide`` belong to a later milestone whose labels are
written by people who do not tune the chunkers. Until then the test only
proves the gate and fails loudly rather than reporting a vacuous pass.
"""

from __future__ import annotations

import os

import pytest

from tests.constants.live import BAKEOFF_MANIFEST_ENV

pytestmark = pytest.mark.docling


def test_bakeoff_requires_a_corpus_manifest() -> None:
    manifest = os.environ.get(BAKEOFF_MANIFEST_ENV)
    if not manifest:
        pytest.fail(
            f"{BAKEOFF_MANIFEST_ENV} is unset: point it at the bake-off corpus manifest. "
            "The corpus and labels are a pending, human-labelled milestone (spec-0035 R12)."
        )
    pytest.fail(
        f"bake-off harness not implemented yet; manifest {manifest!r} was not read "
        "(pending corpus + labels milestone, spec-0035 R12)."
    )
