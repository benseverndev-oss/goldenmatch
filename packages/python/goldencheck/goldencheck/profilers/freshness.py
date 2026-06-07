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

Pure-Polars: `dt.max()` + a vectorized future-count. No native kernel -- date
arithmetic is already vectorized and cheap.
"""
from __future__ import annotations

import datetime as _dt

import polars as pl

from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

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
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        col = df[column]
        is_datetime = col.dtype == pl.Datetime
        is_date = col.dtype == pl.Date
        if not (is_datetime or is_date):
            return []

        non_null = col.drop_nulls()
        if non_null.len() == 0:
            return []

        # "now" in the column's own granularity. Datetime cols may be tz-aware;
        # comparison against a naive `now` raises -> skip gracefully.
        now: _dt.date | _dt.datetime = _dt.date.today() if is_date else _dt.datetime.now()
        try:
            future_count = int((non_null > now).sum())
            newest = non_null.max()
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
                    affected_rows=non_null.len(),
                    sample_values=[str(newest_date)],
                    suggestion="Confirm the pipeline feeding this table is still running.",
                    confidence=0.5,
                    metadata={"technique": "freshness", "age_days": age_days},
                ))

        return findings
