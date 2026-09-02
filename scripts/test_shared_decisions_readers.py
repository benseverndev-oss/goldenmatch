"""Cross-module config-field readers.

The load-bearing test is test_the_incident_pair_is_surfaced: two modules read
BOTH blocking_config.passes and .keys and must agree on precedence. Nothing
checked that they did, and they did not.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared_decisions.readers import field_readers, shared_fields  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "incident_1c843c8a5"
REPO = Path(__file__).resolve().parent.parent
GM = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"


def test_the_incident_pair_is_surfaced():
    """EXIT CRITERION. Both fixture modules read `passes` and `keys`; the scan
    must report both fields as read by more than one module."""
    shared = shared_fields(FIXTURES)
    for field in ("passes", "keys"):
        assert field in shared, f"{field} not reported as shared: {sorted(shared)}"
        assert len(shared[field]) >= 2, f"{field} readers: {shared[field]}"
    both = {m for m in shared["passes"] if m in shared["keys"]}
    assert len(both) >= 2, f"expected both fixture modules to read both fields, got {both}"


def test_a_field_read_by_one_module_is_not_shared():
    readers = field_readers(FIXTURES)
    shared = shared_fields(FIXTURES)
    single = {f for f, mods in readers.items() if len(mods) == 1}
    assert single, "fixture has no single-reader field; test cannot witness the filter"
    assert not (single & set(shared)), (
        f"single-reader fields leaked into shared: {single & set(shared)}"
    )


def test_the_real_package_scan_is_not_empty():
    """A wrong root or a parse failure yields an empty dict that reads as clean."""
    shared = shared_fields(GM)
    assert len(shared) >= 5, f"only {len(shared)} shared fields found in goldenmatch"


def test_scan_reports_module_paths_not_absolute():
    shared = shared_fields(FIXTURES)
    for mods in shared.values():
        for m in mods:
            assert not Path(m).is_absolute(), m


def test_non_config_named_bases_are_not_missed():
    """The "config"/"cfg"-substring rule alone misses readers whose base is
    named after the config *type* it holds rather than containing "config" --
    e.g. `blocking.passes` (base `blocking`, prefix of `BlockingConfig`). These
    three modules were silently absent from the real-package scan until the
    base-name rule was widened to also match a class-name prefix; naming them
    explicitly means a future narrowing says which one vanished, not just that
    the count dropped."""
    readers = field_readers(GM)
    both = {m for m in readers.get("passes", set()) if m in readers.get("keys", set())}
    for expected in (
        "core/autoconfig_verify.py",
        "distributed/scoring.py",
        "identity/block_index.py",
    ):
        assert expected in both, f"{expected} missing from passes+keys readers: {sorted(both)}"


def test_at_least_nine_modules_read_both_passes_and_keys():
    """Lower bound on the real package, not an exact count -- the incident
    field pair (`passes`/`keys`) is read by every blocking-strategy consumer,
    and a narrowing rule should not be able to silently drop below what's
    already known to be there. 9 = the 8 found once bare-Name class-prefix
    matching was added, plus core/fused_match.py once attribute-chain bases
    (`config.blocking.passes`) were handled."""
    readers = field_readers(GM)
    both = {m for m in readers.get("passes", set()) if m in readers.get("keys", set())}
    assert len(both) >= 9, f"only {len(both)} modules read both passes and keys: {sorted(both)}"


def test_fused_match_is_a_reader_of_both_passes_and_keys():
    """core/fused_match.py is a shipping scoring backend that reads BOTH
    incident fields via `config.blocking.keys`/`config.blocking.passes` --
    an attribute-chain base (`config.blocking`), not a bare Name. It was
    silently absent from the scan until attribute chains were walked; named
    explicitly so a future narrowing says this exact module vanished."""
    readers = field_readers(GM)
    both = {m for m in readers.get("passes", set()) if m in readers.get("keys", set())}
    assert "core/fused_match.py" in both, f"fused_match.py missing from: {sorted(both)}"


def test_single_letter_bases_do_not_falsely_match_a_config_class_prefix():
    """A base name that merely PREFIXES a config class name (`c` prefixes
    `CanopyConfig`, `f` prefixes `FieldTransform`) must not count as a config
    read -- `c` in cli/memory.py:253 (`for c in corrections:`) is a
    Correction record, not a CanopyConfig, and its `.trust` read is unrelated
    to any config field. The word-boundary rule (equality, not prefix)
    rejects it; a looser prefix rule previously let it through."""
    readers = field_readers(GM)
    assert "cli/memory.py" not in readers.get(
        "trust", set()
    ), f"cli/memory.py falsely counted as a 'trust' reader: {readers.get('trust', set())}"
