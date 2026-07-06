from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig
from goldenflow.connectors.file import read_file, write_file
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
        """Transform a DataFrame."""
        manifest = Manifest(source=source)

        if self.config.transforms:
            df = self._apply_config_transforms(df, manifest)
        else:
            df = self._apply_auto_transforms(df, manifest, source=source)

        # Apply splits
        for split in self.config.splits:
            if split.source not in df.columns:
                continue
            info = get_transform(split.method)
            if info and info.mode == "dataframe":
                df = info.func(df, split.source)

        # Apply renames
        for old, new in self.config.renames.items():
            if old in df.columns:
                df = df.rename({old: new})

        # Apply drops
        drop_cols = [c for c in self.config.drop if c in df.columns]
        if drop_cols:
            df = df.drop(drop_cols)

        # Apply filters
        for filt in self.config.filters:
            if filt.column in df.columns:
                df = self._apply_filter(df, filt.column, filt.condition)

        # Apply dedup
        if self.config.dedup:
            dedup_cols = [c for c in self.config.dedup.columns if c in df.columns]
            if dedup_cols:
                before = df.shape[0]
                df = df.unique(subset=dedup_cols, keep=self.config.dedup.keep)
                after = df.shape[0]
                if before != after:
                    manifest.add_record(TransformRecord(
                        column=",".join(dedup_cols),
                        transform="dedup",
                        affected_rows=before - after,
                        total_rows=before,
                    ))

        return TransformResult(df=df, manifest=manifest)

    def _apply_config_transforms(
        self, df: pl.DataFrame, manifest: Manifest
    ) -> pl.DataFrame:
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
                if spec.column not in df.columns:
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
                df = self._apply_column_ops(df, spec.column, ops, manifest)
                progress.advance(task)
        return df

    def _apply_auto_transforms(
        self, df: pl.DataFrame, manifest: Manifest, source: str = ""
    ) -> pl.DataFrame:
        """Auto-detect and apply transforms based on column profiling."""
        import os

        from rich.progress import Progress, SpinnerColumn, TextColumn

        file_path = source if source and source != "<dataframe>" else ""
        profile = profile_dataframe(df, file_path=file_path)
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
                df = self._apply_column_ops(df, col_profile.name, ops, manifest)
                progress.advance(task)

        if os.environ.get("GOLDENFLOW_LLM") == "1":
            try:
                from goldenflow.llm.corrector import category_llm_correct  # noqa: F401
                llm_info = get_transform("category_llm_correct")
                if llm_info:
                    for col_profile in profile.columns:
                        if col_profile.inferred_type == "string" and col_profile.unique_pct <= 0.1:
                            df = self._apply_single_transform(df, col_profile.name, llm_info, [], manifest)
            except ImportError:
                pass

        return df

    def _apply_column_ops(
        self,
        df: pl.DataFrame,
        column: str,
        ops: list[tuple[TransformInfo, list[str]]],
        manifest: Manifest,
    ) -> pl.DataFrame:
        """Apply an ordered list of ``(info, params)`` to ``column``. When
        ``GOLDENFLOW_FUSED_APPLY`` is on (and native is available and the column is
        string), maximal runs of owned string->string no-arg kernels are fused into
        ONE native Arrow round-trip (Pillar-1 of the Rust cutover); everything else
        takes the per-transform path unchanged. Default-off, so behavior is
        byte-identical to the per-transform path until opted in."""
        from goldenflow.transforms._chain import FUSABLE_KERNELS, fused_enabled

        fuse = fused_enabled() and df.schema.get(column) in (pl.String, pl.Utf8)
        n = len(ops)
        i = 0
        while i < n:
            info, params = ops[i]
            if fuse and not params and info.name in FUSABLE_KERNELS:
                # Extend the maximal fusable run starting at i.
                j = i
                while j < n and not ops[j][1] and ops[j][0].name in FUSABLE_KERNELS:
                    j += 1
                if j - i >= 2:
                    applied = self._apply_fused_run(df, column, ops[i:j], manifest)
                    if applied is not None:
                        df = applied
                        i = j
                        continue
                    # native declined (e.g. no pyarrow) -> fall through per-op.
            df = self._apply_single_transform(df, column, info, params, manifest)
            i += 1
        return df

    def _apply_fused_run(
        self,
        df: pl.DataFrame,
        column: str,
        run: list[tuple[TransformInfo, list[str]]],
        manifest: Manifest,
    ) -> pl.DataFrame | None:
        """Apply a run of fusable ops via one native chain pass. Emits one audit
        record per op with the EXACT affected-row count from the kernel and
        per-step before/after samples from a cheap head(3) replay through the SAME
        owned kernels (byte-identical to the fused output). Fusable kernels are
        ``mode='expr'`` or ``'series'`` (never ``'dataframe'``), so the replay
        dispatches on mode. Returns the new frame, or ``None`` if the native path
        declined (caller falls back to the per-op path)."""
        from goldenflow.transforms._chain import apply_chain_native

        names = [info.name for info, _ in run]
        res = apply_chain_native(df[column], names)
        if res is None:
            return None
        new_series, changed = res
        total_rows = df.shape[0]
        sample = df.head(3).select(pl.col(column))
        for (info, _params), n_changed in zip(run, changed):
            before = sample[column].head(3).cast(pl.Utf8).to_list()
            if info.mode == "series":
                sample = sample.with_columns(info.func(sample[column]).alias(column))
            else:  # expr
                sample = sample.with_columns(info.func(column).alias(column))
            after = sample[column].head(3).cast(pl.Utf8).to_list()
            manifest.add_record(TransformRecord(
                column=column,
                transform=info.name,
                affected_rows=int(n_changed),
                total_rows=total_rows,
                sample_before=before,
                sample_after=after,
            ))
        return df.with_columns(new_series.alias(column))

    def _apply_single_transform(
        self,
        df: pl.DataFrame,
        column: str,
        info: TransformInfo,
        params: list[str],
        manifest: Manifest,
    ) -> pl.DataFrame:
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
                df, column, info, params, manifest,
            )

    def _apply_single_transform_body(
        self,
        df: pl.DataFrame,
        column: str,
        info: TransformInfo,
        params: list[str],
        manifest: Manifest,
    ) -> pl.DataFrame:
        """Inner body of _apply_single_transform, factored out so the bench
        stage wrapper can clamp around the actual work without adding
        indentation to the existing logic."""
        before_sample = df[column].head(3).cast(pl.Utf8).to_list()
        total_rows = df.shape[0]

        try:
            if info.mode == "expr":
                expr = info.func(column, *params) if params else info.func(column)
                new_df = df.with_columns(expr.alias(column))
            elif info.mode == "dataframe":
                # DataFrame-mode transforms (split_name, split_address, etc.)
                new_df = info.func(df, column)
            else:
                series = df[column]
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
                new_df = df.with_columns(new_series.alias(column))

            after_sample = new_df[column].head(3).cast(pl.Utf8).to_list()

            # Count affected rows
            try:
                changed = (df[column].cast(pl.Utf8) != new_df[column].cast(pl.Utf8)).sum()
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
            return new_df

        except Exception as e:
            manifest.add_error(
                column=column, transform=info.name, row=-1, error=str(e)
            )
            return df  # preserve original on failure

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
    def _apply_filter(df: pl.DataFrame, column: str, condition: str) -> pl.DataFrame:
        if condition == "not_null":
            return df.filter(pl.col(column).is_not_null())
        if condition.startswith("after:"):
            date_str = condition.split(":", 1)[1]
            return df.filter(pl.col(column) > date_str)
        if condition.startswith("before:"):
            date_str = condition.split(":", 1)[1]
            return df.filter(pl.col(column) < date_str)
        return df
