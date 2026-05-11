"""Controller tab — surfaces AutoConfigController telemetry (v1.7-v1.12).

Mirrors the web UI's ControllerPanel (web/frontend/src/components/
ControllerPanel.tsx): stop_reason, health verdict, committed matchkeys,
Path Y negative-evidence indicators, ComplexityProfile sub-profile cells,
indicator column priors, and the RunHistory decision trace.

Populated when the user runs ``Auto-configure (Ctrl+A)`` from the app.
Empty until then.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from goldenmatch.tui.engine import ControllerTelemetry


_HEALTH_COLOR = {
    "green": "#2ecc71",
    "yellow": "#d4a017",
    "red": "#e74c3c",
}

_STOP_REASON_HINTS = {
    "green": "Iteration produced a GREEN profile.",
    "converged": "Profile distance to prior iteration fell below epsilon.",
    "budget_iterations": "Max-iteration budget reached before reaching GREEN.",
    "budget_time": "Wall-clock budget exhausted.",
    "policy_satisfied": "Policy returned no refit; current config acceptable on non-green profile.",
    "policy_no_progress": "Policy proposed identical config twice in a row.",
    "oscillating": "Same (config, rule) pair repeated within a 4-iteration window.",
    "cancelled": "Run was cancelled (KeyboardInterrupt).",
}


class ControllerTab(Static):
    """AutoConfigController telemetry view."""

    DEFAULT_CSS = """
    ControllerTab {
        height: 1fr;
    }
    #controller-empty {
        padding: 2 4;
        color: #8892a0;
    }
    #controller-header {
        padding: 1 2;
        color: #f0f0f0;
    }
    #committed-config {
        padding: 0 2 1 2;
        color: #f0f0f0;
    }
    #profile-strip {
        padding: 0 2 1 2;
        color: #f0f0f0;
    }
    .section-title {
        color: #d4a017;
        text-style: bold;
        padding: 1 2 0 2;
    }
    #priors-table, #decisions-table {
        height: auto;
        max-height: 16;
        margin: 0 2;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._telemetry: ControllerTelemetry | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                "[dim]Press [bold #d4a017]Ctrl+A[/] to auto-configure from "
                "the loaded data. Controller decisions, indicator priors, and "
                "negative-evidence (Path Y) will appear here.[/dim]",
                id="controller-empty",
            )
            yield Static("", id="controller-header")
            yield Static("[bold #d4a017]Committed config[/]", classes="section-title")
            yield Static("", id="committed-config")
            yield Static("[bold #d4a017]Complexity profile[/]", classes="section-title")
            yield Static("", id="profile-strip")
            yield Static(
                "[bold #d4a017]Indicator column priors[/]",
                classes="section-title",
                id="priors-title",
            )
            yield DataTable(id="priors-table")
            yield Static(
                "[bold #d4a017]Refit decisions[/]",
                classes="section-title",
                id="decisions-title",
            )
            yield DataTable(id="decisions-table")

    def on_mount(self) -> None:
        # Hide everything but the placeholder until the user runs auto-config.
        for wid in ("controller-header", "committed-config", "profile-strip"):
            self.query_one(f"#{wid}", Static).display = False
        for wid in ("priors-title", "decisions-title"):
            self.query_one(f"#{wid}", Static).display = False
        priors = self.query_one("#priors-table", DataTable)
        priors.display = False
        priors.add_columns("Column", "Identity", "Corruption")
        decisions = self.query_one("#decisions-table", DataTable)
        decisions.display = False
        decisions.add_columns("Iter", "Rule", "Rationale", "Wall (ms)")

    def update_telemetry(self, telemetry: ControllerTelemetry | None) -> None:
        """Render controller telemetry, or show the placeholder when None."""
        empty = self.query_one("#controller-empty", Static)
        header = self.query_one("#controller-header", Static)
        committed = self.query_one("#committed-config", Static)
        strip = self.query_one("#profile-strip", Static)
        priors_title = self.query_one("#priors-title", Static)
        priors_table = self.query_one("#priors-table", DataTable)
        decisions_title = self.query_one("#decisions-title", Static)
        decisions_table = self.query_one("#decisions-table", DataTable)

        if telemetry is None:
            empty.display = True
            for widget in (header, committed, strip, priors_title, priors_table, decisions_title, decisions_table):
                widget.display = False
            return

        self._telemetry = telemetry
        empty.display = False
        header.display = True
        committed.display = True
        strip.display = True

        header.update(self._render_header(telemetry))
        committed.update(self._render_committed(telemetry))
        strip.update(self._render_strip(telemetry))

        # Indicator priors
        priors_table.clear()
        if telemetry.column_priors:
            priors_title.display = True
            priors_table.display = True
            for col, p in sorted(
                telemetry.column_priors.items(),
                key=lambda kv: (-kv[1]["identity_score"], -kv[1]["corruption_score"]),
            ):
                if p["identity_score"] == 0.0 and p["corruption_score"] == 0.0:
                    continue
                priors_table.add_row(
                    col,
                    f"{p['identity_score']:.2f}",
                    f"{p['corruption_score']:.2f}",
                )
        else:
            priors_title.display = False
            priors_table.display = False

        # Decisions
        decisions = _decisions_list(telemetry.history)
        decisions_table.clear()
        if decisions:
            decisions_title.display = True
            decisions_table.display = True
            for d in decisions:
                decisions_table.add_row(
                    str(d["iteration"]),
                    d["rule_name"],
                    _truncate(d["rationale"], 70),
                    str(d["wall_clock_ms"]),
                )
        else:
            decisions_title.display = False
            decisions_table.display = False

    def _render_header(self, t: ControllerTelemetry) -> str:
        verdict = _health(t.profile)
        color = _HEALTH_COLOR.get(verdict or "", "#8892a0")
        stop = _stop_reason(t.history)
        stop_hint = _STOP_REASON_HINTS.get(stop or "", "")
        parts = [
            f"[{color}]health · {verdict or 'unknown'}[/]",
            f"stop · [bold]{stop.replace('_', ' ') if stop else 'unknown'}[/]",
        ]
        elapsed = _elapsed_ms(t.history)
        if elapsed is not None:
            parts.append(f"elapsed · [bold]{elapsed:.0f}ms[/]")
        drift = _drift(t.history)
        if drift is not None:
            parts.append(f"drift · [bold]{drift:.2f}[/]")
        if t.recorded_at:
            parts.append(f"[dim]at {t.recorded_at[:19]}[/dim]")
        line = "   ".join(parts)
        if stop_hint:
            line += f"\n[dim]{stop_hint}[/dim]"
        return line

    def _render_committed(self, t: ControllerTelemetry) -> str:
        cfg = t.committed_config
        if cfg is None:
            return "[dim]No committed config available.[/dim]"
        try:
            matchkeys = cfg.get_matchkeys()
        except Exception:
            return "[dim]Could not read committed config.[/dim]"
        if not matchkeys:
            return "[dim]Committed config has no matchkeys.[/dim]"

        lines: list[str] = []
        for mk in matchkeys:
            ne = getattr(mk, "negative_evidence", None) or []
            threshold = mk.threshold if mk.threshold is not None else None
            thr_str = f" · threshold {threshold:.2f}" if threshold is not None else ""
            ne_badge = f" · [bold magenta]Path Y · {len(ne)} NE[/]" if ne else ""
            lines.append(
                f"  [#d4a017]●[/] [bold]{mk.name}[/] "
                f"[#8892a0]({mk.type})[/]{thr_str}{ne_badge}"
            )
            field_bits = []
            for f in mk.fields:
                col = f.column or f.field or "—"
                scorer_bit = f"·{f.scorer}" if f.scorer else ""
                weight_bit = f"·w{f.weight:.1f}" if f.weight is not None else ""
                field_bits.append(f"{col}{scorer_bit}{weight_bit}")
            if field_bits:
                lines.append(f"    [dim]{' '.join(field_bits)}[/dim]")
            for nf in ne:
                lines.append(
                    f"    [magenta]NE[/] {nf.field} · {nf.scorer} · "
                    f"threshold {nf.threshold:.2f} · "
                    f"penalty [magenta]-{nf.penalty:.2f}[/]"
                )
        return "\n".join(lines)

    def _render_strip(self, t: ControllerTelemetry) -> str:
        if t.profile is None:
            return "[dim]No profile recorded.[/dim]"
        scoring = getattr(t.profile, "scoring", None)
        blocking = getattr(t.profile, "blocking", None)
        cluster = getattr(t.profile, "cluster", None)
        cells: list[str] = []
        if scoring is not None:
            cells.append(f"pairs [bold]{_num(getattr(scoring, 'n_pairs_scored', 0))}[/]")
            cells.append(f"above thr [bold]{_pct(getattr(scoring, 'mass_above_threshold', 0))}[/]")
            cells.append(f"borderline [bold]{_pct(getattr(scoring, 'mass_in_borderline', 0))}[/]")
        if blocking is not None:
            cells.append(f"blocks [bold]{_num(getattr(blocking, 'n_blocks', 0))}[/]")
            cells.append(f"p99 block [bold]{_num(getattr(blocking, 'block_sizes_p99', 0))}[/]")
        if cluster is not None:
            cells.append(f"clusters [bold]{_num(getattr(cluster, 'n_clusters', 0))}[/]")
            cells.append(f"transitivity [bold]{_pct(getattr(cluster, 'transitivity_rate', 0))}[/]")
        if not cells:
            return "[dim]Profile sub-profiles were not populated.[/dim]"
        return "   ".join(cells)


# ── Helpers ──────────────────────────────────────────────────────────


def _health(profile: Any) -> str | None:
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


def _elapsed_ms(history: Any) -> float | None:
    if history is None:
        return None
    elapsed = getattr(history, "elapsed", None)
    if elapsed is None:
        return None
    try:
        return elapsed.total_seconds() * 1000.0
    except Exception:
        return None


def _drift(history: Any) -> float | None:
    if history is None:
        return None
    d = getattr(history, "full_vs_sample_drift", None)
    return float(d) if d is not None else None


def _decisions_list(history: Any) -> list[dict]:
    if history is None:
        return []
    out: list[dict] = []
    for entry in getattr(history, "entries", []):
        if entry.decision is None:
            continue
        out.append({
            "iteration": int(entry.iteration),
            "rule_name": entry.decision.rule_name,
            "rationale": entry.decision.rationale,
            "wall_clock_ms": int(getattr(entry, "wall_clock_ms", 0) or 0),
        })
    return out


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
