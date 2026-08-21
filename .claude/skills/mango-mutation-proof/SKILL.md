---
name: mango-mutation-proof
description: >
  Proving a test or guard actually fails when the thing it guards breaks. Use
  when: adding a regression guard, reviewing a test that "passes" suspiciously
  easily, or closing a coverage gap. Covers the back-up/mutate/assert-failure/
  restore loop, choosing a mutation that discriminates, the two-sided mutation
  for exclusion rules, and the silent-pass failure modes this repo has hit.
argument-hint: "Name the guard to prove, or paste the test you don't trust"
---

# Mango-Mas Mutation-Proof Skill

## When to Use

- You just wrote a regression guard and want it to be worth its line count
- A test passes and you cannot say what would make it fail
- Review turned up a test whose name claims more than its assertions check
- You are closing a coverage gap and want the new test to be load-bearing

## The Problem This Solves

A green test proves nothing on its own. It proves something only if you know it
would go red. This repo has hit the silent-pass failure four separate times:

- **13 of 15 CLI `monkeypatch` sites had no effect.** The tests passed while
  building real orchestrators against `localhost:1234`, because they asserted
  things the real system also produces — `test_agents_command` checked that
  `chat` appeared in the output, and the real registry contains `chat`.
- **A set-equality assertion could not see a reorder.** `--help` order is a user
  contract; the guard compared sets.
- **A `--verbose` test named `*_enables_debug_logging`** asserted only an exit
  code, so deleting the branch it existed to cover changed nothing.
- **A coverage exclusion that over-matched** made the percentage go *up*.

## The Loop

```bash
# 1. Back up what you are about to break (never rely on git alone mid-edit).
cp <target> "$SCRATCH/target.bak"

# 2. Baseline: the test must pass before it can meaningfully fail.
python -m pytest <test> -q --no-cov -p no:cacheprovider

# 3. Mutate — break exactly the property the guard claims to protect.
# 4. Re-run: it MUST fail, and the message must name the real problem.
# 5. Restore, re-run, confirm green.
cp "$SCRATCH/target.bak" <target>
```

Script the whole loop in one `python -` block rather than running the steps by
hand. A restore that does not happen is worse than the mutation, and an
interrupted manual sequence leaves the tree dirty. Print the last line of each
run so the before/after reads as one block.

## Choosing a Mutation That Discriminates

The mutation has to be the *defect*, not merely a change.

| Guard claims | Mutation that proves it | Mutation that proves nothing |
|---|---|---|
| A patch seam reaches every consumer | Retarget the patch at the facade | Delete the test |
| `--help` order is pinned | Reorder two registrations | Rename a command |
| A branch is exercised | Neuter that branch (`if verbose:` → `pass`) | Comment out the assertion |
| A facade re-exports the same object | Rebind the name to a fresh instance | Remove the import |
| A path is CWD-independent | Run from `/tmp` | Change the path string |

If the mutation is "delete the code under test", the guard is probably
asserting existence rather than behaviour.

## Two-Sided Mutations

A rule with a *purpose* needs both directions, or the naive fix passes:

- Coverage exclusion → mutate it to over-match (real code swallowed) **and**
  delete it entirely (Protocol stubs stop being excluded). One test each.
- Config parity → widen one side without the other, **and** re-add the key that
  silently narrows it back.

Two separate tests, not one. If a single mutation fails both, they are
redundant; if each fails exactly one, they are independent — and that is the
property to check after writing them.

## Negative Cases Are Free Proof

Sometimes the mutated behaviour already exists as a legitimate input. Running
the command *without* `--verbose` is exactly what the deleted branch produces,
so a test asserting "no call recorded" proves mutation-sensitivity without ever
editing source. Prefer this when it is available.

## Checklist

- [ ] Baseline green before mutating.
- [ ] The mutation is the defect, not an unrelated break.
- [ ] The failure message names the real problem, not just `assert False`.
- [ ] Two-sided if the rule has a purpose that deleting it would defeat.
- [ ] Restored and re-run green; `git status` clean.
- [ ] Independent guards fail on different mutations, not all on one.

## Pitfalls

- **`--select` on a CLI overrides config precedence.** Proving a ruff `ignore`
  is load-bearing by passing `--select FAMILY` measures the wrong thing —
  toggle the config and run the real lint command instead.
- **A `pragma: no cover` hides the mutation.** If the mutated line is excluded,
  the test cannot see it and the mutation looks like a pass.
- **Scratch files in the repo tree get collected.** A probe dropped in `tests/`
  runs without that directory's autouse fixtures and can reach live services;
  keep probes in the scratchpad, or accept the fixture set you actually get.
- **`rm -rf` is denied by this repo's permission rules.** Use `shutil.rmtree`
  from inside the scripted loop.
