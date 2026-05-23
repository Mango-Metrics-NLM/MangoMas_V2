"""Live GCP Secret Manager integration test.

Gated by ``RUN_GCP_SECRETS=1`` AND the ``@pytest.mark.gcp_secrets``
marker. Requires:

- ``google-cloud-secret-manager`` + ``google-auth`` installed
  (``pip install -e ".[gcp]"``)
- ``gcloud auth application-default login`` run (or running on
  Workload-Identity-Federation-enabled compute)
- ``GCP_SECRETS_PROJECT`` env var pointing at a real GCP project
- ``GCP_SECRETS_SECRET_NAME`` env var naming a pre-seeded secret
"""

from __future__ import annotations

import os

import pytest
from tests.constants import GCP_SECRETS_PROJECT_ENV, GCP_SECRETS_SECRET_NAME_ENV

from mangomas.config import DEFAULT_GCP_SECRET_VERSION, DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS
from mangomas.secrets.gcp import GCPSecretManagerProvider

pytestmark = pytest.mark.gcp_secrets


def test_resolves_pre_seeded_secret() -> None:
    project = os.environ.get(GCP_SECRETS_PROJECT_ENV)
    secret_name = os.environ.get(GCP_SECRETS_SECRET_NAME_ENV)
    if not project or not secret_name:
        pytest.skip(
            f"set {GCP_SECRETS_PROJECT_ENV} and {GCP_SECRETS_SECRET_NAME_ENV} "
            "to exercise the live GCP Secret Manager"
        )

    provider = GCPSecretManagerProvider(
        project_id=project,
        timeout_seconds=DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS,
        default_version=DEFAULT_GCP_SECRET_VERSION,
    )
    value = provider.get(secret_name)
    assert value is not None, "live secret resolution returned None"
    assert isinstance(value, str)
    assert value  # non-empty
