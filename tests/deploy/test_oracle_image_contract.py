"""Contract tests tying Makefile ``ORACLE_IMAGE`` to ``RepairSettings`` (spec-0044 / ADR-0040)."""

from __future__ import annotations

from mangomas.config import DEFAULT_REPAIR_ORACLE_IMAGE, RepairSettings
from tests.deploy import _makefile


def test_makefile_oracle_image_matches_repair_settings_default() -> None:
    """The image ``make oracle-image`` builds must equal ``RepairSettings.oracle_image``."""
    makefile_tag = _makefile.variable("ORACLE_IMAGE")
    assert makefile_tag == DEFAULT_REPAIR_ORACLE_IMAGE
    assert RepairSettings().oracle_image == makefile_tag


def test_oracle_image_target_builds_dev_extra_into_oracle_image() -> None:
    """``oracle-image`` must pass ``MANGOMAS_EXTRAS=dev`` so ``pytest`` is installed."""
    body = _makefile.target_body("oracle-image")
    assert "--build-arg MANGOMAS_EXTRAS=dev" in body
    assert '-t "$(ORACLE_IMAGE)"' in body
