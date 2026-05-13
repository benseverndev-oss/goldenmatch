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
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "naics_2022.json"

_DIGITS_RE = re.compile(r"\d+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]")
_WS_RE = re.compile(r"\s+")

# narrow-to-broad — title_to_code keeps the narrowest match when the
# same title appears at multiple hierarchy levels.
_SECTIONS = (
    "industries_6digit",
    "industries_5digit",
    "industry_groups_4digit",
    "subsectors_3digit",
    "sectors_2digit",
)


@dataclass(frozen=True)
class _IndustriesState:
    """Loaded NAICS state. Frozen for atomic-swap semantics on _reload."""

    code_to_title: Mapping[str, str]
    title_to_code: Mapping[str, str]
    all_codes: frozenset[str]
    all_titles: frozenset[str]


_lock = Lock()
_state: _IndustriesState | None = None


def _norm_title(title: str) -> str:
    s = title.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _build_state_from_file() -> _IndustriesState | None:
    with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
        "r", encoding="utf-8"
    ) as f:
        payload = json.load(f)
    code_to_title: dict[str, str] = {}
    title_to_code: dict[str, str] = {}
    for section in _SECTIONS:
        for code, title in payload.get(section, {}).items():
            code_to_title[code] = title
    for section in _SECTIONS:
        for code, title in payload.get(section, {}).items():
            key = _norm_title(title)
            if key and key not in title_to_code:
                title_to_code[key] = code
    if not code_to_title:
        return None
    return _IndustriesState(
        code_to_title=code_to_title,
        title_to_code=title_to_code,
        all_codes=frozenset(code_to_title.keys()),
        all_titles=frozenset(code_to_title.values()),
    )


def _load() -> None:
    global _state
    if _state is not None:
        return
    with _lock:
        if _state is not None:
            return
        try:
            _state = _build_state_from_file()
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "goldenmatch.refdata.industries: data file unavailable (%s); "
                "naics_normalize will be a no-op.",
                exc,
            )
            _state = None


def _reload() -> None:
    """Test-only: drop the cached state; next access re-parses."""
    global _state
    with _lock:
        _state = None


def is_available() -> bool:
    _load()
    return _state is not None


def known_codes() -> frozenset[str]:
    _load()
    if _state is None:
        return frozenset()
    return _state.all_codes


def known_titles() -> frozenset[str]:
    _load()
    if _state is None:
        return frozenset()
    return _state.all_titles


def title_for_code(code: str | None) -> str | None:
    if code is None:
        return None
    _load()
    if _state is None:
        return None
    digits = "".join(_DIGITS_RE.findall(code))
    if not digits:
        return None
    # Try the 6-digit truncation first so overlong inputs collapse;
    # fall back to the exact-length form for shorter codes (2-5 digit
    # sector lookups).
    return _state.code_to_title.get(digits[:6]) or _state.code_to_title.get(digits)


def code_for_title(title: str | None) -> str | None:
    if title is None:
        return None
    _load()
    if _state is None:
        return None
    key = _norm_title(title)
    if not key:
        return None
    return _state.title_to_code.get(key)


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
        if _state is not None:
            for run in digit_runs:
                canonical = run[:6]
                for n in range(len(canonical), 1, -1):
                    prefix = canonical[:n]
                    if prefix in _state.code_to_title:
                        return prefix
        # No run resolved (or refdata unavailable) -- fall back to the
        # truncated FIRST run so identical-but-unknown codes still
        # cluster across records.
        return digit_runs[0][:6]
    # Title-shape: try the title lookup.
    if _state is not None:
        canonical = code_for_title(s)
        if canonical is not None:
            return canonical
    return _WS_RE.sub(" ", s.lower())


from goldenmatch.plugins.base import (
    TransformPlugin,  # noqa: E402 — local import keeps the module-top section dataclass-only
)


class NaicsNormalizeTransform(TransformPlugin):
    """Adapter exposing ``naics_normalize`` through the
    ``goldenmatch.plugins.base.TransformPlugin`` protocol."""

    name = "naics_normalize"

    def transform(self, value: str | None) -> str | None:
        return naics_normalize(value)


def register_transforms() -> None:
    """Idempotent. Called from ``goldenmatch.refdata.__init__``."""
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    if not reg.has_transform(NaicsNormalizeTransform.name):
        reg.register_transform(NaicsNormalizeTransform.name, NaicsNormalizeTransform())
