"""Freshness / staleness profiler for date & datetime columns.

Two checks, both designed to be low-noise on ordinary (historical) data:

- **Future-dated values** (always on, zero-config): timestamps after "now" are
  almost always clock skew or data-entry errors (an `order_date` in 2099). High
  signal, no configuration, no false positives on legitimately-old data.
- **Staleness** (name-gated, generous threshold): only for columns whose name
  signals an *update / event* timestamp (`updated_at`, `last_seen`, ...), and
  only when the newest value is more than `_STALE_DAYS` old — a strong hint that
  a pipeline has stalled. Gating + the generous threshold keep historical
  datasets (which are legitimately old) from tripping it.

Routes through the Frame seam (`max()` + `count_gt()`); no native kernel -- date
arithmetic is already vectorized and cheap.
"""
from __future__ import annotations

import datetime as _dt
import logging

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

logger = logging.getLogger(__name__)

_EPOCH_DATE = _dt.date(1970, 1, 1)
_EPOCH_DT = _dt.datetime(1970, 1, 1)


def _now_epoch_for_array(arr_type, now: _dt.date | _dt.datetime) -> int | None:
    """Offset-free epoch of ``now`` in the Arrow array's native temporal unit
    (spec B2). Pure subtraction from the naive 1970-01-01 epoch -- NEVER
    ``.timestamp()`` (that would apply the machine's local UTC offset). Returns
    ``None`` for a non-temporal array so the shadow simply skips."""
    import pyarrow as pa

    if pa.types.is_date32(arr_type):
        d = now.date() if isinstance(now, _dt.datetime) else now
        return (d - _EPOCH_DATE).days
    if pa.types.is_date64(arr_type):
        d = now.date() if isinstance(now, _dt.datetime) else now
        return (d - _EPOCH_DATE).days * 86_400_000
    if pa.types.is_timestamp(arr_type):
        dt = now if isinstance(now, _dt.datetime) else _dt.datetime(now.year, now.month, now.day)
        delta = dt - _EPOCH_DT
        unit = arr_type.unit
        if unit == "s":
            return delta // _dt.timedelta(seconds=1)
        if unit == "ms":
            return delta // _dt.timedelta(milliseconds=1)
        if unit == "us":
            return delta // _dt.timedelta(microseconds=1)
        if unit == "ns":
            return (delta // _dt.timedelta(microseconds=1)) * 1000
    return None

# Newest value older than this (days) on an update/event column => likely stale.
_STALE_DAYS = 365
# Column-name signals that the timestamp tracks "last change", so old == stale.
_UPDATE_KEYWORDS = (
    "updated", "modified", "last_seen", "lastseen", "last_login", "lastlogin",
    "ingested", "loaded", "refreshed", "synced", "as_of", "asof", "event",
    "timestamp", "created", "inserted",
)


def _looks_like_update_column(name: str) -> bool:
    lower = name.lower()
    return any(kw in lower for kw in _UPDATE_KEYWORDS)


class FreshnessProfiler(BaseProfiler):
    def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:
        frame = to_frame(frame)
        col = frame.column(column)
        is_datetime = col.dtype == "datetime"
        is_date = col.dtype == "date"
        if not (is_datetime or is_date):
            return []

        non_null = col.drop_nulls()
        if len(non_null) == 0:
            return []

        # "now" in the column's own granularity. Datetime cols may be tz-aware;
        # comparison against a naive `now` raises -> skip gracefully.
        now: _dt.date | _dt.datetime = _dt.date.today() if is_date else _dt.datetime.now()
        try:
            future_count = non_null.count_gt(now)
            newest = non_null.max()
            # Shadow-compute the fused native date_freshness kernel on the real
            # scan path so it runs against production shapes ahead of the Flip
            # (see tests/engine/test_w2_shadow.py for the parity assertion). This
            # sits INSIDE the try AFTER the successful count_gt/max so a tz-aware
            # column that makes Polars bail never reaches the kernel (spec S). The
            # inner try guarantees a shadow failure never trips the outer except
            # (which would drop legitimate findings) -- NOT authoritative.
            if native_enabled("date_freshness"):
                try:
                    arr = non_null.to_arrow()
                    now_epoch = _now_epoch_for_array(arr.type, now)
                    if now_epoch is not None:
                        native_module().date_freshness(arr, now_epoch)
                except Exception as e:  # noqa: BLE001 - shadow-only, never affects output
                    logger.debug("date_freshness shadow failed on %s: %s", column, e)
        except Exception:  # noqa: BLE001 - tz-aware vs naive, exotic dtype, etc.
            return []

        findings: list[Finding] = []

        if future_count > 0:
            findings.append(Finding(
                severity=Severity.WARNING,
                column=column,
                check="future_dated",
                message=(
                    f"{future_count} value(s) in '{column}' are in the future "
                    f"(newest: {newest}) — likely clock skew or a data-entry error."
                ),
                affected_rows=future_count,
                sample_values=[str(newest)],
                suggestion="Verify the source clock/timezone, or treat future-dated rows as invalid.",
                confidence=0.7,
                metadata={"technique": "freshness", "future_count": future_count},
            ))

        # Staleness: only for update/event columns whose newest value is very old.
        if _looks_like_update_column(column) and newest is not None:
            newest_date = newest.date() if isinstance(newest, _dt.datetime) else newest
            today = _dt.date.today()
            age_days = (today - newest_date).days
            if age_days > _STALE_DAYS:
                findings.append(Finding(
                    severity=Severity.INFO,
                    column=column,
                    check="stale_data",
                    message=(
                        f"Newest '{column}' is {age_days} days old ({newest_date}) — "
                        f"this update/event timestamp suggests the data may be stale."
                    ),
                    affected_rows=len(non_null),
                    sample_values=[str(newest_date)],
                    suggestion="Confirm the pipeline feeding this table is still running.",
                    confidence=0.5,
                    metadata={"technique": "freshness", "age_days": age_days},
                ))

        return findings
