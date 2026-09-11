"""Contract tests binding `docs/architecture/` to the tree it describes.

A C4 model is a claim about the system's structure, and it is the claim most
likely to rot: nothing imports it, nothing runs it, and it is written once per
subsystem rather than once per change. This branch found it had drifted in
exactly that way — `tenancy.py`, the eval registries, the harness governance
package and the MeterProvider were all shipping, all documented elsewhere, and
absent from every diagram; C1 still listed Cloud Run and Cloud Trace as
"future" a release after both landed.

These tests do not check that the prose is *good*. They check the two things a
reader relies on and can be verified mechanically:

* every top-level package under `src/mangomas/` appears somewhere in the
  model, so a whole subsystem cannot be invisible;
* every Mermaid diagram is internally consistent — no relationship names an
  element that was never declared, which renders as a silently missing arrow
  rather than an error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARCH_DIR = _REPO_ROOT / "docs" / "architecture"
_SRC_ROOT = _REPO_ROOT / "src" / "mangomas"

# The C4 levels that must between them account for the whole package tree.
# `cloud-providers.md` and `observability.md` are topic notes, not levels, so
# they are checked for diagram consistency but not for coverage.
_MODEL_DOCS: tuple[str, ...] = ("c1-context.md", "c2-container.md", "c3-component.md")

# `Component(id, …)` / `Container(id, …)` / `System_Ext(id, …)` / `Person(id, …)`
_ELEMENT_RE = re.compile(
    r"\b(?:Component|Container|System|System_Ext|Person|Person_Ext|ContainerDb|SystemDb)"
    r"(?:_Ext)?\(\s*(\w+)\s*,"
)
_BOUNDARY_RE = re.compile(r"\b(?:Container_Boundary|System_Boundary|Boundary)\(\s*(\w+)\s*,")
_REL_RE = re.compile(r"\bRel(?:_Back|_U|_D|_L|_R)?\(\s*(\w+)\s*,\s*(\w+)\s*,")

# Modules whose *name* need not appear verbatim in a diagram, with the reason.
# Each is either a shared helper with no architectural boundary of its own, or
# a surface the model describes under a different name — never a subsystem.
_COVERAGE_EXEMPT: dict[str, str] = {
    "__init__.py": "package marker",
    "_entry_points.py": "shared entry-point iteration helper; no boundary of its own",
    "_headers.py": "shared header sanitisation; described inside the middleware components",
    "correlation.py": "appears as the `correlation` component in C3",
    "errors.py": "the error taxonomy is described by the error_handler component",
    "metrics.py": "described by the C3 `meters` component (ADR-0013)",
    "registry.py": "appears as the `Registry[T]` component in C3",
    "config": "settings are described per-boundary as the MANGOMAS_* vars each reads",
}


def _doc_paths() -> list[Path]:
    return sorted(_ARCH_DIR.glob("*.md"))


def _mermaid_blocks(text: str) -> list[str]:
    return re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL)


def _top_level_entries() -> list[str]:
    return sorted(p.name for p in _SRC_ROOT.iterdir() if p.name != "__pycache__")


def test_architecture_docs_exist() -> None:
    """Non-vacuity: the parametrised tests below are only meaningful if the
    directory actually holds the model."""
    names = {p.name for p in _doc_paths()}
    missing = sorted(set(_MODEL_DOCS) - names)
    assert missing == [], f"missing C4 level(s): {missing}"


@pytest.mark.parametrize("path", _doc_paths(), ids=lambda p: p.name)
def test_every_diagram_relationship_names_a_declared_element(path: Path) -> None:
    """A `Rel(a, b, …)` naming an undeclared id renders as a missing arrow.

    Mermaid does not error on it — the diagram simply comes out wrong, and a
    reader has no way to tell. This is the failure mode a renamed component
    produces, so it is worth catching at the same moment as the rename.
    """
    blocks = _mermaid_blocks(path.read_text(encoding="utf-8"))
    # A topic note (`observability.md`, `cloud-providers.md`) may carry no
    # diagram at all; that is not a failure and not a skip either — there is
    # simply nothing to check, and an empty loop says so honestly.
    for block in blocks:
        declared = set(_ELEMENT_RE.findall(block)) | set(_BOUNDARY_RE.findall(block))
        referenced = {end for rel in _REL_RE.findall(block) for end in rel}
        undeclared = sorted(referenced - declared)
        assert undeclared == [], f"{path.name}: Rel() names undeclared element(s): {undeclared}"


def test_every_source_package_appears_in_the_model() -> None:
    """No top-level subsystem may be missing from every C4 level.

    Four were: `tenancy.py` (ADR-0017), the `eval/` registries, `harness/`
    governance, and the MeterProvider in `telemetry/`. Each shipped, each was
    documented in `CLAUDE.md` and its own ADR, and none appeared in a diagram —
    so the architecture docs described a system four subsystems smaller than
    the one in the repository.

    Matched on the name, not the prose: this asserts the subsystem is *named*
    somewhere in the model, which is the weakest claim worth making
    mechanically. Whether it is described *well* stays a review question.
    """
    # Diagram blocks only, deliberately. Scanning whole files passes on a
    # subsystem that a prose note happens to mention while no box exists for
    # it — verified: deleting the `tenancy_mw` component still left the word
    # "multi-tenancy" in C1's notes, and a whole-file scan stayed green. The
    # claim worth checking is that the *model* names it.
    model_text = "\n".join(
        block
        for name in _MODEL_DOCS
        for block in _mermaid_blocks((_ARCH_DIR / name).read_text(encoding="utf-8"))
    ).lower()

    entries = _top_level_entries()
    assert len(entries) > 10, f"only {len(entries)} entries under {_SRC_ROOT} — layout moved?"

    missing = [
        entry
        for entry in entries
        if entry not in _COVERAGE_EXEMPT and entry.removesuffix(".py").lower() not in model_text
    ]
    assert missing == [], (
        f"subsystem(s) absent from every C4 level: {missing}. Add each to the level "
        "it belongs to, or to _COVERAGE_EXEMPT with why it has no boundary of its own."
    )

    stale = sorted(set(_COVERAGE_EXEMPT) - set(entries))
    assert stale == [], f"_COVERAGE_EXEMPT names entries that no longer exist: {stale}"


def test_model_does_not_describe_landed_work_as_future() -> None:
    """C1 listed Cloud Run and Cloud Trace as pending a release after both shipped.

    A diagram that calls shipped infrastructure "planned" is worse than one
    that omits it: a reader plans work that is already done. Checked against
    the artefacts that prove the landing rather than a hand-maintained list.
    """
    landed = {
        "Cloud Run": _REPO_ROOT / "deploy" / "service.yaml",
        "Cloud Trace": _REPO_ROOT / "src" / "mangomas" / "telemetry" / "exporters.py",
    }
    text = (_ARCH_DIR / "c1-context.md").read_text(encoding="utf-8")
    future_lines = [
        line
        for line in text.splitlines()
        if re.search(r"\b(?:future|planned|pending|still to come)\b", line, re.IGNORECASE)
    ]
    for label, artefact in landed.items():
        assert artefact.exists(), f"{artefact} is gone — this guard's premise no longer holds"
        offending = [line.strip()[:100] for line in future_lines if label in line]
        assert offending == [], (
            f"{label} has shipped ({artefact.name}) but C1 still says: {offending}"
        )


def test_c4_code_names_the_dispatch_surface() -> None:
    """C4 is a dispatch-surface doc; these names are the ones that rot first.

    ``_MODEL_DOCS`` stays c1-c3 because C4 uses classDiagram/flowchart, not
    C4 Rel(). This assertion is the mechanical claim worth making at level 4:
    the topology methods, the composition root package, the middleware
    package, and UnknownProvider stay named.
    """
    text = (_ARCH_DIR / "c4-code.md").read_text(encoding="utf-8")
    required = (
        "dispatch_fan_out_settled",
        "FanOutOutcome",
        "UnknownProvider",
        "composition/",
        "api/middleware/",
    )
    missing = [name for name in required if name not in text]
    assert missing == [], f"c4-code.md no longer names {missing}"
