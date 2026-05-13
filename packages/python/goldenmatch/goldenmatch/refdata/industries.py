"""NAICS 2022 industry-code normalization.

Ships the ``naics_normalize`` transform — canonicalizes industry codes
and titles so that two records describing the same business industry
land on the same string before scoring.

Three input shapes get normalized:

1. **Numeric codes**: digits possibly with separators or trailing text.
   "511210", "511 210", "511210 (Software publishing)" -> "511210".
   Codes longer than 6 digits get truncated to the first 6 (NAICS US
   uses 6-digit codes; longer inputs are typically NAICS sector +
   extra subdivision and the first 6 are still meaningful).

2. **Sector / subsector / industry titles**: known NAICS titles map
   back to their canonical code. "Software Publishers" -> "513210"
   (the 2022 6-digit code), "Information" -> "51", "Crop Production"
   -> "111", etc. Lookup is case-insensitive and tolerant of
   punctuation.

3. **Anything else**: pass through unchanged. The transform never
   raises — it's defensive about partial / dirty data.

Public API:

- ``naics_normalize(value)`` — the transform.
- ``code_for_title(title)`` — title → canonical code (or None).
- ``title_for_code(code)`` — canonical code → title (or None).
- ``is_available()`` — True iff the bundled NAICS file loaded.
- ``known_codes()`` / ``known_titles()`` — diagnostic frozensets.
"""
from __future__ import annotations

import json
import logging
import re
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "naics_2022.json"

_DIGITS_RE = re.compile(r"\d+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]")
_WS_RE = re.compile(r"\s+")

_lock = Lock()
_state: dict = {
    "loaded": False,
    "available": False,
    "code_to_title": {},   # "511210" -> "Software Publishers"
    "title_to_code": {},   # "softwarepublishers" -> "511210"  (normalized title)
    "all_codes": frozenset(),
    "all_titles": frozenset(),
}


def _norm_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Used as the key for title->code lookup so callers can search with
    minor formatting variation ('Software Publishers' vs 'software,
    publishers' vs 'SOFTWARE PUBLISHERS')."""
    s = title.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _load() -> None:
    if _state["loaded"]:
        return
    with _lock:
        if _state["loaded"]:
            return
        try:
            with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
                "r", encoding="utf-8"
            ) as f:
                payload = json.load(f)
            code_to_title: dict[str, str] = {}
            title_to_code: dict[str, str] = {}
            # Sections, ordered narrow-to-broad. When the same title
            # appears at multiple levels (rare), the narrowest (longest
            # code) wins for title->code because it's the most
            # informative match.
            sections = [
                "industries_6digit",
                "industries_5digit",
                "industry_groups_4digit",
                "subsectors_3digit",
                "sectors_2digit",
            ]
            # Build code_to_title from every section so any input
            # resolves (a 2-digit code stays a 2-digit code).
            for section in sections:
                for code, title in payload.get(section, {}).items():
                    code_to_title[code] = title
            # Build title_to_code from narrowest section first so a
            # title that also exists at a broader level keeps the narrow
            # code (more informative for matching).
            for section in sections:
                for code, title in payload.get(section, {}).items():
                    key = _norm_title(title)
                    if key and key not in title_to_code:
                        title_to_code[key] = code
            _state["code_to_title"] = code_to_title
            _state["title_to_code"] = title_to_code
            _state["all_codes"] = frozenset(code_to_title.keys())
            _state["all_titles"] = frozenset(code_to_title.values())
            _state["available"] = bool(code_to_title)
        except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "goldenmatch.refdata.industries: data file unavailable (%s); "
                "naics_normalize will be a no-op.",
                exc,
            )
            _state["available"] = False
        finally:
            _state["loaded"] = True


def _reload() -> None:
    """Test-only: force re-parse of the data file.

    Atomic swap rather than empty-then-reload: readers consult
    ``_state["code_to_title"]`` etc. without the lock (safe under the
    GIL once ``loaded=True``). Wiping the dicts before the parse would
    expose them to readers mid-call.
    """
    with _lock:
        _state["loaded"] = False  # forces _load() to re-run inside its own lock
    _load()


def is_available() -> bool:
    _load()
    return _state["available"]


def known_codes() -> frozenset[str]:
    _load()
    return _state["all_codes"]


def known_titles() -> frozenset[str]:
    _load()
    return _state["all_titles"]


def title_for_code(code: str | None) -> str | None:
    if code is None:
        return None
    _load()
    digits = "".join(_DIGITS_RE.findall(code))
    if not digits:
        return None
    # Try exact length first, then truncate to 6.
    if digits in _state["code_to_title"]:
        return _state["code_to_title"][digits]
    if len(digits) > 6:
        truncated = digits[:6]
        if truncated in _state["code_to_title"]:
            return _state["code_to_title"][truncated]
    return None


def code_for_title(title: str | None) -> str | None:
    if title is None:
        return None
    _load()
    key = _norm_title(title)
    if not key:
        return None
    return _state["title_to_code"].get(key)


def naics_normalize(value: str | None) -> str | None:
    """Canonicalize a NAICS-like value for matching.

    Numeric input: scans every 2+-digit run in the input; for each one,
    tries the truncated-to-6 form, then walks back through shorter
    prefixes to find the longest known code (so "1112112" -> "11121"
    when 111211 isn't in the table but 11121 is). Returns the first run
    that resolves. If no run resolves, returns the 6-digit-truncated
    form of the FIRST run -- unknown codes still cluster across records
    sharing them. Scanning multiple runs lets inputs like
    "NAICS 2022 code 511210" pick up the real code instead of the
    vintage year prefix.

    Title input: canonical code lookup (case- and punctuation-tolerant).
    Unmatched titles pass through with whitespace normalized.

    ``None`` -> ``None``. Empty/whitespace -> ``""``.

    The transform never raises. If the bundled data file is missing,
    falls back to lowercase + whitespace-collapse pass-through.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return ""
    _load()
    digit_runs = [m.group() for m in _DIGITS_RE.finditer(s) if len(m.group()) >= 2]
    if digit_runs:
        if _state["available"]:
            for run in digit_runs:
                canonical = run[:6]
                for n in range(len(canonical), 1, -1):
                    prefix = canonical[:n]
                    if prefix in _state["code_to_title"]:
                        return prefix
        # No run resolved (or refdata unavailable) -- fall back to the
        # truncated FIRST run so identical-but-unknown codes still
        # cluster across records.
        return digit_runs[0][:6]
    # Title-shape: try the title lookup.
    if _state["available"]:
        canonical = code_for_title(s)
        if canonical is not None:
            return canonical
    # Pass-through with whitespace normalize (matches the lowercase+strip
    # behavior of legal_form_strip's fallback).
    return _WS_RE.sub(" ", s.lower())


# ── Plugin protocol adapter ──────────────────────────────────────────────


class NaicsNormalizeTransform:
    """Adapter exposing ``naics_normalize`` through the plugin transform
    interface."""

    name = "naics_normalize"

    def transform(self, value: str | None) -> str | None:
        return naics_normalize(value)


def register_transforms() -> None:
    """Idempotent. Called from ``goldenmatch.refdata.__init__``."""
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    if not reg.has_transform(NaicsNormalizeTransform.name):
        reg.register_transform(NaicsNormalizeTransform.name, NaicsNormalizeTransform())
