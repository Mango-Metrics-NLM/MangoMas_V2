---
name: mango-coverage-audit
description: >
  Auditing whether a coverage number is measured over the right denominator.
  Use when: a file reports high coverage but a branch is obviously untested,
  adding or changing a floor glob or an exclude_lines pattern, or a percentage
  rises after a refactor that added no tests. Covers the parser-vs-report
  comparison, the fail-open shapes this repo has hit, and the guards that pin
  them.
argument-hint: "Name a suspicious module, or say 'audit the whole gate'"
---

# Mango-Mas Coverage-Audit Skill

## When to Use

- A file reports 100% and you can point at a line no test executes
- You are adding or editing a floor glob in `scripts/check_coverage.py`
- You are touching `exclude_lines` in `pyproject.toml`
- Coverage went *up* after a change that added no tests
- A new package was added and you want to know it is actually measured

## The Failure Mode

Coverage gates fail open. A glob matching nothing still prints a percentage and
still passes; an exclusion that over-matches quietly shrinks the denominator.
Neither reports anything. Nothing about a green gate distinguishes "we checked
everything and it passed" from "we checked less than you think and it passed."

**The exclusion case is the nastiest, because the symptom looks like success.**
The lines an over-matching pattern swallows are disproportionately the untested
ones, so the percentage *rises*. Nobody investigates a number going up.

Four instances in this repo, all real:

| Shape | Instance |
|---|---|
| Glob stops matching | `api/*.py` stayed flat when `api/` grew `routes/` |
| Glob never recursed | `cli` floor, ahead of the `cli/main.py` split |
| Exclusion over-matches | unanchored `"\.\.\."` matched `typer.Argument(...)` |
| Floor never written | a new package inherits only the 95% global average |

## The Core Technique

Coverage's own parser knows what the file contains. The report knows what it
counted. **Divergence is the bug.**

```python
from coverage.parser import PythonParser
from pathlib import Path

src = Path("src/mangomas/cli/commands/rag.py").read_text(encoding="utf-8")
parser = PythonParser(text=src, filename="rag.py")
parser.parse_source()
print(len(parser.statements), max(parser.statements))   # 53, line 109
```

Then compare against the report for the same file. `rag.py` reported **16**
statements where the parser saw **53** — everything from the first
`@rag_app.command` decorator down was invisible, because a `typer.Argument(...)`
in the signature matched the exclusion and coverage drops the whole block when
the excluded line belongs to a `def` header.

A cheaper smell test first: **statements vs file length**. 16 statements in a
109-line module is an outlier; compare siblings before reaching for the parser.

## Isolating an Exclusion

Re-run the identical suite with only the suspect pattern changed, into a
scratch data file so the main `.coverage` is untouched:

```bash
COVERAGE_FILE="$SCRATCH/probe.cov" python -m coverage run --branch \
  --source=mangomas -m pytest -q -o addopts="" -p no:cacheprovider
COVERAGE_FILE="$SCRATCH/probe.cov" python -m coverage report --rcfile="$SCRATCH/probe.rc"
```

Lines that appear as *missing* only with the pattern removed are the ones it was
hiding. On the real instance those were exactly the two `--verbose` branches no
test invoked — corroborating a count reached independently.

## Anchoring an Exclusion Correctly

An ellipsis pattern must match a stub body, not a literal `...` argument.
`src/` uses both stub forms, so both need a pattern:

```toml
"^\\s*\\.\\.\\.$",   # 30 sites: ellipsis alone on its line
": \\.\\.\\.$",      # 3 sites:  def get(self, name: str) -> str | None: ...
```

Anchoring to bare lines alone broke the `secrets` package's 100% floor on the
inline form. **Always run the per-package floors, not just the global** — the
global is an average and hides a single small module.

## Checklist

- [ ] Parser statement count matches the report's, for the suspect file.
- [ ] Every floor glob is recursive (`**/*.py`) and its target exists.
- [ ] Every top-level path under `src/mangomas/` has a floor.
- [ ] No exclusion pattern matches real executable source.
- [ ] Stub bodies of *both* forms still match some pattern.
- [ ] `python scripts/check_coverage.py` — per-package, not just global.
- [ ] Any new guard is mutation-proven (see the `mango-mutation-proof` skill).

## Where the Guards Live

`tests/test_check_coverage.py` owns this invariant class and says so in its
docstring. Recursive globs, target existence, floor completeness and
exclusion-pattern sanity all live there. **Add the next one there too** — the
point of a named class with one home is that the fifth instance does not get
rediscovered from scratch.

The exclusion guard is deliberately *semantic*: it matches the configured
patterns against real source lines rather than asserting their shape. A shape
check (`assert "\.\.\." not in patterns`) passes for any differently-worded
regex with the same defect.
