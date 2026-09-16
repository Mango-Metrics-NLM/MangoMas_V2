"""Cognitive-envelope emission settings (``MANGOMAS_SIGNAL__*``).

Distinct from :class:`~mangomas.config.harness.HarnessSettings`
(``MANGOMAS_HARNESS__*``), which is Claude Code governance plus an OTel wrap.
This group only controls whether Mango-Mas *emits* ``CognitiveSignal``
records. It never grants tools, models, or timeouts.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_SIGNAL_ENABLED: bool = False


DEFAULT_SIGNAL_DIR: str = "./data/cognitive-signals"


# Live envelope is mango-integration-contracts 1.1.0. A 1.0.0 override is
# rejected at Settings parse (fail-closed); do not silently coerce.
DEFAULT_SIGNAL_SCHEMA_VERSION: Literal["1.1.0"] = "1.1.0"


DEFAULT_SIGNAL_GENAI_SPANS: bool = False


# Envelope lifetime, in seconds. Mirrors mango_contracts' DEFAULT_TTL_SECONDS /
# MAX_TTL_SECONDS rather than importing them: this module is constructed on
# every Settings build, including when MANGOMAS_SIGNAL__ENABLED is false, and
# importing the contracts package here would break the flag-off guarantee that
# nothing loads it. `tests/cognitive/test_replay_resistance.py` pins these
# against the real envelope so the mirror cannot drift.
DEFAULT_SIGNAL_TTL_SECONDS: int = 60 * 60 * 24


MAX_SIGNAL_TTL_SECONDS: int = 60 * 60 * 24 * 30


DEFAULT_SIGNAL_POLICY_ID: str = "mangomas.cognitive.default"


DEFAULT_SIGNAL_POLICY_VERSION: str = "1"


def _prefixed_sha256(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def policy_snapshot_hash_for(policy_id: str, policy_version: str) -> str:
    """Digest ``id:version`` with the ``sha256:`` prefix the envelope requires.

    **This is a provenance label, not an attestation.** It is a checksum of two
    environment variables, not a digest of a policy document: it proves nothing
    about policy *content*, it cannot detect a changed rule, and the emitting
    process computes it itself. ``MANGOMAS_SIGNAL__POLICY_SNAPSHOT_HASH`` is
    also operator-settable to any 64-hex value, so a signal can claim any
    snapshot.

    A verifier that reads ``policy_snapshot_hash`` as "this signal was produced
    under approved policy P" is trusting a self-signed assertion. Read it as
    "the producer said it was running policy P" — useful for correlating and
    grouping, worthless as authorization. Making it real means digesting an
    actual policy document; that is deferred until a policy document exists to
    digest (ADR-0032, ADR-0033).
    """
    return _prefixed_sha256(f"{policy_id}:{policy_version}")


DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH: str = policy_snapshot_hash_for(
    DEFAULT_SIGNAL_POLICY_ID, DEFAULT_SIGNAL_POLICY_VERSION
)


DEFAULT_SIGNAL_HTTP_URL: str | None = None


DEFAULT_SIGNAL_HTTP_TIMEOUT_SECONDS: float = 5.0


class SignalSettings(BaseModel):
    """Opt-in CognitiveSignal emission (spec-0030 / ADR-0029).

    Default-OFF so an environment without ``MANGOMAS_SIGNAL__*`` is
    byte-identical to today: no sink is attached, agents do not import the
    contracts package on the handle path, and no JSONL is written.
    """

    enabled: bool = DEFAULT_SIGNAL_ENABLED
    dir: str = DEFAULT_SIGNAL_DIR
    schema_version: Literal["1.1.0"] = DEFAULT_SIGNAL_SCHEMA_VERSION
    genai_spans: bool = DEFAULT_SIGNAL_GENAI_SPANS
    ttl_seconds: int = Field(
        default=DEFAULT_SIGNAL_TTL_SECONDS,
        ge=1,
        le=MAX_SIGNAL_TTL_SECONDS,
        description=(
            "How long an emitted envelope stays valid. Sinks refuse an expired "
            "signal (ADR-0032), so this is the window a consumer may treat a "
            "signal as current."
        ),
    )
    policy_id: str = DEFAULT_SIGNAL_POLICY_ID
    policy_version: str = DEFAULT_SIGNAL_POLICY_VERSION
    policy_snapshot_hash: str = Field(
        default=DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH,
        pattern=r"^sha256:[a-f0-9]{64}$",
    )
    http_url: str | None = DEFAULT_SIGNAL_HTTP_URL
    http_timeout_seconds: float = Field(
        default=DEFAULT_SIGNAL_HTTP_TIMEOUT_SECONDS,
        gt=0.0,
    )

    @model_validator(mode="after")
    def _rebind_default_policy_hash(self) -> SignalSettings:
        """Keep the default hash bound to the stated policy id/version.

        An operator who sets only ``MANGOMAS_SIGNAL__POLICY_ID`` must not keep
        the snapshot hash of ``mangomas.cognitive.default:1``. An explicit
        ``policy_snapshot_hash`` is left alone — including when it equals the
        current default — because it may pin a prior snapshot.
        """
        if "policy_snapshot_hash" in self.model_fields_set:
            return self
        rebound = policy_snapshot_hash_for(self.policy_id, self.policy_version)
        if rebound != self.policy_snapshot_hash:
            # Mutate in place: returning model_copy from a top-level
            # ``mode="after"`` validator is ignored on ``__init__``.
            self.policy_snapshot_hash = rebound
        return self
