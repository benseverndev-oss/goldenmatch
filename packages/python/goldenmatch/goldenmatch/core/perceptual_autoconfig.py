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

from goldenmatch._polars_lazy import pl

from goldenmatch.config.schemas import (
    BlockingConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    PerceptualKeyConfig,
)
from goldenmatch.core.perceptual_blocker import recommend_num_bands

# A 16-char (64-bit) image pHash. An audio fingerprint is two or more 8-char
# (32-bit) words concatenated -- a 16-char string is ambiguous, so it is read as
# an image pHash (the common fixed-width case) and audio requires >= 3 words.
# A radial-variance profile is exactly 96 hex chars (RADIAL_ANGLES=48 int8 bins,
# ADR 0022 finding 1). 96 is also a valid audio length (12 words), so a column of
# values that are ALL exactly 96 chars is read as radial (audio fingerprints vary
# in length across records; a uniform-96 column is the geometric profile).
_IMAGE_RE = re.compile(r"^[0-9a-f]{16}$")
_RADIAL_RE = re.compile(r"^[0-9a-f]{96}$")
_AUDIO_RE = re.compile(r"^(?:[0-9a-f]{8}){3,}$")

_SAMPLE = 200
_IMAGE_THRESHOLD = 0.85  # hamming <= ~10/64
# Canonical Haitsma-Kalker match threshold: BER <= 0.35 (similarity >= 0.65). The
# prior 0.80 (BER <= 0.20) missed moderate additive noise; on realistic broadband
# audio, noisy matches sit at ~0.69-0.86 similarity vs ~0.49 for unrelated, so 0.65
# recovers noise robustness with a safe precision margin (ADR 0022 finding 3). The
# earlier "noise kills it / lowering is harmful" reading was a pure-tone artifact --
# tones leave most log-bands near-empty, making the sign bit pure noise.
_AUDIO_THRESHOLD = 0.65
_RADIAL_THRESHOLD = 0.85  # bench operating point (P=1.0, R=0.99 at 0.85)
# Blocking must recall the same near-duplicate radius the scorer accepts, so the
# band count is derived from the image threshold rather than hardcoded (the old
# default of 8 under-recalled at 0.72; the recall-target rule picks 16 at 0.97).
_BLOCK_RECALL_TARGET = 0.95
_HASH_BITS = 64


def _classify(values: list[str]) -> str | None:
    """``"image"`` / ``"radial"`` / ``"audio"`` if every value is that hex form, else None.

    A uniform-96-char column is read as ``radial`` even though 96 chars is also a
    valid audio length -- audio fingerprints vary in length across records, so a
    column that is *always* exactly 96 is the geometric profile, not audio."""
    img = rad = aud = True
    for v in values:
        if not _IMAGE_RE.match(v):
            img = False
        if not _RADIAL_RE.match(v):
            rad = False
        if not _AUDIO_RE.match(v):
            aud = False
        if not img and not rad and not aud:
            return None
    if img:
        return "image"
    if rad:
        return "radial"
    if aud:
        return "audio"
    return None


def detect_perceptual_hash_columns(
    df: pl.DataFrame, sample: int = _SAMPLE
) -> list[tuple[str, str]]:
    """Return ``(column, kind)`` (kind in ``{"image", "radial", "audio"}``) for each
    column whose non-null string values are all fixed-width hex perceptual hashes."""
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


_SCORER_BY_KIND = {"image": "phash", "audio": "audio_fp", "radial": "radial"}
_THRESHOLD_BY_KIND = {
    "image": _IMAGE_THRESHOLD,
    "audio": _AUDIO_THRESHOLD,
    "radial": _RADIAL_THRESHOLD,
}


def build_perceptual_matchkey(column: str, kind: str) -> MatchkeyConfig:
    """A single-field weighted matchkey scoring ``column`` with the media comparator."""
    scorer = _SCORER_BY_KIND[kind]
    threshold = _THRESHOLD_BY_KIND[kind]
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
        num_bands = recommend_num_bands(
            _HASH_BITS, 1.0 - _IMAGE_THRESHOLD, _BLOCK_RECALL_TARGET
        )
        config.blocking = BlockingConfig(
            strategy="perceptual",
            perceptual=PerceptualKeyConfig(
                column=image_cols[0], num_bands=num_bands, hash_bits=_HASH_BITS
            ),
        )
    return config
