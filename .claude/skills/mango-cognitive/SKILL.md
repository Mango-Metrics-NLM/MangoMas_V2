---
name: mango-cognitive
description: >
  CognitiveSignal 1.1.0 producer in Mango-Mas V2. Use when: emitting or
  changing planner/reviewer envelopes, wiring MANGOMAS_SIGNAL__* (never
  MANGOMAS_HARNESS__*), attaching CognitiveSignalSink on AgentContext.extras,
  refusing cognitive fields from PDP input, or diagnosing flag-off identity
  / contained sink failures. Covers INV-16 (cognition proposes; the sibling
  Code Agent Harness disposes), extras-only wiring, and JSONL/HTTP sinks.
argument-hint: "Describe the SIGNAL change (e.g. 'emit from planner', 'HTTP sink timeout', 'PDP refuse-don't-strip')"
---

# Mango-Mas Cognitive Skill

## When to Use

- Emit or change `CognitiveSignal` 1.1.0 from planner / reviewer
- Add a tunable under `MANGOMAS_SIGNAL__*` (`SignalSettings`)
- Wire or replace `CognitiveSignalSink` (JSONL, HTTP, composite)
- Change the observation-role map (`planner`/`verifier`; `tool` raises)
- Diagnose flag-off identity, a missing JSONL line, or a swallowed sink error

## Not this skill

- Claude Code harness / `BREAKING-CHANGE` / `.claude/settings.json` hooks →
  `mango-harness`. Do **not** overload `MANGOMAS_HARNESS__*`.
- New `Agent` types → `mango-agent-add`. Emit stays in `_structured.handle`.
- Envelope schema / `mango_contracts` models → edit
  `mango-integration-contracts/` (isolated `make contracts-coverage`).

---

## Architecture (producer, not broker)

```
config/signal.py          SignalSettings (MANGOMAS_SIGNAL__*, default-OFF)
composition/signal.py     extras['cognitive_sink'] only when enabled
cognitive/constants.py    extras keys, JSONL filename, GenAI span alias
cognitive/roles.py        fail-closed observation map (never implementer)
cognitive/pdp.py          refuse_cognitive_pdp_fields (raises; does not strip)
cognitive/sink.py         Jsonl / Http / Composite + build_sink
cognitive/producer.py     build_signal + emit_agent_signal (log + swallow)
agents/_structured.py     emit after AgentResponse; stream is untouched
```

Cognition **proposes**. The sibling
[Mango Code Agent Harness](https://github.com/ianshank/Mango_Code_Agent-Harness)
**disposes** (INV-16). This package must not import `mangomas.harness`,
adapters, agents, or a harness `ExecutionBroker` / `command_actions`.

---

## Rules (do not regress)

| Rule | Detail |
|------|--------|
| Default-OFF | `MANGOMAS_SIGNAL__ENABLED=false`. Flag-off dispatch is byte-identical: no extras sink, no JSONL, no `mango_contracts` import on the handle path. |
| Extras only | Sink lives at `ctx.extras["cognitive_sink"]`. No new `AgentContext` field (`core/agent.py` is protected). |
| Prefix split | `MANGOMAS_SIGNAL__*` emits envelopes. `MANGOMAS_HARNESS__*` is Claude Code + OTel wrap. Never overload the latter. |
| Handle, not stream | Emit after `AgentResponse` in `_structured.handle`. Do not wrap `Orchestrator.dispatch` or `Tool.execute`. Streaming must not emit. |
| Contained I/O | Sink failures log + swallow. `AgentResponse` is unchanged. No new `MangomasError` for disk/HTTP. |
| Refuse, don't strip | `refuse_cognitive_pdp_fields` **raises** on `confidence` / authority-shaped keys. Never drop them and forward. |
| Join keys in lineage | `trace_id` / correlation id go in `lineage.source_event_ids` (`otel-trace:`, `mangomas.correlation_id:`). Payload models are `extra="forbid"`. |
| Chat / summarize | `harness_role_for_agent` returns `None`; `_EMITTERS` does not include them. Unmapped observation — they must not emit. |
| `tool` raises | Map raises (`UnmappedToolAgentError`). Emit path skips (not swallowed). `retrieve` stays local RAG. No `run_command` / `write_file` / `apply_patch`. Unknown names never default to `implementer`. |
| Envelope 1.1.0 | `Literal["1.1.0"]` rejects `1.0.0` at Settings parse. Do not silently coerce. |
| GenAI aliases | `gen_ai.invoke_agent` is additive and **default-off** (`MANGOMAS_SIGNAL__GENAI_SPANS`). Live spans stay `orchestrator.*` / `harness.agent_invoke`. |
| Dual install | `mango_contracts` is a sibling package, not a root `dependencies` pin. `make install`, CI, and the Docker dual-wheel build install it. Pytest `pythonpath` is not a runtime install. |

---

## Not a Claude Code hook

SIGNAL is env-driven (`MANGOMAS_SIGNAL__*`), not a `.claude/settings.json`
edit. Do **not** add PreToolUse / ConfigChange / SessionStart hooks for it.
The emit site is `_structured.handle` after `AgentResponse` — that is the
loop hook. Orchestrator acceptance loops and workflow `loop` nodes stay
unchanged.

---

## Add a sink or field (worked example)

1. New tunables are `DEFAULT_SIGNAL_*` in `config/signal.py`, re-exported
   from `mangomas.config` and `tests.constants` as `X as X`.
2. Sinks satisfy `CognitiveSignalSink` (`@runtime_checkable`). Register
   behaviour in `build_sink`; JSONL is always on, HTTP composes when
   `http_url` is set. Inner HTTP failures must not prevent the JSONL write
   (`CompositeCognitiveSink`).
3. Tests live under `tests/cognitive/` with `FakeCognitiveSink` from
   `tests/fakes.py`. Integration: `tests/integration/test_signal_flow.py`
   (gated `RUN_INTEGRATION=1`) drives `compose_app`.
4. Do not add a `mangomas cognitive` CLI, inbound HTTP list/get, OPA/gRPC,
   or Memory/Learning write-back.

## Diagnosing Failures

1. Flag-on `handle` raises `ImportError: mango_contracts` → the sibling
   package is not installed. `make install` (or the Docker dual-wheel).
2. Flag-off still writes JSONL → extras were attached while `enabled=False`,
   or an ambient `MANGOMAS_SIGNAL__ENABLED` leaked into the process.
3. Planner stream wrote a line → emit leaked into `_do_stream`; keep it in
   `handle` only.
4. Confidence changes a downstream decision → a caller fed payload/summary
   into PDP. Use `pdp_input_from_signal` only.
5. `tool` mapped to `implementer` → the role map defaulted. Unknown names
   and `tool` must raise.
