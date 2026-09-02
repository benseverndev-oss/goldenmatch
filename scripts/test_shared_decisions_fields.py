"""Config-model field enumeration.

The shared-decision scan needs to know which attribute names are CONFIG fields.
Scanning every attribute access in the repo would drown in `self.x` noise, so
the field set is derived from the Pydantic models themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.fields import config_fields  # noqa: E402


def test_blocking_config_fields_are_found():
    """BlockingConfig is the model behind the incident this engine must catch."""
    fields = config_fields()
    assert "BlockingConfig" in fields, sorted(fields)[:20]
    assert {"passes", "keys", "strategy"} <= fields["BlockingConfig"]


def test_a_plausible_number_of_models_is_found():
    """A parse failure or a wrong path yields a near-empty dict that would make
    every downstream result vacuously clean. (Measured at implementation: 41)"""
    fields = config_fields()
    assert len(fields) >= 30, f"only {len(fields)} config models found (was 41)"


def test_known_models_are_present():
    """Parser regression test: named models must be present with plausible field
    counts. Failure names the missing or emptied model."""
    fields = config_fields()
    required_models = {
        "BlockingConfig": 25,
        "GoldenMatchConfig": 29,
        "IdentityConfig": 16,
        "MatchkeyConfig": 18,
    }
    for model, expected_field_count in required_models.items():
        assert model in fields, f"model {model!r} not found; available: {sorted(fields)[:20]}"
        actual_count = len(fields[model])
        assert actual_count > 0, f"model {model!r} has no fields"
        assert actual_count == expected_field_count, (
            f"model {model!r}: expected {expected_field_count} fields, got {actual_count}"
        )
