# Spec-0054: Architecture-proposal pipeline (the propose stage)

- **Status:** Draft
- **Linked ADR:** [ADR-0042](../docs/adr/0042-governed-self-improvement-loop.md) (Proposed)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Governed self-improvement loop: decision records (ADR-0042 / spec-0052..0055)`
- **Delivery plan:** [milestone M3](../docs/plans/20261123T000000Z-governed-self-improvement-loop-plan.md)

## Problem

Every loop item needs approved acceptance before anyone implements it, and
every architecture request in this repo already follows an informal path:
gather evidence, check existing decisions, design, critique, decide. Today
that path depends on whoever runs it, its evidence is not checked, and
reviewer findings can be summarised away. This spec makes it repeatable,
citation-checked and measurable, and ends it where the authority sits: human
approval.

## Pipeline

```
Architecture request
       |
       v
Intake
       |
       v
Evidence retrieval
       |
       +------------------------------+
       |              |               |
  Requirements     Existing ADRs   Architecture
     Agent            Agent        Evidence Agent
       |              |               |
       +--------------+---------------+
                      |
                 Design Agent
                      |
             +--------+--------+
             |                 |
        Critic Agent      Security Agent
             |                 |
             +--------+--------+
                      |
                Evidence-backed
                   proposal
                      |
               HUMAN APPROVAL
```

## Requirements

- **R1 Roles.** The analyst, critic and security roles are read-only: no file
  or GitHub write tools, MCP servers included.

  | Node | Output | Reuses |
  |---|---|---|
  | Intake | Request record (R2) | the ADR trigger rule in `specs/README.md` |
  | Evidence retrieval | Evidence bundle (R3) | the repo's own indexes: `specs/README.md`, `docs/adr/`, the design-rule table in `AGENTS.md`, the governance table in `pyproject.toml` |
  | Requirements agent | Requirements, WHEN/THEN scenarios in both directions, acceptance criteria | `specs/TEMPLATE.md` |
  | Existing ADRs agent | Constraining decisions, conflicts, supersession candidates, next free numbers, surface owners | `docs/adr/`, the owner tables in `CLAUDE.md` |
  | Architecture evidence agent | Code facts at the pinned SHA; layering, protocol, protected-path and trusted-base impact | `mango-layering-auditor`, `mango-protocol-auditor` |
  | Design agent | Options, recommendation, spec draft, ADR draft, backlog-item draft, contract tests | a planning subagent; `mango-adr-author` for the ADR file |
  | Critic agent | Findings against project principles | `mango-architect` |
  | Security agent | Findings on trust boundaries, untrusted inputs, secrets, new hosts, tool effects (ADR-0039), new dependencies (ADR-0041) | the `security-review` skill; `mango-sandbox`, `mango-harness` |
  | Evidence-backed proposal | The assembled package (R4, R5) | code, not a model |
  | Human approval | Merge, one send-back, or rejection | the maintainer |

- **R2 Request record.** `request_id`, problem, constraints, non-goals,
  requester, links, and `needs_adr` (true when the change introduces or moves a
  boundary).
- **R3 Evidence bundle.** A pinned commit SHA and items
  `{id, claim, kind, locator, quote}`, where `kind` is `repo`, `adr`, `spec`,
  `test` or `external`, and a repo locator is `path:start-end@sha`. Untrusted
  text (issues, comments, web pages) is quoted as data with its origin and is
  never followed as an instruction (reliability plan E0).
- **R4 Proposal package** on a `claude/proposal-<request_id>` branch: spec
  draft; ADR draft at Proposed when `needs_adr`; backlog-item draft (spec-0053);
  contract tests under `tests/contract/<id_snake>/` (spec-0053 R5); and
  `docs/proposals/<request_id>/` holding `evidence.json`, `findings.json` and
  `proposal.md`. Contract tests may appear in a proposal only once spec-0053
  R5's isolation and explicit runner exist (M2), so a contract-bearing proposal
  can never turn `dev` red; its CI must show the tests collected and failing
  before implementation.
- **R5 Assembly by code.** `proposal.md` is generated from every node's
  output: the design, the critic's and security agent's findings verbatim with
  severity, the evidence table and open questions. No model writes or edits
  the findings section.
- **R6 Citation checker.** `scripts/check_proposal_evidence.py`, run in CI on
  proposal branches: every repo locator resolves at the pinned SHA and its
  quote matches after whitespace normalisation; every requirement maps to at
  least one acceptance criterion; the package's paths fit the proposal branch
  class (spec-0052); trusted-base and protected-path impact is computed, not
  claimed. External URLs are listed for sampling, not fetched. It also
  re-assembles `proposal.md` from the JSON files and fails on any difference,
  so no model can edit the findings after assembly. The checker confirms that
  quotes exist; whether a quote supports its claim is judged by the maintainer
  and measured by the sampled unsupported-claim rate.
- **R7 One pass.** No automatic revise-until-approved loop. The maintainer may
  send a proposal back once with comments; the re-run is a new pass that takes
  the comments as input.
- **R8 Approval.** The maintainer approves by merging the proposal PR.
  Send-backs and rejections are recorded too (spec-0055). Moving an ADR from
  Proposed to Accepted is a human edit: proposal branches cannot modify an
  existing record (spec-0052 R5).
- **R9 Substrate.** A saved Claude Code workflow (the Workflow tool loads
  named workflows from `.claude/workflows/`) at
  `.claude/workflows/architecture-proposal.*`, inside the trusted base. Not a
  skill: a skill never delegates to an agent (`CLAUDE.md:68`).
- **R10 Replay evaluation.** A maintainer-curated set of past ADRs, preferring
  those dated after the model's training cutoff, each paired with the commit
  before it landed. The pipeline runs on a history-free export of that commit,
  with no network access to the repository host and the `github` and `fetch`
  MCP servers disabled (`.mcp.json` ships both), because this repository is
  public and the recorded decision is otherwise one request away. Its output is
  compared with the recorded Decision and Alternatives. A held-out subset is never used
  to tune role briefs.

## Metrics

Agreement with recorded decisions; unsupported-claim rate on a sampled audit;
finding precision as labelled by the maintainer; issues found later that the
pipeline missed; approval, send-back and rejection rates; cost and time per
proposal.

## Scenarios (WHEN/THEN)

- WHEN a citation's quote does not match the file at the pinned SHA THEN the
  checker fails and the proposal is not presented; WHEN every citation matches
  THEN it passes.
- WHEN a requirement has no acceptance criterion THEN the checker fails.
- WHEN a proposal branch touches `src/` THEN the scope check fails
  (spec-0052).
- WHEN the critic reports a blocking finding THEN it appears verbatim in
  `proposal.md` and the proposal still reaches the maintainer: findings inform,
  they do not gate.
- WHEN `proposal.md` differs from a re-assembly of its JSON files THEN the
  checker fails.
- WHEN a replay snapshot contains `.git` or files from after the decision, or
  the replay environment can reach the repository host or has the `github` or
  `fetch` MCP server enabled, THEN the replay harness refuses to run.

## Config / env additions

None in `Settings`.

## Protocol / contract impact

- New/changed protocols: none in `src/`.
- New error types: none.
- Registry additions: none.
- New file formats: request record, evidence bundle, findings.

## Backwards-compatibility

- Additive. Specs and ADRs written by hand stay valid and need not pass
  through the pipeline.

## Product track P1: MangoMas port

The graph is expressible today as `sequence` plus `fan_out`. A faithful port
first needs evidence that reaches every node (`sequence` passes only the
previous step's output, `src/mangomas/workflow/nodes/sequence.py:39-42`), role
agents as `mangomas.agents` plugins (`src/mangomas/agents/discovery.py:46`),
and line-anchored retrieval (RAG chunks carry no line numbers,
`src/mangomas/rag/models.py:23-27`). The port is scored against this version
on the replay set.

## Test plan

- Unit: `tests/test_check_proposal_evidence.py` and the assembler's tests, both
  directions for every scenario, with fixture repositories and no network,
  registered in `SCRIPTS_TESTS`.
- Corpus: a contract that the analyst, critic and security roles name only
  read-only agents.
- Replay: the snapshot refusal in R10.

## Acceptance criteria

- [ ] Every scenario is a test, in both directions.
- [ ] One real proposal produced end to end and approved or rejected by the
      maintainer.
- [ ] The replay set exists with a held-out subset, and the metrics are
      reported for it.
- [ ] CHANGELOG updated.
