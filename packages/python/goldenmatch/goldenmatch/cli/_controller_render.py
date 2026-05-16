"""Rich rendering of AutoConfigController telemetry for the CLI.

Mirrors the web ControllerPanel and TUI ControllerTab: stop_reason,
ComplexityProfile health, committed matchkeys with Path Y NE indicator,
indicator column priors, refit decisions trace.

The two entry points:

- ``capture_controller_state()`` reads ``_LAST_CONTROLLER_RUN`` off the
  ContextVar and returns ``(profile, history)`` or ``(None, None)``. Call
  this immediately after ``auto_configure_df`` / ``auto_configure`` /
  ``run_dedupe(... auto_config=True)`` returns.
- ``render_controller_panel(...)`` returns a renderable Rich Panel; print
  it to stderr in your command's success path.

Why stderr: keeps stdout clean for downstream piping (``goldenmatch dedupe
... | jq``, etc.). Aligns with how the existing ``dedupe`` command
already routes preview output via ``err_console``.
"""
from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_HEALTH_STYLES = {
    "green": "bold green",
    "yellow": "bold yellow",
    "red": "bold red",
}

_STOP_REASON_HINTS = {
    "green": "iteration produced a GREEN profile",
    "converged": "profile distance to prior iteration fell below epsilon",
    "budget_iterations": "max-iteration budget reached before GREEN",
    "budget_time": "wall-clock budget exhausted",
    "policy_satisfied": "policy left current config in place on non-green profile",
    "policy_no_progress": "policy proposed identical config twice in a row",
    "oscillating": "same (config, rule) pair repeated within a 4-iteration window",
    "cancelled": "cancelled (KeyboardInterrupt)",
}


def capture_controller_state() -> tuple[Any, Any]:
    """Read controller state off the autoconfig ContextVar.

    Returns ``(profile, history)``; both are ``None`` when the controller
    didn't run on the current thread (e.g. user passed ``--config``).
    """
    try:
        from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
    except Exception:
        return None, None
    state = _LAST_CONTROLLER_RUN.get()
    if state is None:
        return None, None
    return state[0], state[1]


def _health_verdict(profile: Any) -> str | None:
    if profile is None:
        return None
    try:
        return profile.health().value
    except Exception:
        return None


def _stop_reason(history: Any) -> str | None:
    if history is None:
        return None
    sr = getattr(history, "stop_reason", None)
    return sr.value if sr is not None else None


def _header(profile: Any, history: Any) -> Text:
    txt = Text()
    verdict = _health_verdict(profile)
    if verdict:
        txt.append(f"health · {verdict}", style=_HEALTH_STYLES.get(verdict, ""))
    reason = _stop_reason(history)
    if reason:
        if txt:
            txt.append("    ")
        txt.append(f"stop · {reason.replace('_', ' ')}", style="bold")
        hint = _STOP_REASON_HINTS.get(reason)
        if hint:
            txt.append(f"  ({hint})", style="dim")
    if history is not None:
        elapsed = getattr(history, "elapsed", None)
        if elapsed is not None:
            try:
                ms = elapsed.total_seconds() * 1000.0
                txt.append(f"    elapsed · {ms:.0f}ms", style="dim")
            except Exception:
                pass
        drift = getattr(history, "full_vs_sample_drift", None)
        if drift is not None:
            txt.append(f"    drift · {drift:.2f}", style="dim")
    return txt


def _committed_matchkeys(committed_config: Any) -> RenderableType | None:
    if committed_config is None:
        return None
    try:
        matchkeys = committed_config.get_matchkeys()
    except Exception:
        return None
    if not matchkeys:
        return None
    table = Table(
        show_header=True,
        header_style="bold cyan",
        title="committed matchkeys",
        title_style="bold cyan",
        title_justify="left",
        box=None,
        padding=(0, 1),
    )
    table.add_column("name", style="cyan")
    table.add_column("type")
    table.add_column("threshold", justify="right")
    table.add_column("fields", overflow="fold")
    table.add_column("path Y")
    for mk in matchkeys:
        threshold = (
            f"{mk.threshold:.2f}" if mk.threshold is not None else "—"
        )
        fields = ", ".join(
            f"{f.column or f.field}"
            + (f"·{f.scorer}" if f.scorer else "")
            for f in mk.fields
        )
        ne = getattr(mk, "negative_evidence", None) or []
        ne_marker = (
            Text(f"{len(ne)} NE", style="bold magenta") if ne else Text("—", style="dim")
        )
        table.add_row(mk.name, mk.type or "—", threshold, fields, ne_marker)
    return table


def _negative_evidence(committed_config: Any) -> RenderableType | None:
    if committed_config is None:
        return None
    try:
        matchkeys = committed_config.get_matchkeys()
    except Exception:
        return None
    rows: list[tuple[str, str, str, str, str]] = []
    for mk in matchkeys:
        for nf in getattr(mk, "negative_evidence", None) or []:
            rows.append((
                mk.name,
                nf.field,
                nf.scorer,
                f"{nf.threshold:.2f}",
                f"-{nf.penalty:.2f}",
            ))
    if not rows:
        return None
    table = Table(
        show_header=True,
        header_style="bold magenta",
        title="negative evidence (path Y)",
        title_style="bold magenta",
        title_justify="left",
        box=None,
        padding=(0, 1),
    )
    table.add_column("matchkey")
    table.add_column("field")
    table.add_column("scorer")
    table.add_column("threshold", justify="right")
    table.add_column("penalty", justify="right", style="magenta")
    for r in rows:
        table.add_row(*r)
    return table


def _profile_strip(profile: Any) -> RenderableType | None:
    if profile is None:
        return None
    cells: list[tuple[str, str]] = []
    scoring = getattr(profile, "scoring", None)
    if scoring is not None:
        cells.append(("pairs", _num(getattr(scoring, "n_pairs_scored", 0))))
        cells.append(("above thr", _pct(getattr(scoring, "mass_above_threshold", 0))))
        cells.append(("borderline", _pct(getattr(scoring, "mass_in_borderline", 0))))
    blocking = getattr(profile, "blocking", None)
    if blocking is not None:
        cells.append(("blocks", _num(getattr(blocking, "n_blocks", 0))))
        cells.append(("p99 block", _num(getattr(blocking, "block_sizes_p99", 0))))
    cluster = getattr(profile, "cluster", None)
    if cluster is not None:
        cells.append(("clusters", _num(getattr(cluster, "n_clusters", 0))))
        cells.append(("transitivity", _pct(getattr(cluster, "transitivity_rate", 0))))
    if not cells:
        return None
    table = Table(
        show_header=False,
        title="complexity profile",
        title_style="bold cyan",
        title_justify="left",
        box=None,
        padding=(0, 1),
    )
    table.add_column(style="cyan")
    table.add_column(style="bold")
    for label, value in cells:
        table.add_row(label, value)
    return table


def _decision_trace(history: Any) -> RenderableType | None:
    if history is None:
        return None
    rows: list[tuple[str, str, str, str]] = []
    for entry in getattr(history, "entries", []):
        if entry.decision is None:
            continue
        rows.append((
            str(entry.iteration),
            entry.decision.rule_name,
            _truncate(entry.decision.rationale, 80),
            f"{int(getattr(entry, 'wall_clock_ms', 0) or 0)}ms",
        ))
    if not rows:
        return None
    table = Table(
        show_header=True,
        header_style="bold cyan",
        title="refit decisions",
        title_style="bold cyan",
        title_justify="left",
        box=None,
        padding=(0, 1),
    )
    table.add_column("iter", justify="right")
    table.add_column("rule")
    table.add_column("rationale", overflow="fold")
    table.add_column("wall", justify="right", style="dim")
    for r in rows:
        table.add_row(*r)
    return table


def _column_priors(profile: Any) -> RenderableType | None:
    if profile is None:
        return None
    data_profile = getattr(profile, "data", None)
    priors = getattr(data_profile, "column_priors", None) or {}
    rows: list[tuple[str, float, float]] = []
    for col, p in priors.items():
        identity = float(getattr(p, "identity_score", 0.0))
        corruption = float(getattr(p, "corruption_score", 0.0))
        if identity == 0.0 and corruption == 0.0:
            continue
        rows.append((col, identity, corruption))
    if not rows:
        return None
    rows.sort(key=lambda r: (-r[1], -r[2]))
    table = Table(
        show_header=True,
        header_style="bold cyan",
        title="indicator column priors",
        title_style="bold cyan",
        title_justify="left",
        box=None,
        padding=(0, 1),
    )
    table.add_column("column")
    table.add_column("identity", justify="right")
    table.add_column("corruption", justify="right")
    for col, identity, corruption in rows:
        table.add_row(col, f"{identity:.2f}", f"{corruption:.2f}")
    return table


def render_controller_panel(
    *,
    profile: Any,
    history: Any,
    committed_config: Any,
    verbose: bool = False,
) -> Panel:
    """Build the Rich Panel rendered by ``dedupe`` / ``match`` / ``autoconfig``.

    Always shows the header + committed matchkeys + NE table (Path Y is
    headline information). ``verbose`` adds the complexity profile strip,
    full decision trace, and indicator column priors — useful for debugging
    auto-config decisions, noisy by default.
    """
    parts: list[RenderableType] = []
    parts.append(_header(profile, history))
    plan_row = _execution_plan(history)
    if plan_row is not None:
        parts.append(plan_row)
    mks = _committed_matchkeys(committed_config)
    if mks is not None:
        parts.append(mks)
    ne = _negative_evidence(committed_config)
    if ne is not None:
        parts.append(ne)
    if verbose:
        for renderable in (
            _profile_strip(profile),
            _decision_trace(history),
            _column_priors(profile),
        ):
            if renderable is not None:
                parts.append(renderable)
    return Panel(
        Group(*parts),
        title="[bold yellow]AutoConfig controller[/]",
        title_align="left",
        border_style="yellow",
    )


def render_short_status(*, profile: Any, history: Any, committed_config: Any) -> str:
    """One-line plain-text summary (no Rich markup, no ANSI). For logs."""
    bits: list[str] = []
    verdict = _health_verdict(profile)
    if verdict:
        bits.append(f"health={verdict}")
    reason = _stop_reason(history)
    if reason:
        bits.append(f"stop={reason}")
    if history is not None:
        decisions = [e for e in getattr(history, "entries", []) if e.decision is not None]
        bits.append(f"iterations={len(decisions)}")
    plan_short = _execution_plan_short(history)
    if plan_short:
        bits.append(plan_short)
    if committed_config is not None:
        try:
            matchkeys = committed_config.get_matchkeys()
            ne_count = sum(len(getattr(mk, "negative_evidence", None) or []) for mk in matchkeys)
            bits.append(f"matchkeys={len(matchkeys)}")
            if ne_count:
                bits.append(f"path_y_ne={ne_count}")
        except Exception:
            pass
    return " ".join(bits) or "controller unavailable"


# ── Helpers ──────────────────────────────────────────────────────────


def _execution_plan(history: Any) -> RenderableType | None:
    """Render the controller v3 ExecutionPlan as a Rich Text row.

    ``None`` when history is missing or pre-v3 (no execution_plan field).
    Highlights the rule + backend; tuning knobs follow in dim style so the
    rule selection is the headline.
    """
    if history is None:
        return None
    plan = getattr(history, "execution_plan", None)
    if plan is None:
        return None
    rule = getattr(plan, "rule_name", None) or "unknown"
    backend = getattr(plan, "backend", "polars-direct")
    extras: list[str] = []
    chunk = getattr(plan, "chunk_size", None)
    if chunk is not None:
        extras.append(f"chunk_size={chunk}")
    workers = getattr(plan, "max_workers", None)
    if workers:
        extras.append(f"max_workers={workers}")
    spill = getattr(plan, "pair_spill_threshold", None)
    if spill:
        extras.append(f"spill={spill}")
    strategy = getattr(plan, "clustering_strategy", None)
    if strategy and strategy != "in_memory":
        extras.append(f"clustering={strategy}")
    head = f"[bold cyan]Plan:[/] [bold]{rule}[/] -> backend=[bold]{backend}[/]"
    if extras:
        head = f"{head} [dim]({', '.join(extras)})[/]"
    return Text.from_markup(head)


def _execution_plan_short(history: Any) -> str:
    """One-token summary of the execution plan, or empty string when absent."""
    if history is None:
        return ""
    plan = getattr(history, "execution_plan", None)
    if plan is None:
        return ""
    rule = getattr(plan, "rule_name", None) or "unknown"
    backend = getattr(plan, "backend", "polars-direct")
    return f"plan={rule}/{backend}"


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"
