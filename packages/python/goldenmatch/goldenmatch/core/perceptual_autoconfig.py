"""Perceptual media-as-evidence auto-config (ADR 0022, slice 3b).

Detect columns that hold fixed-width hex perceptual hashes -- a 16-char image
pHash (``core.perceptual.phash_hex``) or a multiple-of-8-char audio fingerprint
(``core.perceptual.audio_fp_hex``) -- and wire the matching media comparator
(``phash`` / ``audio_fp``) plus, for image, perceptual LSH blocking, so a media
column becomes a match feature with zero manual config ("modality as evidence").

Gated OFF by default: ``auto_configure_df`` calls :func:`apply_perceptual_autoconfig`
only under ``GOLDENMATCH_PERCEPTUAL_AUTOCONFIG=1`` (byte-identical when off). The
helpers are also usable directly to build a config for a known media column.
"""
from __future__ import annotations

import re

import polars as pl

from goldenmatch.config.schemas import (
    BlockingConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    PerceptualKeyConfig,
)

# A 16-char (64-bit) image pHash. An audio fingerprint is two or more 8-char
# (32-bit) words concatenated -- a 16-char string is ambiguous, so it is read as
# an image pHash (the common fixed-width case) and audio requires >= 3 words.
_IMAGE_RE = re.compile(r"^[0-9a-f]{16}$")
_AUDIO_RE = re.compile(r"^(?:[0-9a-f]{8}){3,}$")

_SAMPLE = 200
_IMAGE_THRESHOLD = 0.85  # hamming <= ~10/64
_AUDIO_THRESHOLD = 0.80


def _classify(values: list[str]) -> str | None:
    """``"image"`` / ``"audio"`` if every value is that fixed-width hex hash, else None."""
    img = aud = True
    for v in values:
        if not _IMAGE_RE.match(v):
            img = False
        if not _AUDIO_RE.match(v):
            aud = False
        if not img and not aud:
            return None
    if img:
        return "image"
    if aud:
        return "audio"
    return None


def detect_perceptual_hash_columns(
    df: pl.DataFrame, sample: int = _SAMPLE
) -> list[tuple[str, str]]:
    """Return ``(column, kind)`` (kind in ``{"image", "audio"}``) for each column
    whose non-null string values are all fixed-width hex perceptual hashes."""
    out: list[tuple[str, str]] = []
    for col in df.columns:
        if col.startswith("__"):
            continue
        try:
            raw = df[col].cast(pl.Utf8).drop_nulls().head(sample).to_list()
        except Exception:  # noqa: BLE001 - non-castable column: not a hash column
            continue
        vals = [v.strip().lower() for v in raw if v is not None and v.strip()]
        if len(vals) < 2:
            continue
        kind = _classify(vals)
        if kind is not None:
            out.append((col, kind))
    return out


def build_perceptual_matchkey(column: str, kind: str) -> MatchkeyConfig:
    """A single-field weighted matchkey scoring ``column`` with the media comparator."""
    scorer = "phash" if kind == "image" else "audio_fp"
    threshold = _IMAGE_THRESHOLD if kind == "image" else _AUDIO_THRESHOLD
    return MatchkeyConfig(
        name=f"perceptual_{kind}_{column}",
        type="weighted",
        threshold=threshold,
        fields=[MatchkeyField(field=column, scorer=scorer, weight=1.0)],
    )


def apply_perceptual_autoconfig(
    config: GoldenMatchConfig, df: pl.DataFrame
) -> GoldenMatchConfig:
    """Append perceptual matchkeys for detected media-hash columns, and set
    perceptual blocking on an image column when the committed config has no other
    blocking. Additive + idempotent: a column already used as a matchkey field is
    skipped, so re-running never duplicates."""
    detected = detect_perceptual_hash_columns(df)
    if not detected:
        return config

    existing_fields = {f.field for mk in config.get_matchkeys() for f in mk.fields if f.field}
    matchkeys = list(config.get_matchkeys())
    image_cols: list[str] = []
    added = False
    for col, kind in detected:
        if col in existing_fields:
            continue
        matchkeys.append(build_perceptual_matchkey(col, kind))
        added = True
        if kind == "image":
            image_cols.append(col)

    if added:
        if config.matchkeys is not None:
            config.matchkeys = matchkeys
        elif config.match_settings is not None:
            config.match_settings.matchkeys = matchkeys
        else:
            config.matchkeys = matchkeys

    # Perceptual LSH blocking when the committed config has nothing else to block
    # on (a pure media-dedup): otherwise leave the chosen blocking strategy intact.
    blk = config.blocking
    no_blocking = blk is None or (
        blk.strategy == "static" and not blk.keys and not (blk.passes or [])
    )
    if image_cols and no_blocking:
        config.blocking = BlockingConfig(
            strategy="perceptual", perceptual=PerceptualKeyConfig(column=image_cols[0])
        )
    return config
