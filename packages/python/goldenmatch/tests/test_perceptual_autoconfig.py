"""Perceptual media-as-evidence auto-config (ADR 0022, slice 3b).

The detector + config builder that turn a fixed-width-hex perceptual-hash column
into a ``phash`` / ``audio_fp`` matchkey (+ perceptual blocking for image) with
zero manual config, behind the ``GOLDENMATCH_PERCEPTUAL_AUTOCONFIG`` gate.
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core import perceptual
from goldenmatch.core.perceptual_autoconfig import (
    apply_perceptual_autoconfig,
    build_perceptual_matchkey,
    detect_perceptual_hash_columns,
)

try:
    import polars as pl
except ImportError:  # pragma: no cover
    pl = None


def _img_col(n: int) -> list[str]:
    return [perceptual.phash_hex(0x0123456789ABCDEF ^ i) for i in range(n)]


def _audio_col(n: int) -> list[str]:
    return [perceptual.audio_fp_hex([i, i + 1, i + 2, i + 3]) for i in range(n)]  # 4 words


def test_detect_image_and_audio_columns():
    df = pl.DataFrame({"name": ["a", "b", "c", "d"], "ph": _img_col(4), "fp": _audio_col(4)})
    det = dict(detect_perceptual_hash_columns(df))
    assert det.get("ph") == "image"
    assert det.get("fp") == "audio"
    assert "name" not in det  # plain text is not a hash column


def test_detect_ignores_internal_and_short_columns():
    df = pl.DataFrame({"__row_id__": _img_col(3), "ph": _img_col(3)})
    det = dict(detect_perceptual_hash_columns(df))
    assert "__row_id__" not in det  # internal columns skipped
    assert det.get("ph") == "image"


def test_build_perceptual_matchkey_shapes():
    mk = build_perceptual_matchkey("ph", "image")
    assert mk.type == "weighted" and mk.threshold == 0.85
    assert mk.fields[0].scorer == "phash" and mk.fields[0].weight == 1.0
    mka = build_perceptual_matchkey("fp", "audio")
    assert mka.fields[0].scorer == "audio_fp" and mka.threshold == 0.80


def test_apply_appends_matchkey_and_sets_blocking_when_empty():
    df = pl.DataFrame({"ph": _img_col(4)})
    out = apply_perceptual_autoconfig(GoldenMatchConfig(), df)
    names = [mk.name for mk in out.get_matchkeys()]
    assert "perceptual_image_ph" in names
    assert out.blocking is not None and out.blocking.strategy == "perceptual"
    assert out.blocking.perceptual is not None and out.blocking.perceptual.column == "ph"


def test_apply_preserves_existing_blocking():
    df = pl.DataFrame({"ph": _img_col(4), "name": ["a", "b", "c", "d"]})
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(name="exact_name", type="exact", fields=[MatchkeyField(field="name")])
        ],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["name"])]),
    )
    out = apply_perceptual_autoconfig(cfg, df)
    assert "perceptual_image_ph" in [mk.name for mk in out.get_matchkeys()]
    assert out.blocking.strategy == "static"  # real blocking left intact


def test_apply_is_idempotent():
    df = pl.DataFrame({"ph": _img_col(4)})
    cfg = apply_perceptual_autoconfig(GoldenMatchConfig(), df)
    n1 = len(cfg.get_matchkeys())
    apply_perceptual_autoconfig(cfg, df)  # column already a matchkey field -> skipped
    assert len(cfg.get_matchkeys()) == n1


def test_apply_noop_without_media_columns():
    df = pl.DataFrame({"name": ["alice", "bob"], "city": ["nyc", "sf"]})
    cfg = GoldenMatchConfig()
    out = apply_perceptual_autoconfig(cfg, df)
    assert out.get_matchkeys() == [] and out.blocking is None
