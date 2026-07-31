"""HTTP route factories, grouped by surface (spec 0014 / M11).

Each module exposes a ``build_*_router() -> APIRouter`` factory rather than a
module-level router: ``create_app`` calls them so per-app configuration (e.g.
the ``/history`` Query bounds) is read at app-construction time, matching the
settings-cache semantics the tests rely on.
"""

from __future__ import annotations

from mangomas.api.routes.agents import build_agent_router
from mangomas.api.routes.system import build_system_router
from mangomas.api.routes.workflows import build_workflow_router

__all__ = ["build_agent_router", "build_system_router", "build_workflow_router"]
