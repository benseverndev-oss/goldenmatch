from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)


class ProfileTab(TabPane):
    """Column types, samples, and issues."""

    def __init__(self, df=None, profile=None):
        super().__init__("Profile", id="profile-tab")
        self._df = df
        self._profile = profile

    def compose(self) -> ComposeResult:
        if self._profile:
            table = DataTable()
            table.add_columns("Column", "Type", "Nulls", "Unique", "Sample")
            for col in self._profile.columns:
                table.add_row(
                    col.name,
                    col.inferred_type,
                    f"{col.null_count} ({col.null_pct:.0%})",
                    str(col.unique_count),
                    ", ".join(col.sample_values[:3]),
                )
            yield table
        else:
            yield Static("No data loaded. Use: goldenflow interactive <file>")


class TransformTab(TabPane):
    """Select columns, pick transforms, preview results."""

    def __init__(self, df=None, profile=None):
        super().__init__("Transform", id="transform-tab")
        self._df = df
        self._profile = profile
        self._selected_column: str | None = None
        self._selected_transform: str | None = None
        self._applied_df = None

    def compose(self) -> ComposeResult:
        if not self._profile:
            yield Static("No data loaded. Use: goldenflow interactive <file>")
            return

        from goldenflow.transforms import list_transforms
        all_transforms = list_transforms()

        with Horizontal():
            # Left panel: columns checkboxes
            with Vertical(id="column-list"):
                yield Label("Columns")
                table = DataTable(id="columns-table")
                table.add_columns("Select", "Column", "Type")
                for col in self._profile.columns:
                    table.add_row(
                        "[ ]",
                        col.name,
                        col.inferred_type,
                    )
                yield table

            # Right panel: transform selector + preview
            with Vertical(id="transform-panel"):
                yield Label("Transform")
                transform_options = [(t.name, t.name) for t in all_transforms]
                if not transform_options:
                    transform_options = [("(none)", "(none)")]
                yield Select(
                    options=transform_options,
                    id="transform-select",
                    prompt="Pick a transform",
                )
                yield Label("Preview (before → after)")
                yield RichLog(id="preview-log", highlight=True)
                yield Button("Apply Transforms", id="apply-btn", variant="primary")

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle column selection in the table."""
        if event.data_table.id == "columns-table":
            row_key = event.cell_key.row_key
            table = self.query_one("#columns-table", DataTable)
            # Toggle checkbox in column 0
            current = table.get_cell(row_key, table.columns[0].key)
            new_val = "[x]" if current == "[ ]" else "[ ]"
            table.update_cell(row_key, table.columns[0].key, new_val)
            # Track selected column (column 1)
            self._selected_column = str(table.get_cell(row_key, table.columns[1].key))
            self._update_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "transform-select":
            self._selected_transform = str(event.value) if event.value != Select.BLANK else None
            self._update_preview()

    def _update_preview(self) -> None:
        """Show before/after sample for selected column+transform."""
        try:
            log = self.query_one("#preview-log", RichLog)
        except Exception:
            return

        log.clear()
        if not self._selected_column or not self._selected_transform:
            log.write("Select a column and transform to preview.")
            return
        if self._df is None or self._selected_column not in self._df.columns:
            log.write("Column not found in data.")
            return

        from goldenflow.transforms import get_transform
        from goldenflow.engine.manifest import Manifest
        info = get_transform(self._selected_transform)
        if info is None:
            log.write(f"Transform '{self._selected_transform}' not found.")
            return

        # Show before
        try:
            import polars as pl
            before_list = self._df[self._selected_column].head(5).cast(pl.Utf8).to_list()
        except Exception:
            before_list = []

        # Apply transform to get after sample
        try:
            import polars as pl
            from goldenflow.engine.transformer import TransformEngine
            engine = TransformEngine()
            manifest = Manifest(source="preview")
            preview_df = self._df.head(5)
            new_df = engine._apply_single_transform(preview_df, self._selected_column, info, [], manifest)
            after_list = new_df[self._selected_column].cast(pl.Utf8).to_list()
        except Exception as e:
            log.write(f"Preview error: {e}")
            return

        log.write(f"[bold]Column:[/bold] {self._selected_column}")
        log.write(f"[bold]Transform:[/bold] {self._selected_transform}")
        log.write("")
        for b, a in zip(before_list, after_list):
            if b != a:
                log.write(f"  [red]{b}[/red] → [green]{a}[/green]")
            else:
                log.write(f"  {b} (unchanged)")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-btn":
            self._apply_transforms()

    def _apply_transforms(self) -> None:
        """Apply selected transforms to selected columns."""
        try:
            log = self.query_one("#preview-log", RichLog)
            table = self.query_one("#columns-table", DataTable)
        except Exception:
            return

        if self._df is None:
            log.write("No data loaded.")
            return

        from goldenflow.transforms import get_transform
        from goldenflow.engine.manifest import Manifest
        from goldenflow.engine.transformer import TransformEngine

        engine = TransformEngine()
        manifest = Manifest(source="tui-apply")
        df = self._df

        # Find checked columns
        applied_count = 0
        for row_key in table.rows:
            check_val = table.get_cell(row_key, table.columns[0].key)
            if check_val == "[x]":
                col_name = str(table.get_cell(row_key, table.columns[1].key))
                if self._selected_transform and col_name in df.columns:
                    info = get_transform(self._selected_transform)
                    if info:
                        df = engine._apply_single_transform(df, col_name, info, [], manifest)
                        applied_count += 1

        self._applied_df = df
        log.write(f"\n[green]Applied {self._selected_transform} to {applied_count} column(s).[/green]")
        # Pass result up to app
        self.app._transformed_df = df


class MapTab(TabPane):
    """Schema mapping editor."""

    def __init__(self, df=None, profile=None):
        super().__init__("Map", id="map-tab")
        self._df = df
        self._profile = profile
        self._target_df = None
        self._mappings = None

    def compose(self) -> ComposeResult:
        with Vertical():
            if not self._profile:
                yield Static("No source data loaded. Use: goldenflow interactive <file>")
                return

            # Source columns display
            with Horizontal():
                with Vertical(id="source-cols"):
                    yield Label("Source Columns")
                    src_table = DataTable(id="source-table")
                    src_table.add_columns("Column", "Type")
                    for col in self._profile.columns:
                        src_table.add_row(col.name, col.inferred_type)
                    yield src_table

                with Vertical(id="mapping-area"):
                    yield Label("Mappings (load target to populate)")
                    yield Button("Load Target File", id="load-target-btn")
                    yield Input(placeholder="Target file path...", id="target-path-input")
                    yield DataTable(id="mapping-table")
                    yield Button("Export Mapping", id="export-mapping-btn", variant="success")

    def _init_mapping_table(self) -> None:
        """Initialize the mapping table columns."""
        try:
            table = self.query_one("#mapping-table", DataTable)
            if not table.columns:
                table.add_columns("Source", "Target", "Confidence", "Tier")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load-target-btn":
            self._load_target()
        elif event.button.id == "export-mapping-btn":
            self._export_mapping()

    def _load_target(self) -> None:
        """Load target file and run SchemaMapper."""
        try:
            path_input = self.query_one("#target-path-input", Input)
            target_path = path_input.value.strip()
        except Exception:
            return

        if not target_path:
            return

        target_file = Path(target_path)
        if not target_file.exists():
            return

        try:
            from goldenflow.connectors.file import read_file
            from goldenflow.mapping.schema_mapper import SchemaMapper

            self._target_df = read_file(target_file)
            mapper = SchemaMapper()
            self._mappings = mapper.map(self._df, self._target_df)

            # Populate mapping table
            self._init_mapping_table()
            table = self.query_one("#mapping-table", DataTable)
            table.clear()
            for m in self._mappings:
                tier = "auto" if m.confidence >= 0.9 else "suggest" if m.confidence >= 0.6 else "skip"
                table.add_row(m.source, m.target, f"{m.confidence:.2f}", tier)
        except Exception as e:
            try:
                table = self.query_one("#mapping-table", DataTable)
                self._init_mapping_table()
                table.clear()
                table.add_row(f"Error: {e}", "", "", "")
            except Exception:
                pass

    def _export_mapping(self) -> None:
        """Export mapping config to a YAML file."""
        if not self._mappings:
            return

        try:
            from goldenflow.mapping.schema_mapper import SchemaMapper
            from goldenflow.config.loader import save_config

            mapper = SchemaMapper()
            cfg = mapper.to_config(self._mappings)
            out_path = Path("mapping_config.yaml")
            save_config(cfg, out_path)
        except Exception:
            pass


class ExportTab(TabPane):
    """Save cleaned data, config, and manifest."""

    def __init__(self):
        super().__init__("Export", id="export-tab")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Export Format")
            with RadioSet(id="format-radio"):
                yield RadioButton("CSV", id="fmt-csv", value=True)
                yield RadioButton("Parquet", id="fmt-parquet")
                yield RadioButton("JSON", id="fmt-json")

            yield Label("Output File Path")
            yield Input(placeholder="output/cleaned_data.csv", id="output-path-input")

            yield Label("Additional Outputs")
            yield Checkbox("Save config YAML", id="save-config-check")
            yield Checkbox("Save manifest JSON", id="save-manifest-check")

            yield Button("Export", id="export-btn", variant="primary")
            yield Static("", id="export-status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export-btn":
            self._do_export()

    def _do_export(self) -> None:
        """Perform the export."""
        try:
            status = self.query_one("#export-status", Static)
            path_input = self.query_one("#output-path-input", Input)
            out_path_str = path_input.value.strip()

            if not out_path_str:
                status.update("Please enter an output file path.")
                return

            out_path = Path(out_path_str)

            # Get the transformed df from app
            df = getattr(self.app, "_transformed_df", None)
            if df is None:
                # Try to get original df
                df = getattr(self.app, "_df", None)
            if df is None:
                status.update("No data to export. Load and transform data first.")
                return

            # Determine format from radio buttons
            fmt = "csv"
            try:
                radio_set = self.query_one("#format-radio", RadioSet)
                if radio_set.pressed_button:
                    btn_id = radio_set.pressed_button.id
                    if btn_id == "fmt-parquet":
                        fmt = "parquet"
                    elif btn_id == "fmt-json":
                        fmt = "json"
            except Exception:
                pass

            # Write output file
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if fmt == "csv":
                final_path = out_path.with_suffix(".csv")
                df.write_csv(final_path)
            elif fmt == "parquet":
                final_path = out_path.with_suffix(".parquet")
                df.write_parquet(final_path)
            elif fmt == "json":
                final_path = out_path.with_suffix(".json")
                df.write_json(final_path)
            else:
                df.write_csv(out_path)
                final_path = out_path

            # Optionally save config
            save_config = False
            save_manifest = False
            try:
                save_config = self.query_one("#save-config-check", Checkbox).value
                save_manifest = self.query_one("#save-manifest-check", Checkbox).value
            except Exception:
                pass

            if save_config:
                import goldenflow
                from goldenflow.config.loader import save_config as _save_cfg
                cfg = goldenflow.GoldenFlowConfig()
                cfg_path = final_path.with_suffix(".yaml")
                _save_cfg(cfg, cfg_path)

            if save_manifest:
                manifest = getattr(self.app, "_manifest", None)
                if manifest:
                    manifest_path = final_path.with_name(final_path.stem + "_manifest.json")
                    manifest.save(manifest_path)

            status.update(f"Exported to {final_path}")

        except Exception as e:
            try:
                status = self.query_one("#export-status", Static)
                status.update(f"Export error: {e}")
            except Exception:
                pass


class GoldenFlowApp(App):
    """GoldenFlow interactive TUI."""

    TITLE = "GoldenFlow"
    CSS = """
    TabbedContent { height: 100%; }
    Horizontal { height: 1fr; }
    Vertical { height: 1fr; }
    #column-list { width: 40%; }
    #transform-panel { width: 60%; }
    #source-cols { width: 30%; }
    #mapping-area { width: 70%; }
    """

    def __init__(self, path: Path | None = None):
        super().__init__()
        self._path = path
        self._df = None
        self._profile = None
        self._transformed_df = None
        self._manifest = None
        self.title = "GoldenFlow"

        if path and path.exists():
            from goldenflow.connectors.file import read_file
            from goldenflow.engine.profiler_bridge import profile_dataframe

            self._df = read_file(path)
            self._profile = profile_dataframe(self._df, file_path=str(path))

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            yield ProfileTab(df=self._df, profile=self._profile)
            yield TransformTab(df=self._df, profile=self._profile)
            yield MapTab(df=self._df, profile=self._profile)
            yield ExportTab()
        yield Footer()
