---
name: mango-decompose
description: >
  Splitting an oversized module into a package via the ADR-0019 re-export
  facade pattern. Use when: a module has grown past readability (a "god
  file") and needs decomposing into focused submodules, or when reviewing
  such a split for completeness. Covers the extract/facade/wire/verify
  sequence — group modules, the `__init__.py` facade, wiring the new
  package into `tests/test_import_compat.py`'s facade-identity contract,
  and confirming per-submodule coverage — the step this repo has skipped
  once already.
argument-hint: "Name the module to decompose (e.g. 'src/mangomas/foo.py') or paste the file you're reviewing a split of"
---

# Mango-Mas Decompose Skill

## When to Use

- A module has grown past readability and mixes several distinct
  responsibilities in one file (the classic "god file")
- You are about to copy the `cli/`, `config/`, `telemetry/`, or
  `composition/` precedent for a new target
- You are reviewing a decomposition PR and want to know what "done" means
  beyond "the tests still pass"

## The Problem This Solves

Backward-compatible package decomposition (ADR-0019) has landed **four**
times in this repo — `cli/main.py` → `cli/`, `config.py` → `config/`,
`telemetry.py` → `telemetry/` (all spec-0015), and `composition.py` →
`composition/` (a later, unnumbered instance of the same pattern). Every
one of them needs the identical four steps. The fourth time skipped step 3
— wiring the new facade into `tests/test_import_compat.py` — and a typo'd
`__all__` entry (`_vector_embedding_factory` instead of
`_vertex_embedding_factory`) shipped in the first commit. Nothing caught
it mechanically; it took a manual peer-review pass to find. The facade
contract test existed the whole time — it just didn't know about the new
package.

This skill exists so step 3 is never optional again.

## The Four Steps

### 1. Extract into group modules

Split the god file into focused submodules under a new package directory
(`src/mangomas/<name>/`), one responsibility per file. Keep every function
signature, every log message, and every error message byte-for-byte
identical — a decomposition is a move, not a rewrite. Preserve the
dependency direction: leaf modules (registries, config-only helpers) first,
modules that compose them last. No sibling submodule should import another
sibling except through the leaf layer — verify with:

```bash
grep -rn "^from mangomas\.<name>\." src/mangomas/<name>/*.py
```

### 2. Build the `__init__.py` facade

Every name importable from the old single-file module today must remain
importable from the new package's `__init__.py`, via explicit `X as X`
re-exports (never implicit) — see any existing `composition/__init__.py`
or `config/__init__.py` for the pattern. Sort `__all__` alphabetically
(`ruff`'s `RUF022` enforces this). Import order inside `__init__.py`
matters when a submodule's import has an intentional side effect (e.g.
`composition/agents.py`'s import-time registry seeding) — that import must
come before anything that reads the registry.

**Do not hand-transcribe `__all__`.** Generate the list from the actual
bound names instead of retyping each one — this is exactly where the
`_vector_embedding_factory`/`_vertex_embedding_factory` typo shipped:

```bash
python -c "
import mangomas.<name> as m
for n in sorted(n for n in vars(m) if not n.startswith('__')):
    print(n)
"
```

Then run `ruff check --select F822 src/mangomas/<name>/__init__.py` before
moving on — it catches an `__all__` entry with no matching binding
immediately, for free, and would have caught the exact bug this skill is
named after.

### 3. Wire the new facade into `tests/test_import_compat.py`

**This is the step that goes missing.** `tests/test_import_compat.py`
already contains a generalized facade-identity contract
(`_FACADES`, `_PRIVATE_FACADE_CONTRACT`) proven against `cli`, `telemetry`,
`config`, and now `composition`. Adding a fifth package means:

1. Add `"mangomas.<name>": (<submodule names>,)` to `_FACADES`.
2. If any re-exported name is underscore-prefixed (a "private but
   documented" factory or helper, tests reach it directly) — add it to
   `_PRIVATE_FACADE_CONTRACT["mangomas.<name>"]` mapping name → owning
   submodule. The `_owned_names()` helper filters underscore-prefixed
   names as "not public", so without this step almost an entire
   factory-heavy facade (like `composition/`'s) is invisible to every
   other check in the file.
3. Add the three parametrized tests every existing facade has, adjusted
   to the new package name: `test_<name>_facade_name_is_importable`
   (parametrized over `_public_names(facade)` — i.e. iterates `__all__`
   itself, which is the one that catches a stray typo), a `..._reexports_are_identical_objects`
   test (parametrized over `_FACADES["mangomas.<name>"]`), and
   `test_every_owned_public_name_reaches_the_<name>_facade`.

**Prove it's load-bearing** (per `mango-mutation-proof`): reintroduce a
one-character typo in `__all__`, confirm
`test_<name>_facade_name_is_importable` fails with a message naming the
exact bad entry, then revert.

### 4. Verify per-submodule coverage, not just the package aggregate

A package can clear its 95% floor while individual submodules sit at
60-70% — this happened to `composition/embeddings.py` (67%) and
`composition/vector.py` (70%) because their SDK-backed factories only had
registry-membership tests, never kwarg-forwarding tests, while the
lmstudio-backed sibling in the same file did. Check every file
individually:

```bash
python -m pytest tests/test_<name>.py --cov=src/mangomas/<name> --cov-report=term-missing -q
```

For a factory that lazy-imports an optional SDK inside its function body,
test kwarg-forwarding by monkeypatching the SDK class at its import
source — not by calling the factory unpatched (which either needs the
real SDK installed or raises `ImportError`):

```python
def test_<provider>_factory_forwards_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("mangomas.adapters.<layer>.<provider>.<ClientClass>", _Recorder)
    cfg = <Settings>(...)
    _<provider>_factory(cfg)
    assert captured == {...}
```

## Checklist

- [ ] Every function signature, log message, and error message preserved
      byte-for-byte from the original file
- [ ] No sibling submodule imports another sibling except through the leaf
      (registry/config) layer
- [ ] `__init__.py`'s `__all__` generated from actual bound names, not
      hand-retyped, and alphabetically sorted
- [ ] `ruff check --select F822` run against the new `__init__.py` before
      moving on
- [ ] New package added to `tests/test_import_compat.py`'s `_FACADES`
- [ ] Every underscore-prefixed re-export added to
      `_PRIVATE_FACADE_CONTRACT`
- [ ] The three parametrized facade tests added and passing
- [ ] Facade-typo mutation proof run (per `mango-mutation-proof`): a
      one-character `__all__` typo makes `test_<name>_facade_name_is_importable`
      fail with a message naming the bad entry, then reverted
- [ ] `python scripts/check_coverage.py` — floor glob updated if the old
      module was a single `.py` file (`src/mangomas/<name>.py` →
      `src/mangomas/<name>/**/*.py`)
- [ ] Per-submodule coverage checked individually, not just the package
      aggregate — every SDK-backed factory has a kwarg-forwarding test,
      not just a registry-membership check
- [ ] `make gate` green: lint, format-check, typecheck, frontmatter,
      protected-paths, test, coverage
- [ ] CHANGELOG entry cites the correct governing spec — check
      `specs/0015-package-decomposition.md`'s own requirement list before
      claiming an "R1"/"R2"/etc. slot; a decomposition outside its four
      listed targets (`cli`, `config`, `telemetry`, `core/structured.py`)
      is a new instance of the ADR-0019 pattern, not a spec-0015 item

## Pitfalls

- **Hand-transcribing `__all__` from memory or from the old file's
  implicit export surface.** The old single-file module had no `__all__`
  at all in at least one precedent (`composition.py`) — every module-level
  name was importable regardless of underscore prefix. Generate the new
  `__all__` programmatically (see Step 2) rather than trying to remember
  every name.
- **Testing a lazy-SDK-import factory by calling it directly.** It either
  needs the real optional extra installed or raises `ImportError` — always
  monkeypatch the SDK class at its import source instead.
- **A dynamic `import mangomas.<name> as x` inside a sibling submodule's
  function body**, done only so a test can monkeypatch the facade name —
  check first whether the function being patched is defined in the *same
  file* as the caller. If so, call it directly and repoint the test's
  `monkeypatch.setattr` at the defining module
  (`mangomas.<name>.<submodule>.<fn>`) instead of the facade re-export —
  this is strictly simpler and was a false save-for-later in the
  `composition/secrets.py` precedent.
- **Manual registry `._store` manipulation without a matching restore.**
  If a test needs a registry entry absent for the duration, save what was
  there before, wrap the body in `try/finally`, and restore it —
  `Registry.scoped()` only supports temporarily *setting* a value, not
  temporarily clearing one.
- **A stale prose count.** `tests/tooling/test_corpus_contract.py`'s
  `test_prose_corpus_counts_match_the_live_corpus` cross-checks any "N
  skills"/"N agents" claim in the docs against the live corpus — adding a
  skill or agent as a side effect of this work means grepping `README.md`
  and `CLAUDE.md` for the old count.
