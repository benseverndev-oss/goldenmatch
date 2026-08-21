"""One pointer, appended to the exceptions people most often work around.

An exception is where a reader is most likely to guess. A message like "the
controller committed a RED config" reads as a malfunction, so the next move is
usually to dig through the implementation for a way around it -- when the real
answer is that the refusal is deliberate, documented, and has named escape
hatches. Exceptions are also the one place a library gets to talk to a caller
who never read the docs, including an AI agent that never will.

So the high-traffic exceptions end with a line naming where the answer lives.
Kept to one short line: an error message that buries its actual cause under
marketing is worse than one that says nothing.
"""

from __future__ import annotations

# Ships inside the wheel; resolvable offline at
# ``Path(goldenmatch.__file__).parent / "llms.txt"``.
_LLMS_TXT = "goldenmatch/llms.txt"
_DOCS = "https://docs.bensevern.dev/docs/goldenmatch"

AGENT_DOCS_HINT = (
    f"This behaviour is documented, not incidental -- see {_LLMS_TXT} "
    f"(ships inside this package) or {_DOCS} before working around it."
)


def with_docs_hint(message: str) -> str:
    """Append the pointer to an exception message, idempotently."""
    if AGENT_DOCS_HINT in message:
        return message
    return f"{message.rstrip()} {AGENT_DOCS_HINT}"
