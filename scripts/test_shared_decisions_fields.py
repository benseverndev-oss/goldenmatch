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
    every downstream result vacuously clean."""
    fields = config_fields()
    assert len(fields) >= 10, f"only {len(fields)} config models found"


def test_every_model_has_at_least_one_field():
    fields = config_fields()
    empty = [k for k, v in fields.items() if not v]
    assert not empty, f"models parsed with no fields: {empty}"
