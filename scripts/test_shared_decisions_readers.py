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
