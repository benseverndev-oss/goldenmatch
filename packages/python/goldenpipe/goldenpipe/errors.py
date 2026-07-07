"""goldenpipe-local exceptions."""
from __future__ import annotations


class PipeNotConfidentError(RuntimeError):
    """Raised by the auto-config brain when it cannot confidently plan a
    pipeline for a large input (red confidence band at/above the row threshold).

    Parallels goldenmatch's ``ControllerNotConfidentError``: refuse loudly
    rather than run an expensive, likely-wrong pipeline. Supply an explicit
    pipeline config (or reduce the input size) to proceed.
    """
