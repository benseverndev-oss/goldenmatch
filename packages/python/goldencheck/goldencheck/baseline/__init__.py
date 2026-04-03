"""Deep profiling baseline — learn-once, monitor-forever."""
from __future__ import annotations

from pathlib import Path
from typing import Union

import polars as pl

from goldencheck.baseline.models import BaselineProfile

__all__ = ["create_baseline", "load_baseline"]


def create_baseline(
    df_or_path: Union[pl.DataFrame, Path, str],
    *,
    source: str = "",
    skip: list[str] | None = None,
    sample_size: int = 500_000,
) -> BaselineProfile:
    """Create a deep-profiling :class:`~goldencheck.baseline.models.BaselineProfile`.

    Parameters
    ----------
    df_or_path:
        A Polars DataFrame **or** a file path (``str`` / :class:`~pathlib.Path`)
        to a CSV, Parquet, or Excel file.  When a path is supplied, the file is
        loaded with :func:`~goldencheck.engine.reader.read_file` and the
        ``source`` field is set to the resolved path string.
    source:
        Optional human-readable label to embed in the profile.  Ignored when
        *df_or_path* is a path (the path itself is used instead).
    skip:
        Technique names to omit.  Valid names:
        ``"semantic"``, ``"statistical"``, ``"constraints"``,
        ``"correlation"``, ``"patterns"``, ``"priors"``.
    sample_size:
        Maximum rows to profile.  If the DataFrame has more rows it is
        down-sampled deterministically (seed 42) before profiling.

    Returns
    -------
    BaselineProfile
        A fully-populated baseline profile.
    """
    # ------------------------------------------------------------------
    # Guard: scipy + numpy must be importable before any heavy work.
    # ------------------------------------------------------------------
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "scipy and numpy are required for deep-profiling baseline. "
            "Install them with: pip install 'goldencheck[baseline]'"
        ) from exc

    skip_set: set[str] = set(skip or [])
    path: Path | None = None

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    if isinstance(df_or_path, pl.DataFrame):
        df = df_or_path
        resolved_source = source
    else:
        path = Path(df_or_path)
        from goldencheck.engine.reader import read_file
        df = read_file(path)
        resolved_source = str(path)

    # ------------------------------------------------------------------
    # Sample if needed
    # ------------------------------------------------------------------
    if df.height > sample_size:
        from goldencheck.engine.sampler import maybe_sample
        df = maybe_sample(df, max_rows=sample_size)

    # ------------------------------------------------------------------
    # 1. Semantic types
    # ------------------------------------------------------------------
    semantic_types: dict[str, list[str]] = {}
    if "semantic" not in skip_set:
        from goldencheck.baseline.semantic import infer_semantic_types
        semantic_types = infer_semantic_types(df, use_embeddings=True)

    # ------------------------------------------------------------------
    # 2. Statistical profiles
    # ------------------------------------------------------------------
    from goldencheck.baseline.models import StatProfile
    stat_profiles: dict[str, StatProfile] = {}
    if "statistical" not in skip_set:
        from goldencheck.baseline.statistical import profile_statistical
        stat_profiles = profile_statistical(df, semantic_types=semantic_types)

    # ------------------------------------------------------------------
    # 3. Constraints — pass date columns derived from semantic types
    # ------------------------------------------------------------------
    from goldencheck.baseline.models import FunctionalDependency, TemporalOrder
    constraints_fd: list[FunctionalDependency] = []
    constraints_keys: list[list[str]] = []
    constraints_temporal: list[TemporalOrder] = []
    if "constraints" not in skip_set:
        from goldencheck.baseline.constraints import mine_constraints
        date_columns = semantic_types.get("date", [])
        constraints_fd, constraints_keys, constraints_temporal = mine_constraints(
            df, date_columns=date_columns
        )

    # ------------------------------------------------------------------
    # 4. Correlations
    # ------------------------------------------------------------------
    from goldencheck.baseline.models import CorrelationEntry
    correlations: list[CorrelationEntry] = []
    if "correlation" not in skip_set:
        from goldencheck.baseline.correlation import analyze_correlations
        correlations = analyze_correlations(df)

    # ------------------------------------------------------------------
    # 5. Patterns
    # ------------------------------------------------------------------
    from goldencheck.baseline.models import PatternGrammar
    patterns_raw: dict[str, list[PatternGrammar]] = {}
    if "patterns" not in skip_set:
        from goldencheck.baseline.patterns import induce_patterns
        patterns_raw = induce_patterns(df)

    # Flatten: keep only the top grammar per column (highest coverage).
    patterns: dict[str, PatternGrammar] = {
        col: grammars[0] for col, grammars in patterns_raw.items() if grammars
    }

    # ------------------------------------------------------------------
    # 6. Priors — requires a file path (scan_file needs a path)
    # ------------------------------------------------------------------
    from goldencheck.baseline.models import ConfidencePrior
    priors_flat: dict[str, ConfidencePrior] = {}
    if "priors" not in skip_set and path is not None:
        from goldencheck.engine.scanner import scan_file
        from goldencheck.baseline.priors import build_priors
        findings, _profile = scan_file(path)
        nested_priors = build_priors(findings, row_count=df.height)
        # Flatten nested {check: {col: prior}} → {"check:col": prior}
        for check, col_map in nested_priors.items():
            for col, prior in col_map.items():
                priors_flat[f"{check}:{col}"] = prior

    # ------------------------------------------------------------------
    # Assemble BaselineProfile
    # ------------------------------------------------------------------
    return BaselineProfile(
        source=resolved_source,
        rows=df.height,
        columns=df.columns,
        stat_profiles=stat_profiles,
        constraints_fd=constraints_fd,
        constraints_keys=constraints_keys,
        constraints_temporal=constraints_temporal,
        correlations=correlations,
        patterns=patterns,
        semantic_types={
            col: type_name
            for type_name, cols in semantic_types.items()
            for col in cols
        },
        priors=priors_flat,
    )


def load_baseline(path: Union[Path, str]) -> BaselineProfile:
    """Load a :class:`~goldencheck.baseline.models.BaselineProfile` from YAML.

    Parameters
    ----------
    path:
        Path to a ``.yml`` / ``.yaml`` baseline profile previously saved with
        :meth:`~goldencheck.baseline.models.BaselineProfile.save`.

    Returns
    -------
    BaselineProfile
        The deserialised profile.
    """
    return BaselineProfile.load(Path(path))
