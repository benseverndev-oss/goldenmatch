"""GoldenMatch binding of the in-tree issue-reporter (``_diagnostics_report``).

Pins package / version / repo so call sites read one helper. Diagnostics is
never load-bearing: the report primitives in
:mod:`goldenmatch.core._diagnostics_report` never raise, so a failing prompt
can never become the failure.

Fires only on ANOMALIES -- a wheel-skew slow path, an unexpected crash, a broken
optional-dependency install -- never on expected fallbacks or user-input errors.
See ``docs/design/2026-07-12-diagnostics-issue-reporter.md``.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from goldenmatch.core import _diagnostics_report as _gd

logger = logging.getLogger("goldenmatch")

_REPO = "benseverndev-oss/goldenmatch"

# Exceptions that are the USER's situation or a by-design refusal -- these must
# NEVER trigger a "file an issue" prompt. Everything else at a public entry
# point is treated as unexpected (a candidate bug).
_EXPECTED_ENTRYPOINT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    FileNotFoundError,
    NotImplementedError,
)


def _expected_exceptions() -> tuple[type[BaseException], ...]:
    """The by-design exception set, resolved lazily so importing this module
    never drags the controller/lint/probabilistic modules."""
    extra: list[type[BaseException]] = []
    for mod, name in (
        ("goldenmatch.core.autoconfig_controller", "ControllerNotConfidentError"),
        ("goldenmatch.core.autoconfig_controller", "ConfigValidationError"),
        ("goldenmatch.core.config_lint", "ConfigLintError"),
        ("goldenmatch.core.probabilistic", "FSModelMismatchError"),
        ("goldenmatch.core.throughput_verify", "ThroughputNotApplicableError"),
        ("goldenmatch.core.distributed_routing_rules", "SlowPathRefusedError"),
        ("goldenmatch.core._paths", "PathOutsideAllowedRootError"),
    ):
        try:
            m = __import__(mod, fromlist=[name])
            extra.append(getattr(m, name))
        except Exception:  # noqa: BLE001 - a missing optional module is fine
            continue
    return _EXPECTED_ENTRYPOINT_EXCEPTIONS + tuple(extra)


def _version() -> str | None:
    try:
        from goldenmatch import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return None


def report_anomaly(category: str, summary: str, **kwargs: Any) -> None:
    """Report a GoldenMatch anomaly (never raises)."""
    kwargs.setdefault("package", "goldenmatch")
    kwargs.setdefault("version", _version())
    kwargs.setdefault("repo", _REPO)
    kwargs.setdefault("logger", logger)
    _gd.report_anomaly(category, summary, **kwargs)


def report_unexpected(
    exc: BaseException,
    *,
    category: str,
    summary: str,
    expected: Iterable[type[BaseException]] | None = None,
    **kwargs: Any,
) -> None:
    """Report ``exc`` only if it is not a by-design / user-facing exception."""
    kwargs.setdefault("package", "goldenmatch")
    kwargs.setdefault("version", _version())
    kwargs.setdefault("repo", _REPO)
    kwargs.setdefault("logger", logger)
    _gd.report_exception(
        exc,
        category=category,
        summary=summary,
        expected=tuple(expected) if expected is not None else _expected_exceptions(),
        **kwargs,
    )


_F = TypeVar("_F", bound=Callable[..., Any])


def guard_entrypoint(category: str, summary: str) -> Callable[[_F], _F]:
    """Decorator: on an UNEXPECTED exception at a public entry point, emit an
    issue prompt (with traceback + environment) then re-raise unchanged. Expected
    exceptions (see :func:`_expected_exceptions`) pass straight through."""

    def deco(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - report then re-raise
                # Exception, not BaseException: KeyboardInterrupt/SystemExit are
                # the user's action, not an anomaly -- they pass through silently.
                report_unexpected(exc, category=category, summary=summary)
                raise

        return wrapper  # type: ignore[return-value]

    return deco
