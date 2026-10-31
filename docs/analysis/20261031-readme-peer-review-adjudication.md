# README peer review — adjudication

- **Date:** 2026-10-31
- **Scope:** an external review of the root `README.md` ("Analysis: MangoMas V2
  README"), received as pasted text. The subject is `README.md` at `2a6d6dc`;
  line numbers below refer to that revision.
- **Method:** every claim was checked against the tree: README, `pyproject.toml`,
  `.env.example`, `src/mangomas/{config,composition,agents,api,cli,adapters}`,
  `tests/integration/`, `.github/workflows/ci.yml` and earlier `docs/analysis/`
  records. The CLI failure modes were reproduced against an editable install
  (bad setting, RAG disabled, unreachable LM Studio).
- **Verdict:** **approve-with-corrections.** The onboarding diagnosis is right.
  Three factual claims are wrong, one premise (that the repository is public) is
  wrong, and the review misses the README's most serious defects. Those are
  *inaccuracies* — statements the code contradicts — rather than omissions.

Tags follow the 2026-09-12 council rewrite: **[Keep]** holds as stated,
**[Qualify]** holds in part, **[Reject]** does not hold.

---

## 1. Claim-by-claim

| Review claim | Tag | Evidence |
|---|---|---|
| Positioning, protocol seams, opt-in discipline, back-compat aliases | Keep | README L3-5, L48-102, L110-145 |
| Quick start is runnable on both shells | **Qualify** | The bash block (L38-44) dropped the LM Studio step, the model-id edit and the CLI line. `.env.example` ships `MANGOMAS_LLM__MODEL=local-model`, so a bash user's first call fails |
| "882 tests at ~98% coverage" | **Reject** | The figure appears nowhere in the tree. README L448 defers to live numbers on purpose; the last recorded run was 3029 passed (`20260919-agents-md-corpus-peer-review.md`) |
| "400 commits" | Reject | 307 commits reachable from `2a6d6dc` |
| No prerequisites | Keep | `requires-python = ">=3.11"`, CI matrix 3.11/3.12 (`ci.yml:57-58`), Docker `python:3.11-slim` — none stated in the README |
| Agents are a black box | Keep | Named only in the layout tree (L495) — and named wrongly: the tool agent registers as `tool`, not `tool_agent` (`composition/agents.py:46`, `ToolAgent.name`) |
| License tension on a public repository | **Reject (premise)** | The repository is private (`20260912-council-peer-review-rewrite.md` §2, §10 "no public-ing"). `CONTRIBUTING.md` addresses internal contributors and admins. "Proprietary" with no `LICENSE` file is coherent for a private repository |
| The harness section is "the longest section" | **Qualify** | False as stated: L339-378 is about 40 lines; Quality gates (about 85) and Project layout (about 75) are longer. The audience point holds for the `.claude/` surface table, not for the `MANGOMAS_HARNESS__*` block, which configures the runtime |
| No troubleshooting | Keep | No such section, although each failure mode is well defined in code (§3) |
| No badges or sample output | Qualify | A sample session: Keep. Badges: a CI badge is cheap; a coverage badge needs Codecov token setup for a private repository and is not worth it |
| Wrap artifacts ("bac kwards", "HEALTHCHECK t hat", a stray space) | **Reject** | The source text is clean (L111, L392-393, L74). These came from whatever rendered the page for the reviewer. The proposed pre-commit wrap check has no defect to catch |

## 2. Defects the review missed

Ranked by how much harm a reader takes from them.

1. **Workflow "default-OFF" was overstated** (L258-259, L284-285). `POST
   /workflows/run` runs a caller-supplied inline graph even when
   `MANGOMAS_WORKFLOW__ENABLED=false`, because
   `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION` defaults to `true`
   (`config/workflow.py`, `workflow/loader.py::resolve_workflow_source`,
   ADR-0033). The variable was missing from the README. This matters for
   security: an operator reading "default-OFF" would not close the route.
2. **The Vertex table contradicted the code** (L91-97). `MODEL` was marked
   required with no default; it defaults to `local-model` and is never
   required. `PROJECT_ID` was marked required; nothing validates it, and `None`
   goes straight to `vertexai.init`, which falls back to the ADC project.
3. **The SSE envelope was incomplete** (L134-145). The stream sends a
   `metadata` frame — `{"agent", "degraded", "chunks"}` — before `done`
   (`api/routes/agents.py`), and an error after the first byte ends the stream
   with no `done` frame. Neither was documented, so a client could not detect a
   degraded or failed stream.
4. **The harness section conflated two things.** `MANGOMAS_HARNESS__ENABLED`
   controls runtime span tracing. The `.claude/` agent and skill corpus is
   contributor tooling, always present, with no runtime effect. The section
   presented both as one opt-in feature.
5. **The bash quick start was incomplete** (§1).
6. **Agent name drift:** `tool_agent` in the layout tree; the registered name is `tool`.
7. **The identity paragraph was misplaced** (L303-312) inside "Cognitive
   signals". The 2026-09-12 rewrite asked for disambiguation; it belongs at the top.
8. **Adjacent stale docs.** `AGENTS.md` said the integration tests require LM
   Studio; they are hermetic ASGI tests that CI runs on every push
   (`tests/integration/conftest.py`). `CONTRIBUTING.md` said "Six files are
   protected paths" and omitted the governance surface ADR-0030 added.
9. **Stale Vertex model id.** `gemini-1.5-flash` appears in the README,
   `.env.example`, `tests/constants/live.py` and five docs. It needs a current
   id confirmed against Vertex before a repo-wide change — not done here.

## 3. Code gaps this review surfaced

These are code defects, not documentation. The README now documents the
current behaviour honestly; each is a follow-up.

- **Fixed (same PR).** `mangomas chat` with LM Studio unreachable printed an
  `LLMUnavailable` traceback and exited 1. Only `workflow run` and `eval`
  caught `MangomasError`. Every command now runs through
  `cli/_runtime.py::run_or_exit` and prints `<Command> failed: <message>
  (<detail>)` with exit 1.
- **Fixed (same PR).** A missing optional extra (`vertex`, `rag`,
  `embeddings-local`) raised a plain `ImportError` from an adapter constructor,
  inside `_build()`, so the CLI showed a traceback. `build_or_exit` now maps it
  to `Missing optional dependency: <install hint>` with exit 2. The API still
  fails at startup with the `ImportError`.
- A wrong model id is invisible to `/readyz`, whose LM Studio ping is `GET
  /models`. It surfaces as `502 llm_bad_response` on the first call.
- `MANGOMAS_LLM__PROJECT_ID` is not validated for `PROVIDER=vertex`, unlike the
  GCP secrets provider's `project_id`.

## 4. What changed, and what did not

Changed in the same commit as this record:

- `README.md`: CI badge; a private-repository line next to the license;
  "What this is not" moved to the top; Prerequisites; bash quick start at
  parity; a sample session; Vertex table corrected; SSE `metadata` frame and
  stream-failure behaviour; Built-in agents table; workflow
  `ALLOW_INLINE_DEFINITION` row and warning; the harness section split into
  runtime tracing and contributor tooling; Troubleshooting; nightly,
  dependabot and PR-template automation moved under Quality gates; layout
  names `tool`.
- `AGENTS.md`: integration-test comment corrected.
- `CONTRIBUTING.md`: protected-path intro names the ADR-0030 governance surface.

Also changed in a follow-up commit: the two CLI gaps in §3 (`cli/_runtime.py`
and the command modules), and two README Troubleshooting corrections from
review (the error-envelope scope versus FastAPI's 422, and `504 step_timeout`).

Not changed: the review's wrap-check and coverage-badge recommendations
(rejected above); the `/readyz` model check and the Vertex `PROJECT_ID`
validation in §3; the Vertex model id; no `LICENSE` file.
