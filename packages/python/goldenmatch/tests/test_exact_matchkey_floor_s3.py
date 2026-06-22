"""S3 (spec 2026-06-22-autoconfig-smarter-faster-s1-s3): per-type exact-matchkey
cardinality floor. Closes the standing TODO at autoconfig.py (issue #715).

`exact_matchkey_floor(col_type)`: email 0.70, phone 0.30, else 0.50. An exact
matchkey on a column is only allowed when its cardinality_ratio >= the per-type
floor, so emails demand near-uniqueness while phones (legitimately shared) get a
lower bar.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _exact_matchkey_floor_py,
    build_matchkeys,
    exact_matchkey_floor,
)


def test_floor_per_type():
    assert _exact_matchkey_floor_py("email") == 0.70
    assert _exact_matchkey_floor_py("phone") == 0.30
    assert _exact_matchkey_floor_py("name") == 0.50
    assert _exact_matchkey_floor_py("string") == 0.50
    assert exact_matchkey_floor("email") == 0.70


def test_floor_unknown_defaults_to_half():
    assert _exact_matchkey_floor_py("identifier") == 0.50
    assert _exact_matchkey_floor_py("multi_name") == 0.50
    assert _exact_matchkey_floor_py("totally_unknown") == 0.50
    assert _exact_matchkey_floor_py("") == 0.50


def _profile(name: str, col_type: str, card: float) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        dtype="str",
        col_type=col_type,
        confidence=0.9,
        sample_values=["a", "b", "c"],
        null_rate=0.0,
        cardinality_ratio=card,
        avg_len=8.0,
    )


def _exact_matchkey_cols(profiles: list[ColumnProfile]) -> set[str]:
    """Names of columns that became EXACT matchkeys."""
    cfg = build_matchkeys(profiles)
    cols: set[str] = set()
    for mk in cfg:
        if mk.type == "exact":
            for f in mk.fields:
                if f.field is not None:
                    cols.add(f.field)
    return cols


def test_phone_at_0_4_now_backs_exact_matchkey():
    # cardinality 0.4: below the old blanket 0.5 (would have been rejected),
    # but >= the per-type phone floor 0.30 -> now accepted.
    cols = _exact_matchkey_cols([_profile("phone", "phone", 0.4)])
    assert "phone" in cols


def test_email_at_0_6_now_rejected():
    # cardinality 0.6: above the old blanket 0.5 (would have been accepted),
    # but below the per-type email floor 0.70 -> now rejected.
    cols = _exact_matchkey_cols([_profile("email", "email", 0.6)])
    assert "email" not in cols


def test_default_floor_unchanged_for_non_tuned_types():
    # Non-email/phone types keep the historical 0.50 floor: the kernel returns
    # 0.50 so their gate is byte-identical to the old blanket behavior. (Whether
    # such a column becomes an exact matchkey at all is governed by scorer
    # selection, not this floor -- S3 only changes the per-type threshold.)
    for ct in ("name", "string", "identifier", "numeric", "date", "year"):
        assert exact_matchkey_floor(ct) == 0.50
