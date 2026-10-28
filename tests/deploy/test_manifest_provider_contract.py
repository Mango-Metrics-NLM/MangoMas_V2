"""The reference manifest must be deployable as written (spec-0038, P0-B).

``deploy/service.yaml`` selects four cloud providers. Before this contract
nothing tied that selection to anything: the image could lack a provider's
SDK (P0-A fixed the image), the manifest could select Postgres without a DSN
(``DBSettings.url`` silently kept the SQLite default), name a setting that no
``Settings`` field reads, or ship with auth off and no request bounds. Every
rendered environment is checked here against four sources that already exist
and are never restated: the ``Settings`` tree (field names), the Dockerfile's
default extras expanded through pyproject (SDK availability), the
``PROVIDER_REQUIREMENTS`` rows in ``tests.constants.deploy`` (what each
provider needs), and the Makefile's ``IMAGE_SMOKE_IMPORTS`` (what CI imports
inside the built image).
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests._script_loader import load_script_module
from tests.constants.deploy import (
    API_MAX_BODY_BYTES_VAR,
    API_MAX_CONCURRENT_REQUESTS_VAR,
    AUTH_ENABLED_VAR,
    AUTH_SECRET_REF_VAR,
    DB_URL_VAR,
    IMAGE_EXTRAS_BUILD_ARG,
    LLM_API_KEY_VAR,
    PROVIDER_REQUIREMENTS,
    RENDER_PLACEHOLDER_VALUES,
    TEST_IMAGE_REF,
    ProviderRequirement,
)
from tests.deploy import _dockerfile, _makefile, _manifest, _pyproject, _settings_names

deploy_env = load_script_module("deploy_environment.py")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY = deploy_env.load_registry(_REPO_ROOT / _makefile.variable("DEPLOY_REGISTRY"))
_ENVIRONMENT_NAMES = list(_REGISTRY.environments)
#: Vacuity floor: the base manifest selects at least this many provider rows.
_MIN_SELECTED_PROVIDERS = 3
#: Settings whose value is a credential; a literal in a manifest is a leak.
_SECRET_SHAPED_VARS = (LLM_API_KEY_VAR, DB_URL_VAR)
_PLACEHOLDER_RE = re.compile(r"\$\{[^}]*\}")


def _rendered(name: str) -> dict[str, Any]:
    result: dict[str, Any] = deploy_env.render_manifest(
        _manifest.base_manifest(),
        _REGISTRY.environments[name],
        TEST_IMAGE_REF,
        RENDER_PLACEHOLDER_VALUES,
    )
    return result


def _plain_values(manifest: dict[str, Any]) -> dict[str, str]:
    """``name -> value`` for every plain (non-secretKeyRef) env entry."""
    return {
        name: str(entry["value"])
        for name, entry in _manifest.env_vars(manifest).items()
        if "value" in entry
    }


def selected_providers(values: Mapping[str, str]) -> list[ProviderRequirement]:
    """The rows whose selector the manifest sets."""
    return [row for row in PROVIDER_REQUIREMENTS if values.get(row.selector[0]) == row.selector[1]]


def image_extras() -> set[str]:
    """Every extra the production image installs, meta-extras expanded."""
    extras: set[str] = set()
    for extra in _dockerfile.build_arg_default(IMAGE_EXTRAS_BUILD_ARG).split(","):
        if extra.strip():
            extras |= _pyproject.extra_closure(extra.strip())
    return extras


def missing_required_settings(values: Mapping[str, str], manifest: dict[str, Any]) -> list[str]:
    """``provider:VAR`` for every required setting a selected provider lacks."""
    present = set(_manifest.env_vars(manifest))
    return [
        f"{row.name}:{var}"
        for row in selected_providers(values)
        for var in row.required
        if var not in present
    ]


def smoke_imports() -> set[str]:
    return {part.strip() for part in _makefile.variable("IMAGE_SMOKE_IMPORTS").split(",")}


# ── denominators ──────────────────────────────────────────────────────────────


def test_base_manifest_selects_providers() -> None:
    """Guard the denominator: with no row selected every check below is vacuous."""
    selected = selected_providers(_plain_values(_manifest.base_manifest()))
    assert len(selected) >= _MIN_SELECTED_PROVIDERS, [row.name for row in selected]


def test_provider_rows_name_real_extras_and_settings() -> None:
    """Every row's extra exists in pyproject and every required var is a Settings field."""
    table = _pyproject.extras_table()
    declared = _settings_names.declared_names()
    for row in PROVIDER_REQUIREMENTS:
        assert row.extra in table, f"{row.name}: extra {row.extra!r} is not in pyproject"
        assert row.selector[0] in declared, f"{row.name}: selector {row.selector[0]} is not a field"
        for var in row.required:
            assert var in declared, f"{row.name}: required {var} is not a Settings field"


# ── the contract, over every rendered environment ─────────────────────────────


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_every_manifest_env_name_resolves_to_a_settings_field(name: str) -> None:
    """A misspelled ``MANGOMAS_*`` name is silently ignored by pydantic-settings."""
    names = set(_manifest.env_vars(_rendered(name)))
    assert _settings_names.unresolved(names) == []


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_each_selected_provider_has_its_extra_in_the_image(name: str) -> None:
    """Selecting a provider the image cannot import boots, then fails on the first request."""
    extras = image_extras()
    missing = [
        f"{row.name} needs extra {row.extra!r}"
        for row in selected_providers(_plain_values(_rendered(name)))
        if row.extra not in extras
    ]
    assert missing == [], f"{name}: {missing}; image extras are {sorted(extras)}"


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_each_selected_provider_has_its_required_settings(name: str) -> None:
    """Postgres without a DSN keeps the SQLite default; Vertex without a project id raises."""
    manifest = _rendered(name)
    assert missing_required_settings(_plain_values(manifest), manifest) == []


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_secret_shaped_settings_are_never_literals(name: str) -> None:
    """The DSN carries the password and the API key is a credential: secretKeyRef only."""
    entries = _manifest.env_vars(_rendered(name))
    for var in _SECRET_SHAPED_VARS:
        assert var in entries, f"{name}: {var} missing"
        assert "value" not in entries[var], f"{name}: {var} is a literal"
        assert entries[var]["valueFrom"]["secretKeyRef"]["name"]


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_rendered_manifests_contain_no_unfilled_placeholders(name: str) -> None:
    """The base is never substituted: an overlay that forgets to restate a
    ``${...}`` value would deploy the literal placeholder as a project id."""
    values = _plain_values(_rendered(name))
    leaked = {var: value for var, value in values.items() if _PLACEHOLDER_RE.search(value)}
    assert leaked == {}, f"{name}: unfilled placeholders {leaked}"


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_reference_manifest_enables_auth_and_backpressure(name: str) -> None:
    """Safe defaults: a copied manifest must not ship an open, unbounded service."""
    manifest = _rendered(name)
    values = _plain_values(manifest)
    assert values.get(AUTH_ENABLED_VAR) == "true", f"{name}: auth is off"
    assert values.get(AUTH_SECRET_REF_VAR), f"{name}: auth enabled without a token secret ref"
    assert int(values[API_MAX_BODY_BYTES_VAR]) > 0, f"{name}: request body unbounded"
    container_concurrency = manifest["spec"]["template"]["spec"]["containerConcurrency"]
    assert int(values[API_MAX_CONCURRENT_REQUESTS_VAR]) == int(container_concurrency), (
        f"{name}: the app's in-flight cap must equal containerConcurrency, or the app "
        f"refuses requests Cloud Run routes (or the cap is never reached)"
    )


def test_non_production_environments_use_their_own_auth_token_ref() -> None:
    """The token ref is a plain value naming a secret, so the secretKeyRef test
    above cannot see it; a dev token must still never open production."""
    prod = _plain_values(_manifest.base_manifest())[AUTH_SECRET_REF_VAR]
    refs = {prod}
    for name in _ENVIRONMENT_NAMES:
        if _REGISTRY.environments[name].trigger.branch is None:
            continue  # production (tag-triggered) keeps the base ref
        ref = _plain_values(_rendered(name))[AUTH_SECRET_REF_VAR]
        assert ref != prod, f"{name} reuses production's API token secret {prod!r}"
        refs.add(ref)
    assert len(refs) == len(_ENVIRONMENT_NAMES), "two environments share a token secret"


# ── image smoke ↔ manifest ────────────────────────────────────────────────────


def test_smoke_import_list_covers_every_selected_provider() -> None:
    """`make docker-smoke` must import, inside the image, each selected provider's SDK."""
    selected = {
        row.smoke_module for row in selected_providers(_plain_values(_manifest.base_manifest()))
    }
    missing = selected - smoke_imports()
    assert missing == set(), f"IMAGE_SMOKE_IMPORTS lacks {sorted(missing)}"


def test_docker_smoke_target_builds_then_imports_inside_the_image() -> None:
    body = _makefile.target_body("docker-smoke")
    assert body.startswith("docker-smoke: docker-build"), "smoke must build the image first"
    assert "docker run --rm" in body
    assert "import $(IMAGE_SMOKE_IMPORTS)" in body
    assert "from mangomas.api.app import create_app" in body, "the app itself must import too"
    assert 'docker build -t "$(DOCKER_IMAGE)" .' in _makefile.target_body("docker-build")


# ── mutation proofs (mango-mutation-proof) ────────────────────────────────────


def test_a_misspelled_env_name_is_detected() -> None:
    assert _settings_names.unresolved({"MANGOMAS_LLM__MODL"}) == ["MANGOMAS_LLM__MODL"]


def test_a_missing_required_setting_is_detected() -> None:
    """Dropping the DSN from a manifest copy must name postgres as unsatisfied."""
    manifest = _manifest.base_manifest()
    env = _manifest.container(manifest)["env"]
    env[:] = [entry for entry in env if entry["name"] != DB_URL_VAR]
    assert missing_required_settings(_plain_values(manifest), manifest) == [
        f"postgres:{DB_URL_VAR}"
    ]


def test_a_provider_without_its_extra_is_detected() -> None:
    """A `cloud` meta-extra that lost `postgres` must fail the extras check."""
    real = _pyproject.PYPROJECT.read_text(encoding="utf-8")
    mutated = real.replace('"mangomas[vertex,postgres,gcp]"', '"mangomas[vertex,gcp]"', 1)
    assert mutated != real, "cloud extra definition moved — fixture needs updating"
    assert "postgres" not in _pyproject.extra_closure("cloud", text=mutated)
    assert "postgres" in _pyproject.extra_closure("cloud")


def test_an_unknown_extra_is_rejected_not_empty() -> None:
    with pytest.raises(AssertionError, match="no extra named"):
        _pyproject.extra_closure("definitely-not-an-extra")


def test_placeholder_detection_sees_the_base_manifest() -> None:
    """The unfilled-placeholder guard only means something if the base has some."""
    values = _plain_values(_manifest.base_manifest())
    assert any(string.Template(v).get_identifiers() for v in values.values())
    assert _PLACEHOLDER_RE.search("${PROJECT_ID}")
