"""Log-output sanitization (CodeQL py/log-injection mitigation).

User-supplied values (file paths, run ids, config strings) flow into
log lines. Strip control characters so a crafted value can't forge
log records or smuggle ANSI escapes into terminals tailing the log.
"""

from __future__ import annotations

import re

_CONTROL_CHARS = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MAX_LEN = 1000


def sanitize_for_log(value: object, max_length: int = _MAX_LEN) -> str:
    """Return a log-safe string: newlines collapsed, ANSI/control chars
    stripped, truncated to *max_length*."""
    s = str(value)
    s = s.replace("\r", " ").replace("\n", " ")
    s = _CONTROL_CHARS.sub("", s)
    if len(s) > max_length:
        s = s[: max(0, max_length - 3)] + "..."
    return s
