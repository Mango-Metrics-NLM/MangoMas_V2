"""Unknown and authority-shaped fields are rejected (extra='forbid')."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.mango_contracts.constants import BLOB_CONTENT_HASH, envelope_base

from mango_contracts import CognitiveSignal, EvidenceBundle, SignalKind
from mango_contracts.authority import (
    FORBIDDEN_AUTHORITY_KEYS,
    FORBIDDEN_SECRET_KEYS,
    normalise_key,
    reject_authority_shaped_keys,
)
from mango_contracts.cognitive_signal import CognitiveRecommendation, SignalLineage
from mango_contracts.evidence import EvidenceReference
from mango_contracts.payloads import (
    PiiFindingPayload,
    PlanningProposalPayload,
    ReviewFindingPayload,
    RoutingRecommendationPayload,
)
from mango_contracts.proposed_action import ProposedAction

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowed_tools", ["write_file", "shell"]),
        ("execution_approved", True),
        ("release_approved", True),
        ("granted_capabilities", ["workspace.write"]),
        ("policy_override", {"allow_destructive": True}),
        ("gate_passed", True),
        ("retry_limit", 999),
        ("execution_priority", 1),
        ("human_approved", True),
        ("sandbox_bypass", True),
        ("capability_grant", "write"),
    ],
)
def test_authority_shaped_extra_fields_are_rejected(field: str, value: object) -> None:
    raw = envelope_base()
    raw[field] = value
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_invalid_control_fixture_is_rejected() -> None:
    raw = json.loads((_FIXTURES / "invalid_control_field.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_invalid_signal_type_is_rejected() -> None:
    raw = envelope_base(signal_type="review.finding.extra")
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_signal_type_must_match_kind() -> None:
    raw = envelope_base(signal_type="pii.finding")
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_schema_1_0_0_is_rejected() -> None:
    raw = envelope_base(schema_version="1.0.0")
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_sufficient_evidence_requires_references() -> None:
    raw = envelope_base()
    raw["evidence"] = {"status": "sufficient", "refs": []}
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_prompt_injectable_requires_evidence() -> None:
    raw = envelope_base(memory_class="prompt_injectable")
    raw["evidence"] = {"status": "none", "refs": []}
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_critical_cannot_be_prompt_injectable() -> None:
    raw = envelope_base(memory_class="prompt_injectable", severity="critical")
    raw["evidence"] = {"status": "partial", "refs": []}
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_complete_recommended_requires_recommendation() -> None:
    raw = envelope_base(
        signal_type="workflow.complete_recommended",
        signal_kind="workflow.complete_recommended",
        payload={},
    )
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_ttl_skew_is_rejected() -> None:
    raw = envelope_base(ttl_seconds=60)
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_naive_timestamp_is_rejected() -> None:
    raw = envelope_base(created_at="2026-09-08T21:00:00")
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_oversized_tag_is_rejected() -> None:
    raw = envelope_base(tags=["x" * 65])
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_nested_payload_authority_key_is_rejected() -> None:
    raw = envelope_base()
    raw["payload"] = {
        "finding_id": "finding_nested_001",
        "affected_artifacts": [],
        "impact_statement": "nested grant",
        "allowed_tools": ["write_file"],
    }
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_nested_list_payload_authority_key_is_rejected() -> None:
    raw = envelope_base()
    raw["payload"]["notes"] = [{"gate_passed": True}]
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_secret_key_in_payload_is_rejected() -> None:
    raw = envelope_base()
    raw["payload"]["api_key"] = "sk-test"
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_evidence_metadata_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceReference(
            evidence_id="ev_secret_001",
            source_type="workspace_blob",
            source_uri="workspace://root/x",
            content_hash=BLOB_CONTENT_HASH,
            retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            trust_tier="trusted_local",
            metadata={"token": "nvapi-secret"},
        )


def test_naive_retrieved_at_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceReference(
            evidence_id="ev_naive_001",
            source_type="workspace_blob",
            source_uri="workspace://root/x",
            content_hash=BLOB_CONTENT_HASH,
            retrieved_at=datetime(2026, 9, 8),  # noqa: DTZ001 — naivety is the defect under test
            trust_tier="trusted_local",
        )


def test_non_string_mapping_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="keys must be strings"):
        reject_authority_shaped_keys({1: "x"}, location="payload")


def test_hyphenated_authority_key_is_rejected() -> None:
    raw = envelope_base()
    raw["payload"]["Allowed-Tools"] = ["shell"]
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_nested_command_key_is_rejected() -> None:
    raw = envelope_base()
    raw["payload"]["command"] = "pytest -q"
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_scalar_list_payload_is_allowed() -> None:
    reject_authority_shaped_keys(["no", "keys", "here"], location="payload")


def test_tuple_of_authority_dicts_is_rejected() -> None:
    with pytest.raises(ValueError, match="forbidden authority key"):
        reject_authority_shaped_keys(({"retry_limit": 1},), location="payload")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("allowedTools", "allowed_tools"),
        ("AllowedTools", "allowed_tools"),
        ("apiKey", "api_key"),
        ("APIKey", "api_key"),
        ("policyOverride", "policy_override"),
        ("Allowed-Tools", "allowed_tools"),
        ("Allowed--Tools", "allowed_tools"),
        ("_allowedTools_", "allowed_tools"),
        ("allowed_tools", "allowed_tools"),
        ("bearerToken", "bearer_token"),
    ],
)
def test_normalise_key_unifies_camel_and_snake(raw: str, expected: str) -> None:
    assert normalise_key(raw) == expected


def _to_camel(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


@pytest.mark.parametrize("key", sorted(FORBIDDEN_AUTHORITY_KEYS | FORBIDDEN_SECRET_KEYS))
def test_nested_payload_forbidden_key_is_rejected(key: str) -> None:
    raw = envelope_base()
    raw["payload"][key] = True
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


@pytest.mark.parametrize(
    "key",
    sorted(k for k in (FORBIDDEN_AUTHORITY_KEYS | FORBIDDEN_SECRET_KEYS) if "_" in k),
)
def test_camelcase_nested_forbidden_key_is_rejected(key: str) -> None:
    raw = envelope_base()
    raw["payload"][_to_camel(key)] = True
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_unknown_payload_schema_camelcase_authority_key_is_rejected() -> None:
    raw = envelope_base(
        signal_type=SignalKind.RESEARCH_FINDING.value,
        signal_kind=SignalKind.RESEARCH_FINDING.value,
        payload={"allowedTools": ["shell"]},
    )
    with pytest.raises(ValidationError):
        CognitiveSignal.model_validate(raw)


def test_evidence_metadata_camelcase_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceReference(
            evidence_id="ev_camel_001",
            source_type="workspace_blob",
            source_uri="workspace://root/x",
            content_hash=BLOB_CONTENT_HASH,
            retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
            trust_tier="trusted_local",
            metadata={"apiKey": "sk-test"},
        )


def test_evidence_classifiers_are_normalised() -> None:
    ref = EvidenceReference(
        evidence_id="ev_norm_001",
        source_type="GitHub Blob",
        source_uri="workspace://root/x",
        content_hash=BLOB_CONTENT_HASH,
        retrieved_at=datetime(2026, 9, 8, tzinfo=UTC),
        trust_tier="Trusted Local",
    )
    assert ref.source_type == "github_blob"
    assert ref.trust_tier == "trusted_local"


def test_public_models_forbid_extra_and_are_frozen() -> None:
    models = (
        CognitiveSignal,
        CognitiveRecommendation,
        SignalLineage,
        EvidenceReference,
        EvidenceBundle,
        ProposedAction,
        RoutingRecommendationPayload,
        ReviewFindingPayload,
        PiiFindingPayload,
        PlanningProposalPayload,
    )
    for model in models:
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("frozen") is True
