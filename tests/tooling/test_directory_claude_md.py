"""Per-directory instruction documents, and the claims that make them checkable.

Five `agent.md` files were deleted from this tree once, for the recorded reason
that "a file nothing loads cannot be kept honest". They held claims that were
**false rather than stale** — a fictional ``TurnRepository.save()``, an SSE
format a client could not parse. Neither is a path, so no amount of path
checking would have caught either.

The entry bar for a file here is therefore not "a directory exists". It is
**one mechanically-checkable semantic claim**, on the model of
``tests/deploy/test_env_example_contract.py`` (documented names and values
against live ``Settings``, both directions):

* ``composition/`` — every provider name it tabulates is really registered.
* ``api/`` — the middleware order it documents is the order ``create_app``
  installs, read from the AST.
* ``adapters/`` — every subpackage it tabulates really declares that
  ``@runtime_checkable`` Protocol in ``base.py``.

What stays unchecked is stated rather than implied: rationale, "why" and
boundary prose are not mechanically verifiable, and that residue is the reason
this is a three-file tranche rather than a corpus.
"""

from __future__ import annotations

import ast
import importlib
import re
from typing import TYPE_CHECKING

import pytest

from mangomas.registry import Registry
from tests.constants.corpus import (
    DIRECTORY_DOC_MAX_LINES,
    DIRECTORY_DOC_MAX_SECTION_LINES,
    DIRECTORY_DOC_PROHIBITED_RESTATEMENTS,
    DIRECTORY_DOC_RELPATHS,
    DIRECTORY_DOC_SECTION_HEADINGS,
    DIRECTORY_DOCS_PREDATING_THE_SECTION_CONTRACT,
    EXPECTED_AGENT_SLUGS,
    EXPECTED_SKILL_SLUGS,
    RETIRED_STRAY_AGENT_FILENAME,
)
from tests.tooling._corpus import REPO_ROOT, iter_backticked_tokens

if TYPE_CHECKING:
    from collections.abc import Iterator

_SECTION_RE = re.compile(r"^(## .*)$", re.MULTILINE)
_MERMAID_RE = re.compile(r"^```mermaid\n(.*?)^```", re.MULTILINE | re.DOTALL)
_TABLE_ROW_RE = re.compile(r"^\|(?!\s*[-: ]+\|)(.+)\|\s*$", re.MULTILINE)
_MANGO_SLUG_RE = re.compile(r"`(mango-[a-z0-9-]+)`")
# Only tokens that look like a path we could resolve. A bare word in backticks
# is prose (`Settings`, `dispatch`), not a claim about the filesystem.
_PATH_TOKEN_RE = re.compile(r"^[\w./-]+\.(?:py|md|json|toml|yml|yaml)$")

_CONTRACT_RELPATHS = tuple(
    rel
    for rel in DIRECTORY_DOC_RELPATHS
    if rel not in DIRECTORY_DOCS_PREDATING_THE_SECTION_CONTRACT
)


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def _sections(text: str) -> list[str]:
    return [line.strip() for line in _SECTION_RE.findall(text)]


def _table_cells(text: str, section: str) -> list[list[str]]:
    """Rows of the markdown table under ``section``, as stripped cell lists."""
    body = _section_body(text, section)
    rows = []
    for raw in _TABLE_ROW_RE.findall(body):
        cells = [cell.strip() for cell in raw.split("|")]
        if cells and not all(set(c) <= {"-", ":", ""} for c in cells):
            rows.append(cells)
    return rows[1:] if rows else []  # drop the header row


def _section_body(text: str, section: str) -> str:
    parts = _SECTION_RE.split(text)
    for index, chunk in enumerate(parts):
        if chunk.strip() == section and index + 1 < len(parts):
            return parts[index + 1]
    return ""


# ── Inventory and shape ───────────────────────────────────────────────────────


def test_directory_doc_inventory_matches_the_tree() -> None:
    """Set equality, both directions, so editing the tuple is the review record.

    A file added without being declared is as much a defect as a declared file
    that vanished: the first is ungoverned, the second is a dangling promise.
    """
    declared = set(DIRECTORY_DOC_RELPATHS)
    found = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in REPO_ROOT.rglob("CLAUDE.md")
        if ".git/" not in path.as_posix()
        and ".venv/" not in path.as_posix()
        and path != REPO_ROOT / "CLAUDE.md"
    }
    assert found == declared, (
        f"undeclared: {sorted(found - declared)}; declared but missing: "
        f"{sorted(declared - found)}. Update DIRECTORY_DOC_RELPATHS."
    )


@pytest.mark.parametrize("relpath", _CONTRACT_RELPATHS, ids=_CONTRACT_RELPATHS)
def test_sections_are_the_canonical_vocabulary_in_order(relpath: str) -> None:
    """Exact sections, exact order — greppable shape beats a house style guide."""
    assert _sections(_read(relpath)) == list(DIRECTORY_DOC_SECTION_HEADINGS), (
        f"{relpath} must carry exactly {list(DIRECTORY_DOC_SECTION_HEADINGS)} in that order"
    )


@pytest.mark.parametrize("relpath", DIRECTORY_DOC_RELPATHS, ids=DIRECTORY_DOC_RELPATHS)
def test_document_fits_its_budget(relpath: str) -> None:
    """Agents act reliably on the first ~150 lines; past that is decoration."""
    text = _read(relpath)
    assert len(text.splitlines()) <= DIRECTORY_DOC_MAX_LINES, (
        f"{relpath} is {len(text.splitlines())} lines (max {DIRECTORY_DOC_MAX_LINES})"
    )
    for section in _sections(text):
        body = len(_section_body(text, section).splitlines())
        assert body <= DIRECTORY_DOC_MAX_SECTION_LINES, (
            f"{relpath} section {section!r} is {body} lines (max {DIRECTORY_DOC_MAX_SECTION_LINES})"
        )


@pytest.mark.parametrize("relpath", DIRECTORY_DOC_RELPATHS, ids=DIRECTORY_DOC_RELPATHS)
def test_no_yaml_frontmatter(relpath: str) -> None:
    """The opposite rule from `.claude/agents` and `.claude/skills`.

    Those require frontmatter and are linted for it; these must not carry any.
    Two corpora, opposite rules — the kind of thing worth a mechanism rather
    than a sentence somebody remembers.
    """
    assert not _read(relpath).startswith("---"), (
        f"{relpath} starts with YAML frontmatter; instruction documents are plain Markdown"
    )


# ── Claims about the tree ─────────────────────────────────────────────────────


@pytest.mark.parametrize("relpath", DIRECTORY_DOC_RELPATHS, ids=DIRECTORY_DOC_RELPATHS)
def test_every_cited_path_exists(relpath: str) -> None:
    """Paths are resolved relative to the repo root and to the doc's directory.

    A directory document legitimately writes both ``adapters/llm/base.py`` and
    the bare ``base.py`` of the package it lives in.
    """
    here = (REPO_ROOT / relpath).parent
    missing = sorted(
        {
            token
            for _, token in iter_backticked_tokens(_read(relpath))
            for token in [token.split("::", 1)[0].strip().rstrip(",.;")]
            if _PATH_TOKEN_RE.match(token)
            # A retired filename is named precisely *because* it must not exist
            # — `core/agent.md` explains why an edit there never needed a
            # trailer. Requiring it to resolve would demand the repo restore
            # the convention it deleted.
            and not token.endswith(RETIRED_STRAY_AGENT_FILENAME)
            and not (REPO_ROOT / token).exists()
            and not (here / token).exists()
            and not any((REPO_ROOT / root / token).exists() for root in ("src/mangomas", "src"))
        }
    )
    assert missing == [], f"{relpath} cites path(s) that do not exist: {missing}"


@pytest.mark.parametrize("relpath", DIRECTORY_DOC_RELPATHS, ids=DIRECTORY_DOC_RELPATHS)
def test_every_cited_symbol_resolves(relpath: str) -> None:
    """``Class.method`` must name a method that class really has.

    This is the check the retired corpus needed and never had.
    ``TurnRepository.save()`` sat in a real file, on a real class, naming a
    method that does not exist — ``save_turn`` does. Path existence returns
    green on that; an AST walk does not.
    """
    offenders = _unresolved_symbols(_read(relpath))
    assert offenders == [], f"{relpath} names member(s) that do not exist: {offenders}"


def _unresolved_symbols(text: str) -> list[str]:
    """Backticked ``Class.method`` tokens naming a member the class lacks."""
    offenders: list[str] = []
    for _, token in iter_backticked_tokens(text):
        match = re.fullmatch(r"([A-Z][A-Za-z0-9_]*)\.([a-z_][A-Za-z0-9_]*)\(?\)?", token.strip())
        if match is None:
            continue
        class_name, attr = match.groups()
        members = _class_members(class_name)
        if members is not None and attr not in members:
            offenders.append(f"{token} (class {class_name} has: {sorted(members)})")
    return offenders


def test_the_symbol_guard_catches_the_defect_that_retired_agent_md() -> None:
    """The guard above runs over documents that (correctly) contain no offender.

    That makes it vacuous in the committed tree: the loop finds no
    ``Class.method`` token and passes without exercising anything. A guard
    nobody has watched fail is not evidence, so this pins the behaviour against
    a fixture instead of against whatever the corpus happens to say today.

    ``TurnRepository.save()`` is the real specimen — a fictional method on a
    real class in a real file, from the ``agent.md`` corpus that was deleted.
    The method is ``save_turn``; ``save`` never existed.
    """
    assert _class_members("TurnRepository"), "TurnRepository not found — fixture is stale"

    offenders = _unresolved_symbols("Callers persist a turn with `TurnRepository.save()`.")
    assert offenders, "the symbol guard did not flag TurnRepository.save()"
    assert "save_turn" in offenders[0], "the failure should name the real method"

    # The other direction: the real name must pass, or the guard is just noisy.
    assert _unresolved_symbols("Callers use `TurnRepository.save_turn()`.") == []


def _class_members(class_name: str) -> set[str] | None:
    """Every attribute of ``class_name``, or None if the class is not ours.

    Returning None for an unknown name keeps the check silent about types from
    third-party libraries, which is honest: we cannot resolve those from here.
    """
    found: set[str] | None = None
    for path in (REPO_ROOT / "src" / "mangomas").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                found = found or set()
                found |= {
                    child.name
                    for child in node.body
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                }
                found |= {
                    target.id
                    for child in node.body
                    if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
                    for target in [child.target]
                }
    return found


@pytest.mark.parametrize("relpath", DIRECTORY_DOC_RELPATHS, ids=DIRECTORY_DOC_RELPATHS)
def test_every_named_agent_and_skill_exists(relpath: str) -> None:
    """A routing table pointing at an agent that was renamed routes nowhere."""
    known = set(EXPECTED_AGENT_SLUGS) | set(EXPECTED_SKILL_SLUGS)
    unknown = sorted(set(_MANGO_SLUG_RE.findall(_read(relpath))) - known)
    assert unknown == [], f"{relpath} names unknown mango-* agent/skill(s): {unknown}"


@pytest.mark.parametrize("relpath", _CONTRACT_RELPATHS, ids=_CONTRACT_RELPATHS)
def test_verify_block_commands_resolve(relpath: str) -> None:
    """The `## Verify` block is authored prose until something runs it.

    "Professional-looking files with wrong commands" is half the anti-pattern
    the retired corpus died of, and it is the surface a reader is most likely
    to paste straight into a shell.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):", makefile, re.MULTILINE))
    offenders: list[str] = []
    for line in _section_body(_read(relpath), "## Verify").splitlines():
        command = line.strip()
        if command.startswith("make "):
            for target in command.removeprefix("make ").split():
                if "=" not in target and target not in targets:
                    offenders.append(f"make {target}")
        elif command.startswith("python -m pytest "):
            for arg in command.split()[3:]:
                if not arg.startswith("-") and not (REPO_ROOT / arg.split("::")[0]).exists():
                    offenders.append(arg)
    assert offenders == [], f"{relpath} `## Verify` names unrunnable command(s): {offenders}"


@pytest.mark.parametrize("relpath", DIRECTORY_DOC_RELPATHS, ids=DIRECTORY_DOC_RELPATHS)
def test_no_mechanised_rule_is_restated(relpath: str) -> None:
    """Link a rule that a gate already enforces; never copy it."""
    text = _read(relpath).lower()
    offenders = [
        f"{phrase!r} — {remedy}"
        for phrase, remedy in DIRECTORY_DOC_PROHIBITED_RESTATEMENTS
        if phrase in text
    ]
    assert offenders == [], f"{relpath} restates a mechanised rule: {offenders}"


# ── Mermaid ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("relpath", _CONTRACT_RELPATHS, ids=_CONTRACT_RELPATHS)
def test_map_diagram_nodes_name_real_modules(relpath: str) -> None:
    """Semantic, not structural.

    `test_architecture_docs.py` already binds the six C4 diagrams to the tree;
    a new diagram under a weaker check ("fence is closed") would be a tenfold
    volume increase at reduced evidence per artefact. Every node label that
    looks like a module must be one, in this directory.
    """
    text = _read(relpath)
    blocks = _MERMAID_RE.findall(text)
    assert len(blocks) == 1, f"{relpath} must carry exactly one mermaid block, found {len(blocks)}"

    here = (REPO_ROOT / relpath).parent
    labels = re.findall(r'[\["]([\w./]+\.py|[\w]+/)["\]]', blocks[0])
    assert labels, f"{relpath} mermaid block names no modules — it depicts nothing checkable"
    # This directory, or one hop out. A local map that may not name its own
    # neighbours is not a map; `composition/` in the adapters diagram is the
    # whole point of drawing one.
    missing = sorted(
        lab
        for lab in set(labels)
        if not (here / lab).exists() and not (REPO_ROOT / "src/mangomas" / lab).exists()
    )
    assert missing == [], f"{relpath} mermaid names module(s) that do not exist: {missing}"


# ── The one semantic claim per document ───────────────────────────────────────


def test_composition_doc_tabulates_real_provider_registrations() -> None:
    """Its `## Invariants` table must match what import actually registers."""
    relpath = "src/mangomas/composition/CLAUDE.md"
    registries_mod = importlib.import_module("mangomas.composition._registries")
    importlib.import_module("mangomas.composition")
    by_kind = {
        obj._kind: obj  # the registry kind is the documented key
        for obj in vars(registries_mod).values()
        if isinstance(obj, Registry)
    }
    rows = [r for r in _table_cells(_read(relpath), "## Invariants") if len(r) >= 3]
    assert rows, f"{relpath} `## Invariants` has no registry table to check"

    offenders: list[str] = []
    for cells in rows:
        kind = cells[1].strip("` ")
        if kind not in by_kind:
            offenders.append(f"unknown registry kind {kind!r} (known: {sorted(by_kind)})")
            continue
        available = set(by_kind[kind].available())
        documented = {n.strip("` ") for n in cells[2].split(",") if n.strip()}
        if not documented <= available:
            offenders.append(f"{kind}: documented {sorted(documented - available)} not registered")
    assert offenders == [], f"{relpath} documents registrations that do not exist: {offenders}"


def _direct_install(node: ast.AST) -> str | None:
    """The class name of an ``app.add_middleware(Name, ...)`` call, else None.

    A call whose first argument is not a bare name (subscripted, keyword-only)
    is not something this check can attribute to a class.
    """
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_middleware"
        and node.args
    ):
        return None
    first = node.args[0]
    return first.id if isinstance(first, ast.Name) else None


def _middleware_install_order(tree: ast.Module) -> list[str]:
    """Middleware classes in the order ``create_app`` actually installs them.

    **Definition order is not execution order**, and reading it as such is how
    this guard first certified a wrong table. ``_install_tenancy`` is *defined*
    at the top of the module, above the direct ``add_middleware`` calls, but
    ``create_app`` *calls* it last — so Tenancy installs outermost while a
    line-sorted walk placed it third. The documented table was wrong and the
    test agreed with it.

    So: walk ``create_app``'s body in source order and step **into** a
    module-level helper at its call site. ``seen`` guards against a recursive
    helper rather than trusting the source not to have one.
    """
    helpers = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    entry = helpers.get("create_app")
    assert entry is not None, "create_app not found in api/app.py"

    def walk(node: ast.AST, seen: frozenset[str]) -> Iterator[str]:
        for child in ast.iter_child_nodes(node):
            direct = _direct_install(child)
            if direct is not None:
                yield direct
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in helpers
                and child.func.id not in seen
            ):
                yield from walk(helpers[child.func.id], seen | {child.func.id})
                continue
            yield from walk(child, seen)

    return list(walk(entry, frozenset()))


def test_api_doc_documents_the_real_middleware_install_order() -> None:
    """Its `## Invariants` table must equal ``create_app``'s AST order."""
    relpath = "src/mangomas/api/CLAUDE.md"
    tree = ast.parse((REPO_ROOT / "src/mangomas/api/app.py").read_text(encoding="utf-8"))
    actual = _middleware_install_order(tree)
    assert actual, "no add_middleware(Name, ...) calls found — the check would be vacuous"

    rows = [r for r in _table_cells(_read(relpath), "## Invariants") if len(r) >= 2]
    documented = [cells[1].strip("` ") for cells in rows]
    assert documented == actual, (
        f"{relpath} documents middleware install order {documented}, but create_app "
        f"installs {actual}. Starlette wraps in reverse, so this order is load-bearing."
    )


def test_adapters_doc_tabulates_real_runtime_checkable_protocols() -> None:
    """Every row must name a Protocol its `base.py` really declares."""
    relpath = "src/mangomas/adapters/CLAUDE.md"
    rows = [r for r in _table_cells(_read(relpath), "## Invariants") if len(r) >= 3]
    assert rows, f"{relpath} `## Invariants` has no protocol table to check"

    offenders: list[str] = []
    for cells in rows:
        sub = cells[1].strip("`/ ")
        base = REPO_ROOT / "src/mangomas/adapters" / sub / "base.py"
        if not base.is_file():
            offenders.append(f"{sub}: no base.py")
            continue
        tree = ast.parse(base.read_text(encoding="utf-8"))
        # Both halves, not just the decorator. `@runtime_checkable` on a class
        # that does not inherit `Protocol` is a TypeError at import, but this
        # check never imports the module — so without the base test a class
        # carrying only the decorator name would satisfy a claim that it is a
        # runtime-checkable Protocol.
        declared = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(
                isinstance(d, ast.Name) and d.id == "runtime_checkable" for d in node.decorator_list
            )
            and any(isinstance(b, ast.Name) and b.id == "Protocol" for b in node.bases)
        }
        for name in (n.strip("` ") for n in cells[2].split(",") if n.strip()):
            if name not in declared:
                offenders.append(f"{sub}: {name} is not a @runtime_checkable Protocol in base.py")
    assert offenders == [], f"{relpath} documents protocols that do not exist: {offenders}"
