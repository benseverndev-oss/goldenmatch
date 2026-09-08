"""`_record_id_candidates` must accept a precomputed payload/hash and produce
byte-identical ids (ADR 0064 follow-up).

`apply_batch`'s per-row derivation loop needs `_row_to_payload` and
`_hash_payload` for `rowid_to_payload` / `rowid_to_hash` regardless, and
`_record_id_candidates` was recomputing BOTH internally on the no-PK path --
2x the payload build and 2x the json.dumps+sha256 for every row. Measured at
5.86 us/row of pure duplicate work (~29s at 5M), and it persisted even on the
batched-h1 path, since only the fingerprint was being skipped.

These lock the two things that make hoisting safe: the ids are identical
either way, and the helpers really are called once per row rather than twice.
"""

from __future__ import annotations

import pytest
from goldenmatch.identity import resolve as R

_ROWS = [
    {"__row_id__": 0, "id": "1", "first_name": "Ann", "last_name": "Lee", "zip": "10001"},
    {"__row_id__": 1, "id": "2", "first_name": "Bob", "last_name": "Ray", "zip": None},
    {"__row_id__": 2, "id": "3", "first_name": "", "last_name": "Ng", "zip": "94105"},
]


@pytest.mark.parametrize("pk_col", [None, "id"], ids=["no-pk", "pk"])
def test_precomputed_payload_is_byte_identical(pk_col):
    """Both the content-hash path and the natural-PK early return."""
    for row in _ROWS:
        without = R._record_id_candidates(row, "src", pk_col)
        payload = R._row_to_payload(row)
        with_ = R._record_id_candidates(
            row, "src", pk_col, payload=payload, payload_hash=R._hash_payload(payload)
        )
        assert without == with_, row


def test_precomputed_payload_composes_with_batched_h1():
    """The two optimisations are independent: supplying both must still match
    supplying only the batched hash."""
    for row in _ROWS:
        h1 = "a" * 64
        only_h1 = R._record_id_candidates(row, "src", None, precomputed_h1=h1)
        payload = R._row_to_payload(row)
        both = R._record_id_candidates(
            row, "src", None, precomputed_h1=h1,
            payload=payload, payload_hash=R._hash_payload(payload),
        )
        assert only_h1 == both, row


def test_helpers_are_called_once_per_row_not_twice(monkeypatch):
    """The regression guard. A correctness-only test passes just as happily if
    someone reinstates the internal recomputation, which is the actual defect."""
    calls = {"payload": 0, "hash": 0}
    real_payload, real_hash = R._row_to_payload, R._hash_payload

    monkeypatch.setattr(R, "_row_to_payload",
                        lambda r: (calls.__setitem__("payload", calls["payload"] + 1),
                                   real_payload(r))[1])
    monkeypatch.setattr(R, "_hash_payload",
                        lambda p: (calls.__setitem__("hash", calls["hash"] + 1),
                                   real_hash(p))[1])

    for row in _ROWS:
        payload = R._row_to_payload(row)
        phash = R._hash_payload(payload)
        R._record_id_candidates(row, "src", None, payload=payload, payload_hash=phash)

    n = len(_ROWS)
    assert calls["payload"] == n, f"expected {n} payload builds, got {calls['payload']}"
    assert calls["hash"] == n, f"expected {n} hashes, got {calls['hash']}"
