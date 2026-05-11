"""Thread-local profile emitter stack.

Stage instrumentation calls ``current_emitter()`` to get the active emitter;
when the controller is not running, this returns a no-op singleton so stages
pay zero cost.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §Types & contracts § "Profile emitter (S1-C)".
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    DataProfile,
    DomainProfile,
    MatchkeyProfile,
    ScoringProfile,
)


class ProfileEmitter:
    """Buffer for stage outputs in a single iteration. Set by the controller
    via ``profile_capture``; stages call ``set_*`` methods inline.

    Multiple writes overwrite (last writer wins). Empty fields default to
    ``ComplexityProfile`` defaults at assemble time.
    """
    __slots__ = ("blocking", "scoring", "cluster", "data", "domain", "matchkey")

    def __init__(self) -> None:
        self.blocking: BlockingProfile | None = None
        self.scoring: ScoringProfile | None = None
        self.cluster: ClusterProfile | None = None
        self.data: DataProfile | None = None
        self.domain: DomainProfile | None = None
        self.matchkey: MatchkeyProfile | None = None

    def set_blocking(self, p: BlockingProfile) -> None: self.blocking = p
    def set_scoring(self, p: ScoringProfile) -> None: self.scoring = p
    def set_cluster(self, p: ClusterProfile) -> None: self.cluster = p
    def set_data(self, p: DataProfile) -> None: self.data = p
    def set_domain(self, p: DomainProfile) -> None: self.domain = p
    def set_matchkey(self, p: MatchkeyProfile) -> None: self.matchkey = p


class _NullEmitter:
    """Singleton no-op. Stages call ``set_*`` and the writes vanish."""
    __slots__ = ()
    def set_blocking(self, p): pass
    def set_scoring(self, p): pass
    def set_cluster(self, p): pass
    def set_data(self, p): pass
    def set_domain(self, p): pass
    def set_matchkey(self, p): pass


_NULL_EMITTER = _NullEmitter()
# Default is an immutable empty tuple; profile_capture pushes onto a copy.
_emitter_stack: ContextVar[tuple[ProfileEmitter, ...]] = ContextVar(
    "emitter_stack", default=()
)


def current_emitter():
    """Return the active emitter, or the null singleton when none is set."""
    stack = _emitter_stack.get()
    return stack[-1] if stack else _NULL_EMITTER


@contextmanager
def profile_capture() -> Iterator[ProfileEmitter]:
    """Push a new ProfileEmitter onto the stack; pop on exit (incl. exception).

    Concurrency:
      * ContextVar is per-thread / per-asyncio-task → independent stacks.
      * Re-entry within the same context pushes/pops correctly.
      * Exception unwinding hits ``finally`` and restores prior stack via reset.
    """
    emitter = ProfileEmitter()
    prev = _emitter_stack.get()
    new = (*prev, emitter)
    token = _emitter_stack.set(new)
    try:
        yield emitter
    finally:
        _emitter_stack.reset(token)
