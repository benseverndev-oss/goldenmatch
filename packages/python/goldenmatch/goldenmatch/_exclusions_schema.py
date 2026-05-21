"""Single source of the ``exclude_columns`` JSON Schema fragment.

MCP tool schemas, A2A skill schemas, and the REST OpenAPI all consume
this so the parameter shape stays in lockstep across every surface.
A cross-cutting test (`tests/test_exclude_columns_surfaces.py`) pins
byte-equality of the rendered schema in each surface against this
fragment.

Spec: docs/superpowers/specs/2026-05-21-exclude-columns-surfaces-design.md
"""

from __future__ import annotations

# JSON Schema fragment for `exclude_columns` -- the same shape every
# external surface exposes. Internal Python callers use the typed
# kwarg + the GoldenMatchConfig.exclude_columns field directly.
EXCLUDE_COLUMNS_SCHEMA: dict = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Column names to skip across GoldenMatch + GoldenFlow + "
        "auto-config. Optional. Layered with config.exclude_columns "
        "when both are set. force_include (env var) rescues from "
        "any opt-out path."
    ),
    "default": [],
}


def parse_csv_exclude_columns(raw: str | None) -> list[str]:
    """Parse a comma-separated CLI string into a clean list.

    Strips whitespace around each name and filters empties. An empty
    or None input maps to an empty list -- the absence of any
    exclusion, not a list-with-one-empty-string.

    Used by every CLI command that takes ``--exclude-columns``. Keeping
    the parser in one place avoids per-command divergence on
    whitespace / empty-segment handling.
    """
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def merge_exclude_columns_into_config(config, raw: str | None) -> list[str]:
    """Parse a comma-separated CLI string and merge into
    ``config.exclude_columns`` (additive, dedup-preserving order).

    Returns the resolved list (the post-merge config field value) so
    callers can log it. Idempotent on repeated calls with the same
    inputs. Best-effort against config objects that don't accept the
    field (e.g. test shims) -- silently no-ops on AttributeError.

    Does NOT touch the ``_RUNTIME_EXCLUDE_COLUMNS`` ContextVar -- CLI
    handlers manage that explicitly via try/finally so the var doesn't
    leak across pytest invocations sharing a process. The downstream
    pipeline reads ``config.exclude_columns`` directly OR the
    ``dedupe_df``/``match_df`` shim sets the ContextVar from the
    config field at dispatch time.
    """
    parsed = parse_csv_exclude_columns(raw)
    existing = list(getattr(config, "exclude_columns", None) or [])
    if not parsed and not existing:
        return []
    merged = list(dict.fromkeys(existing + parsed))
    if merged:
        try:
            config.exclude_columns = merged
        except Exception:
            pass
    return merged
