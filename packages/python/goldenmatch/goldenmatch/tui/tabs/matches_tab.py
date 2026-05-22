"""Matches tab -- cluster/match viewer with color-coded scores."""

from __future__ import annotations

import polars as pl
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from goldenmatch.tui.engine import EngineResult


class MatchesTab(Static):
    """Match preview with cluster list and detail drill-down."""

    DEFAULT_CSS = """
    MatchesTab {
        height: 1fr;
    }
    #cluster-table {
        height: 40%;
        border: solid $primary;
    }
    #detail-table {
        height: 55%;
        border: solid $accent;
    }
    .no-results {
        padding: 2;
    }
    """

    BINDINGS = [
        # Phase 4 follow-up (#437 surface sync, 2026-05-22): open the
        # MatchesCorrectionModal on the highlighted detail-table row to
        # file a pair-level (approve/reject) Correction.
        Binding("c", "correct_pair", "Correct pair"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._result: EngineResult | None = None
        self._data: pl.DataFrame | None = None
        self._clusters: dict[int, dict] | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "[dim]Run matching from the Config tab to see results here.[/dim]",
                id="no-results-msg",
                classes="no-results",
            )
            yield DataTable(id="cluster-table")
            yield DataTable(id="detail-table")

    def on_mount(self) -> None:
        cluster_table = self.query_one("#cluster-table", DataTable)
        cluster_table.add_columns("Cluster ID", "Size", "Top Score", "Confidence")
        cluster_table.display = False
        cluster_table.cursor_type = "row"

        detail_table = self.query_one("#detail-table", DataTable)
        detail_table.display = False

    def update_results(self, result: EngineResult, data: pl.DataFrame) -> None:
        """Populate cluster list and detail tables from engine results."""
        self._result = result
        self._data = data
        self._clusters = result.clusters

        # Hide placeholder
        no_msg = self.query_one("#no-results-msg", Static)
        no_msg.display = False

        # Build cluster list
        cluster_table = self.query_one("#cluster-table", DataTable)
        cluster_table.clear()
        cluster_table.display = True

        # Build a map of top scores per cluster from scored_pairs
        cluster_top_scores: dict[int, float] = {}
        # Map row_id -> cluster_id
        row_to_cluster: dict[int, int] = {}
        for cid, cinfo in result.clusters.items():
            for mid in cinfo["members"]:
                row_to_cluster[mid] = cid

        for id_a, id_b, score in result.scored_pairs:
            cid_a = row_to_cluster.get(id_a)
            if cid_a is not None:
                cluster_top_scores[cid_a] = max(
                    cluster_top_scores.get(cid_a, 0.0), score
                )

        # Only show multi-member clusters
        multi_clusters = [
            (cid, cinfo)
            for cid, cinfo in result.clusters.items()
            if cinfo["size"] > 1
        ]
        multi_clusters.sort(key=lambda x: x[1]["size"], reverse=True)

        for cid, cinfo in multi_clusters:
            top_score = cluster_top_scores.get(cid, 0.0)
            score_str = f"{top_score:.3f}"
            # Color code: green >0.9, yellow 0.7-0.9, red <0.7
            if top_score > 0.9:
                score_str = f"[green]{score_str}[/green]"
            elif top_score >= 0.7:
                score_str = f"[yellow]{score_str}[/yellow]"
            else:
                score_str = f"[red]{score_str}[/red]"
            conf = cinfo.get("confidence", 0.0)
            conf_str = f"{conf:.2f}"
            if conf >= 0.8:
                conf_str = f"[green]{conf_str}[/green]"
            elif conf >= 0.5:
                conf_str = f"[yellow]{conf_str}[/yellow]"
            else:
                conf_str = f"[red]{conf_str}[/red]"
            cluster_table.add_row(str(cid), str(cinfo["size"]), score_str, conf_str)

        # Clear detail
        detail_table = self.query_one("#detail-table", DataTable)
        detail_table.clear(columns=True)
        detail_table.display = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show cluster members when a cluster row is selected."""
        if self._result is None or self._data is None:
            return
        if event.data_table.id != "cluster-table":
            return

        # Get cluster ID from the selected row
        cluster_table = self.query_one("#cluster-table", DataTable)
        row_key = event.row_key
        row_data = cluster_table.get_row(row_key)
        try:
            cluster_id = int(row_data[0])
        except (ValueError, IndexError):
            return

        cluster_info = self._clusters.get(cluster_id)
        if cluster_info is None:
            return

        member_ids = cluster_info["members"]
        member_df = self._data.filter(pl.col("__row_id__").is_in(member_ids))

        # Show in detail table
        detail_table = self.query_one("#detail-table", DataTable)
        detail_table.clear(columns=True)
        detail_table.display = True

        # Add columns (skip internal columns)
        display_cols = [c for c in member_df.columns if not c.startswith("__")]
        for col in display_cols:
            detail_table.add_column(col)

        # Add rows
        for row in member_df.iter_rows(named=True):
            values = [str(row.get(c, "")) for c in display_cols]
            detail_table.add_row(*values)

    def action_correct_pair(self) -> None:
        """Open MatchesCorrectionModal for two members of the currently
        selected cluster.

        Pair-level corrections take two record IDs. The detail-table
        shows the cluster's members; we use the two highest-row-id
        members of the selected cluster as the pair (operator can
        narrow this later via a follow-up "select pair" UI). When the
        detail table isn't populated yet (no cluster selected), the
        action exits silently.
        """
        from goldenmatch.tui.screens.matches_correction_modal import (
            MatchesCorrectionModal,
        )

        if self._clusters is None or self._data is None:
            return
        try:
            cluster_table = self.query_one("#cluster-table", DataTable)
        except Exception:
            return
        if cluster_table.row_count == 0:
            return
        row_idx = cluster_table.cursor_row
        if row_idx is None:
            return
        try:
            row = cluster_table.get_row_at(row_idx)
            cluster_id = int(str(row[0]))
        except (ValueError, TypeError, IndexError):
            return

        cluster_info = self._clusters.get(cluster_id) if self._clusters else None
        if cluster_info is None:
            return
        members = list(cluster_info.get("members", []))
        if len(members) < 2:
            return

        # Pick the first two members as the representative pair. A
        # later iteration could surface a 2-row selection UI.
        id_a, id_b = members[0], members[1]
        # Try to pull the pair's score from the cluster's pair_scores
        # map for context in the modal title bar.
        pair_scores = cluster_info.get("pair_scores", {}) or {}
        score = pair_scores.get((min(id_a, id_b), max(id_a, id_b)))

        dataset = (
            getattr(self.app, "memory_dataset", None)
            or getattr(self.app, "current_dataset", None)
            or "tui"
        )
        modal = MatchesCorrectionModal(
            id_a=int(id_a),
            id_b=int(id_b),
            score=float(score) if score is not None else None,
            dataset=dataset,
        )
        self.app.push_screen(modal)
