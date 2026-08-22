"""OpenAPI-surface snapshot for the HTTP DTO contract (spec-0022 R14).

The backwards-compatible-contracts rule was enforced only by reviewer
discipline: nothing mechanical noticed a DTO field turning required, a route
disappearing, or a schema being reshaped. This test pins a **normalized
projection** of the generated OpenAPI document — every path's methods and
operation ids, and every component schema's property/required sets — rather
than the raw document, because ``fastapi``/``pydantic`` float on ``>=`` pins
and framework minors reshape raw OpenAPI output (nullable encodings, ``anyOf``
shapes); a raw snapshot would go red on unrelated dependency bumps.

A deliberate surface change regenerates the snapshot in the same PR::

    python -m tests.test_openapi_snapshot

which makes the diff the review record for the contract change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mangomas.agents import ChatAgent
from mangomas.api.app import create_app
from mangomas.core import AgentContext, Orchestrator
from tests.fakes import FakeLLM

_SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "openapi.json"
_REGEN_HINT = (
    "OpenAPI surface changed. If deliberate, regenerate the snapshot in this "
    "PR with: python -m tests.test_openapi_snapshot"
)


def _build_app() -> Any:
    # The injected-orchestrator form: no lifespan, no external services, and
    # app.openapi() needs no TestClient at all.
    ctx = AgentContext(llm=FakeLLM(), repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return create_app(orchestrator=orch)


def _projection() -> dict[str, Any]:
    schema = _build_app().openapi()
    paths = {
        path: {
            method: operation.get("operationId", "")
            for method, operation in sorted(methods.items())
        }
        for path, methods in sorted(schema["paths"].items())
    }
    components = {
        name: {
            "properties": sorted((component.get("properties") or {}).keys()),
            "required": sorted(component.get("required", [])),
        }
        for name, component in sorted(schema.get("components", {}).get("schemas", {}).items())
    }
    return {"paths": paths, "components": components}


def _serialize(projection: dict[str, Any]) -> str:
    return json.dumps(projection, indent=2, sort_keys=True) + "\n"


def test_snapshot_file_is_wellformed() -> None:
    """Vacuity guard: a missing or stub snapshot must fail loudly here, not
    let the comparison below diff against nothing."""
    data = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert data["paths"], "snapshot has no paths — regenerate it"
    assert data["components"], "snapshot has no component schemas — regenerate it"


def test_openapi_projection_matches_committed_snapshot() -> None:
    assert _serialize(_projection()) == _SNAPSHOT_PATH.read_text(encoding="utf-8"), _REGEN_HINT


if __name__ == "__main__":
    _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SNAPSHOT_PATH.write_text(_serialize(_projection()), encoding="utf-8")
    print(f"wrote {_SNAPSHOT_PATH}")  # noqa: T201
