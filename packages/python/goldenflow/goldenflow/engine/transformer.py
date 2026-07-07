from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig
from goldenflow.connectors.file import read_file, write_file
from goldenflow.engine.frame import Frame, to_frame
from goldenflow.engine.manifest import Manifest, TransformRecord
from goldenflow.engine.profiler_bridge import profile_dataframe
from goldenflow.engine.selector import select_transforms
from goldenflow.transforms import TransformInfo, get_transform, parse_transform_name


@dataclass
class TransformResult:
    df: pl.DataFrame
    manifest: Manifest


class TransformEngine:
    def __init__(self, config: GoldenFlowConfig | None = None):
        self.config = config or GoldenFlowConfig()

    def transform_file(
        self,
        path: Path,
        output_dir: Path | None = None,
    ) -> TransformResult:
        """Transform a file. Optionally write output files."""
        df = read_file(path)
        t0 = time.monotonic()
        result = self.transform_df(df, source=str(path))
        elapsed = time.monotonic() - t0

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            stem = path.stem
            out_path = output_dir / f"{stem}_transformed{path.suffix}"
            manifest_path = output_dir / f"{stem}_manifest.json"
            write_file(result.df, out_path)
            result.manifest.save(manifest_path)

        # Record run in history
        try:
            from goldenflow.history import RunRecord, generate_run_id, save_run
            record = RunRecord(
                run_id=generate_run_id(),
                source=str(path),
                timestamp=result.manifest.created_at,
                rows=result.df.shape[0],
                columns=result.df.shape[1],
                transforms_applied=len(result.manifest.records),
                errors=len(result.manifest.errors),
                duration_seconds=round(elapsed, 3),
            )
            save_run(record)
        except Exception:
            pass  # History tracking is best-effort

        return result

    def transform_df(
        self,
        df: pl.DataFrame,
        source: str = "<dataframe>",
    ) -> TransformResult:
        """Transform a DataFrame. The public API takes/returns ``pl.DataFrame``; the
        engine operates on a backend-agnostic :class:`Frame` internally (the seam for
        making Polars optional — see the Polars-eviction design doc)."""
        # Phase 1: the columnar (Polars-free) engine. When opted in via
        # GOLDENFLOW_ENGINE=columnar AND the config is fully owned-string, the
        # transform EXECUTION runs on the native arrow-free chain with no Polars
        # (only the boundary column extract/assemble touches it — Phase 2 removes
        # that). Byte-identical to the Polars engine; anything it can't handle yet
        # declines here and falls through to the Polars path.
        from goldenflow.engine import columnar as _columnar
        if _columnar.columnar_engine_selected() and _columnar.config_is_columnar_ready(self.config):
            out, manifest = _columnar.transform(df, self.config, source=source)
            return TransformResult(df=out, manifest=manifest)

        manifest = Manifest(source=source)
        frame: Frame = to_frame(df)

        if self.config.transforms:
            frame = self._apply_config_transforms(frame, manifest)
        else:
            frame = self._apply_auto_transforms(frame, manifest, source=source)

        # Apply splits (dataframe-mode, multi-column output)
        for split in self.config.splits:
            if split.source not in frame.columns:
                continue
            info = get_transform(split.method)
            if info and info.mode == "dataframe":
                frame = frame.replace_native(info.func(frame.native, split.source))

        # Apply renames
        for old, new in self.config.renames.items():
            if old in frame.columns:
                frame = frame.rename({old: new})

        # Apply drops
        drop_cols = [c for c in self.config.drop if c in frame.columns]
        if drop_cols:
            frame = frame.drop(drop_cols)

        # Apply filters
        for filt in self.config.filters:
            if filt.column in frame.columns:
                frame = self._apply_filter(frame, filt.column, filt.condition)

        # Apply dedup
        if self.config.dedup:
            dedup_cols = [c for c in self.config.dedup.columns if c in frame.columns]
            if dedup_cols:
                before = frame.height
                frame = frame.unique(subset=dedup_cols, keep=self.config.dedup.keep)
                after = frame.height
                if before != after:
                    manifest.add_record(TransformRecord(
                        column=",".join(dedup_cols),
                        transform="dedup",
                        affected_rows=before - after,
                        total_rows=before,
                    ))

        return TransformResult(df=frame.native, manifest=manifest)

    def _apply_config_transforms(
        self, frame: Frame, manifest: Manifest
    ) -> Frame:
        """Apply transforms specified in config."""
        from rich.progress import Progress, SpinnerColumn, TextColumn

        # Lazy-import LLM module if any op mentions "llm"
        all_ops = [op for spec in self.config.transforms for op in spec.ops]
        if any("llm" in op for op in all_ops):
            try:
                import goldenflow.llm.corrector  # noqa: F401 — registers LLM transforms
            except ImportError:
                pass

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Transforming...", total=len(self.config.transforms))
            for spec in self.config.transforms:
                progress.update(task, description=f"Transforming {spec.column}...")
                if spec.column not in frame.columns:
                    progress.advance(task)
                    continue
                ops: list[tuple[TransformInfo, list[str]]] = []
                for op_raw in spec.ops:
                    name, params = parse_transform_name(op_raw)
                    info = get_transform(name)
                    if info is None:
                        manifest.add_error(
                            column=spec.column, transform=name, row=-1,
                            error=f"Transform '{name}' not found in registry",
                        )
                        continue
                    ops.append((info, params))
                frame = self._apply_column_ops(frame, spec.column, ops, manifest)
                progress.advance(task)
        return frame

    def _apply_auto_transforms(
        self, frame: Frame, manifest: Manifest, source: str = ""
    ) -> Frame:
        """Auto-detect and apply transforms based on column profiling."""
        import os

        from rich.progress import Progress, SpinnerColumn, TextColumn

        file_path = source if source and source != "<dataframe>" else ""
        profile = profile_dataframe(frame.native, file_path=file_path)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Auto-profiling...", total=len(profile.columns))
            for col_profile in profile.columns:
                progress.update(task, description=f"Auto-transforming {col_profile.name}...")
                selected = select_transforms(col_profile)
                ops = [(info, []) for info in selected]
                frame = self._apply_column_ops(frame, col_profile.name, ops, manifest)
                progress.advance(task)

        if os.environ.get("GOLDENFLOW_LLM") == "1":
            try:
                from goldenflow.llm.corrector import category_llm_correct  # noqa: F401
                llm_info = get_transform("category_llm_correct")
                if llm_info:
                    for col_profile in profile.columns:
                        if col_profile.inferred_type == "string" and col_profile.unique_pct <= 0.1:
                            frame = self._apply_single_transform(frame, col_profile.name, llm_info, [], manifest)
            except ImportError:
                pass

        return frame

    def _apply_column_ops(
        self,
        frame: Frame,
        column: str,
        ops: list[tuple[TransformInfo, list[str]]],
        manifest: Manifest,
    ) -> Frame:
        """Apply an ordered list of ``(info, params)`` to ``column``. When fusion is
        on (default; native available), maximal runs of owned kernels of a single
        dtype are fused into ONE native Arrow round-trip (Pillar-1 of the Rust
        cutover): string->string kernels on a Utf8 column, or f64->f64 numeric
        kernels (round/clamp/abs_value/fill_zero) on a Float64 column. Everything
        else takes the per-transform path unchanged. The fusable set is
        dtype-aware AND symbol-aware, and is recomputed as the run advances so a
        parser that changes the column dtype mid-chain (e.g. currency_strip:
        str->f64) lets the following numeric ops fuse too."""
        from goldenflow.transforms._chain import (
            fusable_f64_names,
            fusable_names,
            fused_enabled,
            is_fusable,
        )

        fused_on = fused_enabled()

        def _available_for(dtype) -> frozenset[str]:
            if not fused_on:
                return frozenset()
            if dtype in (pl.String, pl.Utf8):
                return fusable_names()
            if dtype == pl.Float64:
                return fusable_f64_names()
            return frozenset()

        n = len(ops)
        i = 0
        while i < n:
            available = _available_for(frame.dtype(column))
            info, params = ops[i]
            if is_fusable(info.name, params, available):
                # Extend the maximal fusable run starting at i.
                j = i
                while j < n and is_fusable(ops[j][0].name, ops[j][1], available):
                    j += 1
                if j - i >= 2:
                    applied = self._apply_fused_run(frame, column, ops[i:j], manifest)
                    if applied is not None:
                        frame = applied
                        i = j
                        continue
                    # native declined (e.g. no pyarrow) -> fall through per-op.
            frame = self._apply_single_transform(frame, column, info, params, manifest)
            i += 1
        return frame

    def _apply_fused_run(
        self,
        frame: Frame,
        column: str,
        run: list[tuple[TransformInfo, list[str]]],
        manifest: Manifest,
    ) -> Frame | None:
        """Apply a run of fusable ops via one native chain pass. Emits one audit
        record per op with the EXACT affected-row count from the kernel and
        per-step before/after samples from a cheap head(3) replay through the SAME
        owned kernels (byte-identical to the fused output). Fusable kernels are
        ``mode='expr'`` or ``'series'`` (never ``'dataframe'``), so the replay
        dispatches on mode. Returns the new frame, or ``None`` if the native path
        declined (caller falls back to the per-op path)."""
        from goldenflow.transforms._chain import apply_chain_native

        ops_spec = [(info.name, [str(p) for p in params]) for info, params in run]
        res = apply_chain_native(frame.column(column), ops_spec)
        if res is None:
            return None
        new_series, changed = res
        total_rows = frame.height
        # Per-step samples come from a cheap 3-row replay through the transform
        # funcs (Polars dispatch — the `.native` escape hatch).
        sample = frame.native.head(3).select(pl.col(column))
        for (info, params), n_changed in zip(run, changed):
            before = sample[column].head(3).cast(pl.Utf8).to_list()
            # Match the per-transform param handling EXACTLY: series-mode casts
            # params (round n=int, clamp bounds=float), expr-mode passes them raw
            # (pad_left's pad char "0" must stay a str, not be int-cast). Mixing
            # these up breaks the sample replay for parameterized expr ops.
            if info.mode == "series":
                typed = self._cast_params(params) if params else []
                sample = sample.with_columns(info.func(sample[column], *typed).alias(column))
            else:  # expr — raw string params, exactly like _apply_single_transform_body
                sample = sample.with_columns(info.func(column, *params).alias(column))
            after = sample[column].head(3).cast(pl.Utf8).to_list()
            manifest.add_record(TransformRecord(
                column=column,
                transform=info.name,
                affected_rows=int(n_changed),
                total_rows=total_rows,
                sample_before=before,
                sample_after=after,
            ))
        return frame.with_column(column, new_series)

    def _apply_single_transform(
        self,
        frame: Frame,
        column: str,
        info: TransformInfo,
        params: list[str],
        manifest: Manifest,
    ) -> Frame:
        """Apply a single transform to a column, recording results in manifest."""
        # Optional cross-package bench instrumentation: when goldenmatch is
        # installed AND a bench_capture is currently active, wrap each
        # transform application in a stage(name) so the per-transform wall +
        # ru_maxrss are attributed in the bench dict. Stage name encodes
        # column + transform so the same transform on different columns is
        # diff'able. No-op when goldenmatch isn't importable (standalone
        # goldenflow use) or no bench_capture is active. Used to diagnose
        # which transform dominated the pipeline_prep_transform wall at the
        # QIS 10M-bucket-realistic bench (~316s / GoldenFlow at 10M).
        try:
            from goldenmatch.core.bench import stage as _bench_stage
            _stage_cm = _bench_stage(f"gf:{column}:{info.name}")
        except ImportError:
            from contextlib import nullcontext
            _stage_cm = nullcontext()

        with _stage_cm:
            return self._apply_single_transform_body(
                frame, column, info, params, manifest,
            )

    def _apply_single_transform_body(
        self,
        frame: Frame,
        column: str,
        info: TransformInfo,
        params: list[str],
        manifest: Manifest,
    ) -> Frame:
        """Inner body of _apply_single_transform, factored out so the bench
        stage wrapper can clamp around the actual work without adding
        indentation to the existing logic. The transform DISPATCH (expr / series /
        dataframe) is the remaining Polars coupling, reached via ``frame.native``."""
        before_sample = frame.column(column).head(3).cast(pl.Utf8).to_list()
        total_rows = frame.height

        try:
            if info.mode == "expr":
                expr = info.func(column, *params) if params else info.func(column)
                new_frame: Frame = frame.replace_native(
                    frame.native.with_columns(expr.alias(column))
                )
            elif info.mode == "dataframe":
                # DataFrame-mode transforms (split_name, split_address, etc.)
                new_frame = frame.replace_native(info.func(frame.native, column))
            else:
                series = frame.column(column)
                typed_params = self._cast_params(params)
                new_series = info.func(series, *typed_params) if typed_params else info.func(series)
                if isinstance(new_series, tuple):
                    # e.g. initial_expand returns (series, flagged_rows)
                    new_series, flagged = new_series
                    if flagged:
                        for row_idx in flagged:
                            manifest.add_error(
                                column=column, transform=info.name, row=row_idx,
                                error="Flagged for review",
                            )
                new_frame = frame.with_column(column, new_series)

            after_sample = new_frame.column(column).head(3).cast(pl.Utf8).to_list()

            # Count affected rows
            try:
                changed = (
                    frame.column(column).cast(pl.Utf8) != new_frame.column(column).cast(pl.Utf8)
                ).sum()
            except Exception:
                changed = total_rows

            manifest.add_record(TransformRecord(
                column=column,
                transform=info.name,
                affected_rows=changed,
                total_rows=total_rows,
                sample_before=before_sample,
                sample_after=after_sample,
            ))
            return new_frame

        except Exception as e:
            manifest.add_error(
                column=column, transform=info.name, row=-1, error=str(e)
            )
            return frame  # preserve original on failure

    @staticmethod
    def _cast_params(params: list[str]) -> list:
        """Try to cast string params to int or float."""
        result = []
        for p in params:
            try:
                result.append(int(p))
            except ValueError:
                try:
                    result.append(float(p))
                except ValueError:
                    result.append(p)
        return result

    @staticmethod
    def _apply_filter(frame: Frame, column: str, condition: str) -> Frame:
        if condition == "not_null":
            return frame.filter_not_null(column)
        if condition.startswith("after:"):
            return frame.filter_cmp(column, ">", condition.split(":", 1)[1])
        if condition.startswith("before:"):
            return frame.filter_cmp(column, "<", condition.split(":", 1)[1])
        return frame
