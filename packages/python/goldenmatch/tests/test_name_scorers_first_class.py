"""The reference-data name scorers are first-class VALID_SCORERS (TS parity).

`name_freq_weighted_jw` / `given_name_aliased_jw` used to be accepted only via the
PluginRegistry validator fallback (registered by importing `goldenmatch.refdata`),
so they were NOT in `VALID_SCORERS` and the `scorers` parity surface listed them
TS-only. They're now first-class: `import goldenmatch` registers them (via
`_api` -> `refdata`), so a config referencing them validates + scores with no
manual `import goldenmatch.refdata`.
"""
from __future__ import annotations

import subprocess
import sys

from goldenmatch.config.schemas import VALID_SCORERS, MatchkeyField
from goldenmatch.core.scorer import score_field

_NAME_SCORERS = ("name_freq_weighted_jw", "given_name_aliased_jw")


def test_name_scorers_in_valid_scorers():
    for s in _NAME_SCORERS:
        assert s in VALID_SCORERS, f"{s} should be first-class in VALID_SCORERS"


def test_matchkey_field_validates_name_scorers():
    for s in _NAME_SCORERS:
        field = MatchkeyField(
            field="name", transforms=["lowercase"], scorer=s, weight=1.0
        )
        assert field.scorer == s


def test_name_scorers_score_end_to_end():
    # given_name_aliased_jw canonicalizes nickname aliases to an exact 1.0.
    assert score_field("Bob", "Robert", "given_name_aliased_jw") == 1.0
    # name_freq_weighted_jw is a Jaro-Winkler variant; identical strings -> 1.0.
    assert score_field("Smith", "Smith", "name_freq_weighted_jw") == 1.0


def test_bare_import_goldenmatch_registers_scorers():
    """A fresh process importing ONLY `goldenmatch` can validate + score with the
    name scorers — proving the promotion isn't a validate-but-crash footgun."""
    code = (
        "import goldenmatch\n"
        "from goldenmatch.config.schemas import MatchkeyField\n"
        "from goldenmatch.core.scorer import score_field\n"
        "MatchkeyField(field='name', transforms=['lowercase'],"
        " scorer='name_freq_weighted_jw', weight=1.0)\n"
        "assert score_field('Bob', 'Robert', 'given_name_aliased_jw') == 1.0\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
