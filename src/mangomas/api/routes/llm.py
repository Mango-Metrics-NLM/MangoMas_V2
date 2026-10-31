"""LLM routes: the read-only provider-profile roster (spec-0039 / ADR-0038).

``GET /llm/profiles`` lets a client (MangoMas Studio's profile picker) discover
the names it may put in ``AgentRequest.metadata["llm_profile"]``. It serialises
the **secret-free** summary the composition root publishes on
``AgentContext.extras`` — never the clients, never a key, a ``secret_ref`` or a
``base_url`` (R6). Authenticated like every data route (ADR-0014).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request

from mangomas.api.auth import require_auth
from mangomas.api.models import LLMProfilesResponse, LLMProfileSummary
from mangomas.composition import LLM_PROFILE_INFO_EXTRAS_KEY

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator


def build_llm_router() -> APIRouter:
    """Build the LLM router (called from inside ``create_app``)."""
    router = APIRouter()

    @router.get(
        "/llm/profiles",
        response_model=LLMProfilesResponse,
        dependencies=[Depends(require_auth)],
    )
    async def list_llm_profiles(request: Request) -> LLMProfilesResponse:
        """List the configured LLM provider profiles: name, provider and model only.

        Reads the summary map rather than the client map, so a hand-assembled
        orchestrator (tests, library callers) with no extras answers ``[]``
        instead of raising, and nothing here ever introspects a client.
        """
        orch: Orchestrator = request.app.state.orchestrator
        info: dict[str, dict[str, str]] = orch.context.extras.get(LLM_PROFILE_INFO_EXTRAS_KEY, {})
        return LLMProfilesResponse(
            profiles=[
                LLMProfileSummary(name=name, provider=entry["provider"], model=entry["model"])
                for name, entry in sorted(info.items())
            ]
        )

    return router
