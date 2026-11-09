"""Hardened deploy posture is a manifest contract, not prose (spec-0045 A4).

ADR-0033 and ADR-0040 keep the *library* defaults permissive for backward
compatibility: inline workflow definitions are accepted, the repair-loop oracle
runs unisolated with a warning, and the model / provider allowlists are empty.
The deploy manifests are where a *service* closes those doors. Each rendered
environment is held to the closed posture here, so a future overlay cannot
quietly reopen one — the same mechanism-over-prose rule as every other
``tests/deploy`` contract.

``dev`` is the one deliberate exception, for inline definitions only: a
caller-supplied graph is how a workflow is tried before it is committed, and
dev is the environment that exists for trying things. It still gets the
isolation and allowlist pins.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._script_loader import load_script_module
from tests.constants.deploy import RENDER_PLACEHOLDER_VALUES, TEST_IMAGE_REF
from tests.deploy import _makefile, _manifest

deploy_env = load_script_module("deploy_environment.py")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY = deploy_env.load_registry(_REPO_ROOT / _makefile.variable("DEPLOY_REGISTRY"))
_ENVIRONMENT_NAMES = list(_REGISTRY.environments)
_INLINE_DEFINITIONS_ALLOWED_IN = frozenset({"dev"})

_INLINE_VAR = "MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION"
_ISOLATION_VAR = "MANGOMAS_REPAIR__ORACLE_ISOLATION"
_ALLOWED_MODELS_VAR = "MANGOMAS_LLM__ALLOWED_MODELS"
_PROVIDER_ALLOWLIST_VAR = "MANGOMAS_LLM__PROVIDER_ALLOWLIST"
_MODEL_VAR = "MANGOMAS_LLM__MODEL"
_PROVIDER_VAR = "MANGOMAS_LLM__PROVIDER"


def _rendered_env(name: str) -> dict[str, str]:
    base = _manifest.base_manifest(_REPO_ROOT / _REGISTRY.base_manifest)
    rendered: dict[str, Any] = deploy_env.render_manifest(
        base, _REGISTRY.environments[name], TEST_IMAGE_REF, dict(RENDER_PLACEHOLDER_VALUES)
    )
    return {
        name_: entry["value"]
        for name_, entry in _manifest.env_vars(rendered).items()
        if "value" in entry
    }


def test_environments_were_discovered() -> None:
    """Vacuity guard: an empty registry would green-light every rule below."""
    assert set(_ENVIRONMENT_NAMES) >= {"dev", "qa", "prod"}


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_inline_workflow_definitions_are_closed_outside_dev(name: str) -> None:
    env = _rendered_env(name)
    expected = "true" if name in _INLINE_DEFINITIONS_ALLOWED_IN else "false"
    assert env[_INLINE_VAR] == expected, (
        f"{name}: {_INLINE_VAR}={env.get(_INLINE_VAR)!r}; a caller-supplied graph on "
        "POST /workflows/run is a dev-only affordance (ADR-0033)"
    )


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_repair_oracle_isolation_is_required(name: str) -> None:
    assert _rendered_env(name)[_ISOLATION_VAR] == "required", (
        f"{name}: the repair-loop oracle must fail closed without a sandbox (ADR-0040)"
    )


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_model_allowlist_is_non_empty_and_contains_the_model(name: str) -> None:
    """A non-empty roster must include MODEL itself or Settings refuses to parse (ADR-0033)."""
    env = _rendered_env(name)
    roster = json.loads(env[_ALLOWED_MODELS_VAR])
    assert isinstance(roster, list) and roster, f"{name}: {_ALLOWED_MODELS_VAR} is empty"
    assert env[_MODEL_VAR] in roster, f"{name}: {_MODEL_VAR} is not in its own allowlist"
    assert "${" not in env[_ALLOWED_MODELS_VAR], f"{name}: unfilled placeholder in the roster"


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_provider_allowlist_is_non_empty_and_contains_the_provider(name: str) -> None:
    env = _rendered_env(name)
    roster = json.loads(env[_PROVIDER_ALLOWLIST_VAR])
    assert isinstance(roster, list) and roster, f"{name}: {_PROVIDER_ALLOWLIST_VAR} is empty"
    assert env[_PROVIDER_VAR] in roster, f"{name}: {_PROVIDER_VAR} is not in its own allowlist"
