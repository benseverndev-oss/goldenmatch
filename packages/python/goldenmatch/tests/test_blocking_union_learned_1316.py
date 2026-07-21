"""#1316: reconcile the #1207 strong-identifier blocking UNION with the >=50K
learned-blocking upgrade.

`_legacy_auto_configure_v0` forces `blocking.strategy = "learned"` at
`total_rows >= 50_000`, which (pre-fix) OVERWROTE the #1207 per-identifier
union. Measured on the #1207 null-sparse multi-source shape at 50K, learned
blocking under-blocks catastrophically -- candidate-pair recall collapses from
1.0 (union) to 0.0 (the learner trains on a <=5K sample, finds no pairs above
its recall threshold, falls back to one column, and skip_oversized then drops
every giant block). So the >=50K gate now KEEPS a strong-id union whose
OR-coverage still clears the target, and only forces learned otherwise.

These tests use a small representative frame with an `n_rows_full` override so
the >=50K branch fires without materializing 50K rows.
"""
from __future__ import annotations

from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    _is_strong_identifier_union,
    _legacy_auto_configure_v0,
    profile_columns,
)

from tests.test_autoconfig_blocking_union_1207 import _null_sparse_person_df

_LARGE = 60_000  # >= the 50K learned-blocking gate


# ── unit: the union detector ───────────────────────────────────────────────

def test_is_strong_identifier_union_detects_the_1207_shape():
    df = _null_sparse_person_df(n=4000)
    profiles = profile_columns(df)
    union = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["npi"], transforms=["strip"])],
        passes=[
            BlockingKeyConfig(fields=["npi"], transforms=["strip"]),
            BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"]),
            BlockingKeyConfig(fields=["first_name", "last_name"], transforms=["strip"]),
        ],
    )
    assert _is_strong_identifier_union(union, profiles) is True


def test_is_strong_identifier_union_rejects_non_union_multipass():
    df = _null_sparse_person_df(n=4000)
    profiles = profile_columns(df)
    # multi_pass over name/compound passes only -- no single strong-id pass.
    name_only = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=["strip"])],
        passes=[
            BlockingKeyConfig(fields=["last_name", "first_name"], transforms=["strip"]),
            BlockingKeyConfig(fields=["last_name"], transforms=["strip"]),
        ],
    )
    assert _is_strong_identifier_union(name_only, profiles) is False


def test_is_strong_identifier_union_rejects_static_and_single_pass():
    df = _null_sparse_person_df(n=4000)
    profiles = profile_columns(df)
    static = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["npi"], transforms=["strip"])],
    )
    assert _is_strong_identifier_union(static, profiles) is False
    one_pass = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["npi"], transforms=["strip"])],
        passes=[BlockingKeyConfig(fields=["npi"], transforms=["strip"])],
    )
    assert _is_strong_identifier_union(one_pass, profiles) is False


# ── integration: the >=50K gate keeps the union, else forces learned ───────

def test_union_is_kept_at_scale_not_overwritten_by_learned():
    """The #1316 fix: the null-sparse strong-id union survives the >=50K gate."""
    df = _null_sparse_person_df(n=6000)
    cfg = _legacy_auto_configure_v0(df, n_rows_full=_LARGE)
    b = cfg.blocking
    assert b is not None
    assert b.strategy == "multi_pass", (
        f"expected the union to be kept, got strategy={b.strategy!r}"
    )
    # the strong-id single-field passes are still present (recall-critical)
    fieldsets = {tuple(p.fields) for p in (b.passes or [])}
    assert ("npi",) in fieldsets
    assert ("email",) in fieldsets


def test_non_union_blocking_still_upgrades_to_learned(monkeypatch):
    """The >=50K learned upgrade still fires for a NON-union blocking config --
    the #1316 guard is conditional on the union, not a blanket disable.

    We drive the null-sparse shape (which routes through the deterministic
    weighted path that reaches the >=50K gate), but force `build_blocking` to
    return a plain static config so the gate must fall through to learned.
    """
    df = _null_sparse_person_df(n=6000)
    static_cfg = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=["strip"])],
    )
    monkeypatch.setattr(
        "goldenmatch.core.autoconfig.build_blocking", lambda *a, **k: static_cfg
    )

    cfg = _legacy_auto_configure_v0(df, n_rows_full=_LARGE)
    b = cfg.blocking
    assert b is not None
    assert not _is_strong_identifier_union(b, profile_columns(df))
    assert b.strategy == "learned", (
        f"expected learned blocking for a non-union config, got {b.strategy!r}"
    )
