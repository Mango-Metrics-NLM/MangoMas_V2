"""Manifest render contract tests (spec-0049 / ADR-0036).

Verifies Cloud Run deployment manifests:
1. Deep recursive inspection of rendered manifests across all environments
   (`dev`, `qa`, `prod`) to assert zero unrendered ``${...}`` placeholders in any
   key, value, annotation, secret, label, service account, or container image.
2. Offline registry validation contract:
   - Programmatic ``validate_registry`` checks all environments.
   - CLI ``scripts/deploy_environment.py validate`` exits 0 (``EXIT_OK``).
3. Manifest render contract:
   - CLI ``scripts/deploy_environment.py render`` with ``--substitute-from-env``
     outputs valid YAML to disk with zero unrendered placeholders and passes
     ``verify_rendered``.
4. Falsifiability & mutation proofs:
   - Unset variable in ``--substitute-from-env`` raises ``DeployConfigError`` / exits code 2.
   - Un-overridden base placeholder is caught by deep recursive inspection.
   - Malformed placeholder syntax (e.g. ``${BAD``) raises ``DeployConfigError``.
   - Deep inspection correctly catches placeholders in keys, nested dicts, and lists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from tests._script_loader import load_script_module
from tests.constants.deploy import (
    LLM_MODEL_PLACEHOLDER_VAR,
    PROJECT_ID_PLACEHOLDER_VAR,
    RENDER_PLACEHOLDER_VALUES,
    TEST_IMAGE_REF,
    TEST_LLM_MODEL,
    TEST_PROJECT_ID,
)
from tests.deploy import _makefile, _manifest

deploy_env = load_script_module("deploy_environment.py")
DeployConfigError = deploy_env.DeployConfigError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = _REPO_ROOT / _makefile.variable("DEPLOY_REGISTRY")
_REGISTRY = deploy_env.load_registry(_REGISTRY_PATH)
_ENVIRONMENT_NAMES: list[str] = list(_REGISTRY.environments)

_PLACEHOLDER_RE = re.compile(r"\$\{[^}]*\}")


def find_unrendered_placeholders(obj: Any, path: str = "$") -> list[tuple[str, str]]:
    """Traverse *obj* recursively and return ``(path, value)`` for any unrendered placeholder."""
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            current_path = f"{path}.{k}"
            if isinstance(k, str) and _PLACEHOLDER_RE.search(k):
                found.append((f"{path}[key:{k}]", k))
            found.extend(find_unrendered_placeholders(v, current_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            current_path = f"{path}[{idx}]"
            found.extend(find_unrendered_placeholders(item, current_path))
    elif isinstance(obj, str) and _PLACEHOLDER_RE.search(obj):
        found.append((path, obj))
    return found


def _render_environment(name: str) -> dict[str, Any]:
    """Helper rendering an environment overlay against the real base manifest."""
    return cast(
        "dict[str, Any]",
        deploy_env.render_manifest(
            _manifest.base_manifest(),
            _REGISTRY.environments[name],
            TEST_IMAGE_REF,
            RENDER_PLACEHOLDER_VALUES,
        ),
    )


# ── 1. Deep recursive inspection for zero placeholders ───────────────────────


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_rendered_manifest_has_zero_unrendered_placeholders(name: str) -> None:
    """Every rendered environment overlay must have zero unrendered placeholders."""
    rendered = _render_environment(name)
    unrendered = find_unrendered_placeholders(rendered)
    assert unrendered == [], f"Environment {name!r} contains unrendered placeholders: {unrendered}"


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_rendered_manifest_structural_contract(name: str) -> None:
    """Rendered manifest maintains Cloud Run structure and expected overrides."""
    rendered = _render_environment(name)
    env_config = _REGISTRY.environments[name]

    # Service name & labels match configured service
    assert rendered["metadata"]["name"] == env_config.service
    assert rendered["metadata"]["labels"]["app"] == env_config.service

    # Container image set correctly
    container = deploy_env._single_container(rendered)
    assert container["image"] == TEST_IMAGE_REF

    # Service account matches override if set
    template_spec = rendered["spec"]["template"]["spec"]
    if env_config.overrides.service_account is not None:
        expected_sa = deploy_env._substitute(
            env_config.overrides.service_account, RENDER_PLACEHOLDER_VALUES, "service_account"
        )
        assert template_spec["serviceAccountName"] == expected_sa
        assert _PLACEHOLDER_RE.search(template_spec["serviceAccountName"]) is None


# ── 2. Offline registry validation contract ──────────────────────────────────


def test_validate_registry_programmatic() -> None:
    """Programmatic validate_registry renders every environment offline without credentials."""
    checked = deploy_env.validate_registry(_REGISTRY, _manifest.base_manifest())
    assert checked == _ENVIRONMENT_NAMES
    assert len(checked) == 3
    assert set(checked) == {"dev", "qa", "prod"}


def test_validate_registry_cli() -> None:
    """CLI validate command parses the registry and renders all environments successfully."""
    exit_code = deploy_env.main(["--registry", str(_REGISTRY_PATH), "validate"])
    assert exit_code == deploy_env.EXIT_OK


def test_validate_registry_cli_with_base_manifest_override() -> None:
    """CLI validate supports --base-manifest override."""
    exit_code = deploy_env.main(
        [
            "--registry",
            str(_REGISTRY_PATH),
            "validate",
            "--base-manifest",
            str(_manifest.SERVICE_YAML),
        ]
    )
    assert exit_code == deploy_env.EXIT_OK


# ── 3. Manifest render CLI contract ──────────────────────────────────────────


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_render_manifest_cli_writes_valid_yaml(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI render command fills placeholders from environment variables and passes verify."""
    monkeypatch.setenv(PROJECT_ID_PLACEHOLDER_VAR, TEST_PROJECT_ID)
    monkeypatch.setenv(LLM_MODEL_PLACEHOLDER_VAR, TEST_LLM_MODEL)

    output_path = tmp_path / f"rendered-{name}.yaml"
    exit_code = deploy_env.main(
        [
            "--registry",
            str(_REGISTRY_PATH),
            "render",
            "--environment",
            name,
            "--image",
            TEST_IMAGE_REF,
            "--output",
            str(output_path),
            "--substitute-from-env",
            PROJECT_ID_PLACEHOLDER_VAR,
            "--substitute-from-env",
            LLM_MODEL_PLACEHOLDER_VAR,
        ]
    )
    assert exit_code == deploy_env.EXIT_OK
    assert output_path.exists()

    content = output_path.read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(content)

    # Prove zero unrendered placeholders exist in the written YAML
    unrendered = find_unrendered_placeholders(loaded)
    assert unrendered == [], f"Written YAML for {name!r} has unrendered placeholders: {unrendered}"

    # Prove container runs the intended image via verify_rendered
    deploy_env.verify_rendered(output_path, TEST_IMAGE_REF)


# ── 4. Falsifiability & mutation proofs ───────────────────────────────────────


def test_falsifiability_missing_substitute_env_var_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset variable specified in --substitute-from-env raises DeployConfigError / exits 2."""
    monkeypatch.delenv(PROJECT_ID_PLACEHOLDER_VAR, raising=False)
    monkeypatch.setenv(LLM_MODEL_PLACEHOLDER_VAR, TEST_LLM_MODEL)

    # Programmatic check
    with pytest.raises(DeployConfigError, match=r"--substitute-from-env: unset environment"):
        deploy_env._variables_from_env([PROJECT_ID_PLACEHOLDER_VAR, LLM_MODEL_PLACEHOLDER_VAR])

    # CLI check
    output_path = tmp_path / "failed.yaml"
    exit_code = deploy_env.main(
        [
            "--registry",
            str(_REGISTRY_PATH),
            "render",
            "--environment",
            "dev",
            "--image",
            TEST_IMAGE_REF,
            "--output",
            str(output_path),
            "--substitute-from-env",
            PROJECT_ID_PLACEHOLDER_VAR,
        ]
    )
    assert exit_code == deploy_env.EXIT_CONFIG_ERROR
    assert not output_path.exists()


def test_falsifiability_unoverridden_base_placeholder_detected() -> None:
    """If the base manifest contains a placeholder not overridden, deep search detects it."""
    base = _manifest.base_manifest()
    # Inject an un-overridden placeholder into the base manifest
    container = deploy_env._single_container(base)
    container["env"].append({"name": "MANGOMAS_LEAKED_VAR", "value": "${UNOVERRIDDEN_BASE_VAR}"})

    rendered = deploy_env.render_manifest(
        base,
        _REGISTRY.environments["prod"],
        TEST_IMAGE_REF,
        RENDER_PLACEHOLDER_VALUES,
    )
    unrendered = find_unrendered_placeholders(rendered)
    assert len(unrendered) == 1
    path, value = unrendered[0]
    assert value == "${UNOVERRIDDEN_BASE_VAR}"
    assert "MANGOMAS_LEAKED_VAR" in path or "env" in path


def test_falsifiability_malformed_placeholder_syntax_raises_deploy_config_error() -> None:
    """Malformed placeholder syntax (e.g. unclosed bracket) raises DeployConfigError."""
    with pytest.raises(DeployConfigError, match="malformed placeholder"):
        deploy_env._substitute("${UNCLOSED_PLACEHOLDER", RENDER_PLACEHOLDER_VALUES, "test_field")


def test_falsifiability_missing_placeholder_value_raises_deploy_config_error() -> None:
    """A placeholder with no value passed in variables raises DeployConfigError."""
    with pytest.raises(DeployConfigError, match=r"placeholder \$\{MISSING\} has no value"):
        deploy_env._substitute("${MISSING}", {}, "test_field")


def test_falsifiability_deep_recursive_detector_finds_all_nested_locations() -> None:
    """The recursive placeholder detector traverses dict keys, values, annotations, and lists."""
    synthetic_tree: dict[str, Any] = {
        "metadata": {
            "name": "valid-name",
            "labels": {"app": "valid-app"},
            "${BAD_KEY}": "bad-key-value",
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "normal.annotation": "1",
                        "autoscaling.knative.dev/maxScale": "${LEAKED_ANNOTATION}",
                    }
                },
                "spec": {
                    "serviceAccountName": "${LEAKED_SA}@proj.iam.gserviceaccount.com",
                    "containers": [
                        {
                            "image": "${LEAKED_IMAGE}",
                            "env": [
                                {"name": "VAR_A", "value": "clean_value"},
                                {"name": "VAR_B", "value": "${LEAKED_ENV_VALUE}"},
                            ],
                            "ports": [
                                {"name": "http1", "containerPort": 8080},
                                {"name": "${LEAKED_PORT_NAME}", "containerPort": 8081},
                            ],
                        }
                    ],
                },
            }
        },
        "raw_list": ["item1", "${LEAKED_LIST_STRING}", "item3"],
    }

    found = find_unrendered_placeholders(synthetic_tree)
    found_values = {val for _, val in found}
    expected_values = {
        "${BAD_KEY}",
        "${LEAKED_ANNOTATION}",
        "${LEAKED_SA}@proj.iam.gserviceaccount.com",
        "${LEAKED_IMAGE}",
        "${LEAKED_ENV_VALUE}",
        "${LEAKED_PORT_NAME}",
        "${LEAKED_LIST_STRING}",
    }
    assert found_values == expected_values
    assert len(found) == 7
