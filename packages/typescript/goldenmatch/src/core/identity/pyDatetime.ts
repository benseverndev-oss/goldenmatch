/**
 * pyDatetime.ts -- reproduce Python's `datetime.isoformat()` for a UTC datetime.
 *
 * Edge-safe (pure, no `node:*`). This is load-bearing for the identity-audit
 * chain's byte-identical cross-toolkit hashing (PR-B): Python stamps
 * `recorded_at` via `datetime.isoformat()`, and the audit `entryHash`/seal
 * chain hashes over the stored string. JS `Date.toISOString()` always emits
 * `YYYY-MM-DDTHH:MM:SS.mmmZ` (fixed 3-digit millis + `Z`), which does NOT match
 * Python. `pyIsoformat` reproduces Python's exact spelling so the string a TS
 * event stores hashes identically to the one a Python event stores.
 *
 * Python `datetime.isoformat()` for a naive/UTC datetime:
 *   - `YYYY-MM-DDTHH:MM:SS`
 *   - plus `.NNNNNN` (6-digit microseconds) ONLY when microseconds != 0
 *     (Python omits the fractional part entirely when microseconds == 0)
 *   - NO `Z` / offset suffix.
 *
 * JS `Date` carries only millisecond resolution, so microseconds are the JS
 * milliseconds padded to 6 digits (`ms * 1000`). The UTC calendar fields are
 * read (`getUTCFullYear` etc.) so the output is the UTC wall-clock, matching a
 * UTC/naive Python datetime.
 */

function pad(value: number, width: number): string {
  return String(value).padStart(width, "0");
}

/**
 * Format a UTC `Date` the way Python `datetime.isoformat()` would:
 * `2026-01-02T03:04:05` when microseconds are zero, otherwise
 * `2026-01-02T03:04:05.678000` (JS millis padded to microseconds). No `Z`.
 */
export function pyIsoformat(date: Date): string {
  const year = pad(date.getUTCFullYear(), 4);
  const month = pad(date.getUTCMonth() + 1, 2);
  const day = pad(date.getUTCDate(), 2);
  const hour = pad(date.getUTCHours(), 2);
  const minute = pad(date.getUTCMinutes(), 2);
  const second = pad(date.getUTCSeconds(), 2);
  const base = `${year}-${month}-${day}T${hour}:${minute}:${second}`;
  const micros = date.getUTCMilliseconds() * 1000;
  if (micros === 0) return base;
  return `${base}.${pad(micros, 6)}`;
}
