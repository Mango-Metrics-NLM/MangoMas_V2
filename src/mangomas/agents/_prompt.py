"""Shared system-prompt resolution and message-building helpers for agents.

Two small pieces of logic were duplicated across the five built-in agents:

1. Resolving an agent's *effective* system prompt from an explicit
   constructor argument plus an optional per-agent
   :class:`~mangomas.config.AgentSettings` override.
2. Prepending that resolved prompt to a request's message list as a
   ``system`` message, unless the caller already supplied one.

:func:`resolve_system_prompt` and :func:`build_messages` factor those two
concerns out. See :func:`resolve_system_prompt`'s docstring for an important
note: the five agents did **not** agree on precedence before this helper
existed, and that divergence is preserved verbatim (via the ``prefer_explicit``
flag) rather than silently unified.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from mangomas.core.agent import AgentRequest, Message
from mangomas.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.llm.base import LLMClient
    from mangomas.config import AgentSettings
    from mangomas.core.agent import AgentContext

logger = logging.getLogger(__name__)


#: ``AgentContext.extras`` key carrying per-agent ``LLMClient`` overrides
#: (ADR-0028 / spec-0028). Named here, beside the resolver that reads it, so the
#: writer in ``composition/builder.py`` and this reader cannot drift on the
#: spelling — a silent drift would degrade every override to ``ctx.llm`` with no
#: error anywhere.
AGENT_LLM_OVERRIDES_EXTRAS_KEY: Final[str] = "agent_llm_overrides"

#: ``AgentContext.extras`` key carrying the named provider-profile clients
#: (spec-0040 / ADR-0038): ``dict[str, LLMClient]`` keyed by profile name, built
#: once at startup by ``composition/llm.py::build_llm_profiles``. Same
#: reader-beside-writer rationale as the override key above.
LLM_PROFILES_EXTRAS_KEY: Final[str] = "llm_profiles"

#: ``AgentContext.extras`` key carrying the **secret-free** profile summaries
#: (``name -> {"provider", "model"}``) that ``GET /llm/profiles`` serialises.
#: Kept apart from the clients so the API never has to introspect one.
LLM_PROFILE_INFO_EXTRAS_KEY: Final[str] = "llm_profile_info"

#: ``AgentRequest.metadata`` / ``AgentResponse.metadata`` key naming the
#: selected profile. The selector rides the existing free-form dict rather
#: than a new DTO field, so ``core/agent.py`` is untouched (spec-0040 R2/R5).
LLM_PROFILE_METADATA_KEY: Final[str] = "llm_profile"


def select_llm_profile(extras: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    """Return the profile name *metadata* selects, validated against *extras*.

    ``None`` when the request names no profile. A name that is not a string, or
    that no built profile answers to, raises :class:`~mangomas.errors.ConfigError`
    (HTTP 400) — the route handlers call this **before** dispatch so an unknown
    name is refused before any SSE header goes out, and
    :func:`resolve_llm` calls it again at use time so the CLI and workflow
    paths get the same answer with no HTTP layer in front of them.

    Pure: reads two mappings, touches nothing.
    """
    name = metadata.get(LLM_PROFILE_METADATA_KEY)
    if name is None:
        return None
    profiles = extras.get(LLM_PROFILES_EXTRAS_KEY) or {}
    if not isinstance(name, str) or name not in profiles:
        raise ConfigError(f"unknown llm_profile {name!r}; configured profiles: {sorted(profiles)}")
    return name


def resolve_llm(
    ctx: AgentContext, agent_name: str, request: AgentRequest | None = None
) -> LLMClient:
    """Return the LLM client this agent should use for this request.

    Precedence, most specific first:

    1. the **request-scoped profile** named by
       ``request.metadata["llm_profile"]`` (spec-0040; explicit, per run);
    2. the **agent-scoped override** in ``ctx.extras["agent_llm_overrides"]``
       (ADR-0028 / spec-0028; a deployment default for one agent);
    3. ``ctx.llm``, the shared default.

    Per ADR-0028, the override dict is a seam built by the composition layer
    (``composition/builder.py``), not this package; the same is true of the
    profile dict. This resolver has no opinion on how (or whether) either is
    populated; it degrades gracefully to ``ctx.llm`` when ``extras`` lacks the
    keys, or lacks *this* agent's name within the override map — the identical
    "explicit-arg -> per-agent override -> shared default" shape
    :func:`resolve_sampling` uses, just resolved at call time (``handle``/
    ``stream``) rather than construction time, since agents only receive
    ``ctx`` once a request arrives. *request* is optional so callers that
    predate spec-0040 keep working unchanged.

    When a profile shadows a configured override the fact is logged at INFO
    (``event="llm_profile_shadows_override"``), because the two are set by
    different people — the override by the operator, the profile by the
    caller — and a silently losing override is the confusion the risk register
    predicted.
    """
    overrides: dict[str, LLMClient] = ctx.extras.get(AGENT_LLM_OVERRIDES_EXTRAS_KEY, {})
    profile = select_llm_profile(ctx.extras, request.metadata) if request is not None else None
    if profile is not None:
        if agent_name in overrides:
            logger.info(
                "llm_profile shadows the agent's MODEL_OVERRIDE for this request",
                extra={
                    "event": "llm_profile_shadows_override",
                    "agent": agent_name,
                    "llm_profile": profile,
                },
            )
        profiles: dict[str, LLMClient] = ctx.extras[LLM_PROFILES_EXTRAS_KEY]
        return profiles[profile]
    return overrides.get(agent_name, ctx.llm)


def resolve_system_prompt(
    explicit: str | None,
    settings: AgentSettings | None,
    *,
    suffix: str | None = None,
    prefer_explicit: bool = False,
) -> str | None:
    """Resolve an agent's effective system prompt.

    Precedence — two modes, preserved from the pre-existing per-agent code
    rather than unified (a genuine behavioural discrepancy predates this
    helper; see spec-0014 milestone M5):

    * ``prefer_explicit=False`` (default) — "settings-first", used by
      :class:`~mangomas.agents.chat.ChatAgent`,
      :class:`~mangomas.agents.summarize.SummarizeAgent`, and
      :class:`~mangomas.agents.tool_agent.ToolAgent`. When *settings* is
      given and ``settings.system_prompt`` is not ``None`` it wins over
      *explicit*; otherwise *explicit* is used (even if it is ``None`` or
      an empty string — no truthiness collapsing).
    * ``prefer_explicit=True`` — "explicit-first", used by
      :class:`~mangomas.agents.planner.PlannerAgent` and
      :class:`~mangomas.agents.reviewer.ReviewerAgent` (via
      :class:`~mangomas.agents._structured.StructuredOutputAgent`).
      *explicit* wins whenever it is not ``None``; only when *explicit* is
      ``None`` does ``settings.system_prompt`` apply.

    Suffix concatenation — when *suffix* is given (the planner/reviewer JSON
    schema prompt, or ToolAgent's tool-format prompt), the precedence-resolved
    base is concatenated with *suffix* using ``"\\n\\n"`` when a base prompt is
    present, or *suffix* is returned alone when the base is ``None``. This
    reproduces the already-landed ``tool_agent.py`` D1 fix (custom prompt
    concatenated with tool prompt, custom first) exactly — including reuse:
    ``ToolAgent.handle`` calls this function a *second* time per-request with
    its already-resolved ``self._system_prompt`` as *explicit*, ``settings=None``
    (so precedence is moot — the value simply passes through), and
    ``suffix=<tool prompt>`` to fold in the per-request, tool-availability-
    dependent prompt fragment.

    Note: when *suffix* is ``None`` the resolved value is returned exactly as
    computed (no truthiness collapsing — an explicit empty string stays an
    empty string, matching the plain-ternary callers). When *suffix* is not
    ``None`` and the resolved base is falsy-but-not-``None`` (an explicit
    empty-string override — not exercised by any real caller since ``None`` is
    always used to mean "no override"), the combination degrades to
    concatenating rather than the legacy ``or``-based fallback the original
    ``ToolAgent`` combine step used for that same corner case; this is an
    accepted, unreachable-in-practice simplification.
    """
    if prefer_explicit:
        base = (
            explicit
            if explicit is not None
            else (settings.system_prompt if settings is not None else None)
        )
    else:
        base = (
            settings.system_prompt
            if settings is not None and settings.system_prompt is not None
            else explicit
        )
    if suffix is None:
        return base
    return f"{base}\n\n{suffix}" if base is not None else suffix


def build_messages(request: AgentRequest, system_prompt: str | None) -> list[Message]:
    """Prepend *system_prompt* as a system message unless the request has one.

    Returns a new list (never mutates ``request.messages``). When
    *system_prompt* is falsy (``None`` or an empty string) or the request
    already contains a ``system``-role message, the request's messages are
    returned unchanged (as a copy).
    """
    messages = list(request.messages)
    if system_prompt and not any(m.role == "system" for m in messages):
        messages.insert(0, Message(role="system", content=system_prompt))
    return messages


def resolve_sampling(settings: AgentSettings | None) -> tuple[float | None, int | None]:
    """Return ``(temperature, max_tokens)`` from *settings*, or ``(None, None)``.

    Extracted because the identical two-line pair was repeated verbatim in four
    agent constructors (`chat`, `summarize`, `tool_agent`, `_structured`). A
    fifth sampling knob would otherwise mean five more edits; now it means one.
    Both stay ``None`` when unset so the LLM adapter keeps its own default.
    """
    if settings is None:
        return None, None
    return settings.temperature, settings.max_tokens
