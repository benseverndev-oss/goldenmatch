"""Candidate validation filter over GoldenFlow validator transforms. Fail-open. Spec 3.5.

GoldenFlow has NO `goldenflow.validators` module. Validators are registered
*transforms* (series-mode: `pl.Series -> bool pl.Series`) resolved via
`goldenflow.transforms.get_transform(name)`. The NANP validator is named
`phone_validate` (phonenumbers.is_possible_number); other useful ones:
`email_validate`, `npi_validate`, `date_validate`. We alias the friendly
`nanp`/`phone` names onto `phone_validate`.

Note: `goldenflow.transforms` alone does NOT populate the registry — the
submodule side-effects only fire when the top-level `goldenflow` package is
imported (its `__init__.py` imports every transform submodule). We therefore
import `goldenflow` (not just `goldenflow.transforms`) to ensure registration.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Friendly survivorship names -> real GoldenFlow transform names.
_VALIDATOR_ALIASES = {"nanp": "phone_validate", "phone": "phone_validate"}


def _resolve_validator(name: str):
    """Return a BATCH validator Callable[[list], list[bool|None]] backed by a
    GoldenFlow series-mode validator transform, or None if unavailable."""
    try:
        import goldenflow  # noqa: F401 — triggers submodule registration side-effects
        import polars as pl
        from goldenflow.transforms import get_transform
    except Exception:
        return None
    real = _VALIDATOR_ALIASES.get(name, name)
    info = get_transform(real)
    if info is None:
        return None

    def _run(values):
        series = pl.Series(name=real, values=[None if v is None else str(v) for v in values])
        return list(info.func(series).to_list())

    return _run


def goldenflow_filter(values: list, name: str) -> list:
    """Return a same-length list with invalid candidates replaced by None.
    Fail-open: unknown/unavailable validator (or any error) returns `values`
    unchanged."""
    validator = _resolve_validator(name)
    if validator is None:
        logger.warning("golden validate: validator %r unavailable; no filtering applied", name)
        return list(values)
    try:
        mask = validator(values)
    except Exception:
        logger.warning("golden validate: validator %r raised; no filtering applied", name)
        return list(values)
    if len(mask) != len(values):
        logger.warning("golden validate: validator %r returned wrong length; no filtering applied", name)
        return list(values)
    # A None/false mask entry -> candidate dropped to None; a None input stays None.
    return [v if (v is not None and ok) else None for v, ok in zip(values, mask)]
